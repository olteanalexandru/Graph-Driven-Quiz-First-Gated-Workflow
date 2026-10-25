#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Pick and run the vulnerability checks in security/checks.yaml.

  uv run security/checks.py detect [--write]        stack tags from manifests and code
  uv run security/checks.py list [--stage S] [--env E] [--md] [--all]
  uv run security/checks.py item ITEM-042 [--write] checks for one work item, by stage
  uv run security/checks.py run --stage S6 [--item ITEM-042] [--only ID ...] [--strict] [--missing-ok]
  uv run security/checks.py floors                  version floors only
  uv run security/checks.py gate ITEM-042           fail while blocker/high boxes are unticked
  uv run security/checks.py validate                check the registry itself

Exit codes: 0 ok, 1 a blocker or high check failed, 2 usage or registry error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is missing. Run the script with: uv run security/checks.py ...")

STAGES = ["S1", "S2", "S4", "S6", "S7", "S8", "weekly"]
ITEM_STAGES = ["S1", "S2", "S4", "S6", "S7"]
TRIGGERED = {"S4", "S6", "S7"}          # stages where paths/content triggers apply
ENVS = ["dev", "ci", "preview", "prod"]
SEVERITIES = ["blocker", "high", "medium"]
GATING = {"blocker", "high"}
SKIP_DIRS = {
    ".git", "node_modules", ".next", "target", "dist", "build", "out", "coverage",
    ".turbo", ".venv", "venv", "__pycache__", "playwright-report", "test-results",
    "graphify-out", ".svelte-kit", ".output", ".cache", ".pnpm-store", "vendor",
    ".gradle", ".idea",
}
SOURCE_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".vue", ".svelte"}
MAX_BYTES = 8_000_000
META_PATHS = ("security/", "docs/items/")   # workflow files never trigger checks
STAGE_TITLES = {
    "S1": "S1 Quiz: ask the questions that apply, record each answer as a decision (D#)",
    "S2": "S2 Design: show it in the UML and data-structure notes",
    "S4": "S4 Acceptance specs: write or extend a test tagged @security",
    "S6": "S6 Test gate: automated",
    "S7": "S7 Review",
    "S8": "S8 Merge / release",
    "weekly": "Weekly",
}
VER_RX = re.compile(r"(\d+)\.(\d+)\.(\d+)")
VAR_RX = re.compile(r"\{\{(\w+)\}\}")


# ---------------------------------------------------------------- helpers

def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def glob_re(glob: str) -> re.Pattern:
    """Glob to regex: ** spans folders, * and ? stay inside one, {a,b} alternates."""
    out, i, n = [], 0, len(glob)
    while i < n:
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{" and "}" in glob[i:]:
            j = glob.index("}", i)
            out.append("(?:" + "|".join(re.escape(a) for a in glob[i + 1:j].split(",")) + ")")
            i = j + 1
            continue
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def static_prefix(glob: str) -> str:
    parts = []
    for seg in glob.split("/"):
        if any(ch in seg for ch in "*?[{"):
            break
        parts.append(seg)
    return "/".join(parts)


class MissingVar(Exception):
    pass


def subst(text: str, vars_: dict) -> str:
    def rep(m):
        value = vars_.get(m.group(1))
        if value in (None, ""):
            raise MissingVar(m.group(1))
        return str(value)
    return VAR_RX.sub(rep, text)


def vtuple(s):
    m = VER_RX.search(str(s))
    return tuple(int(x) for x in m.groups()) if m else None


def short(s: str, n: int = 70) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


def stages_of(c) -> list:
    return [str(s) for s in as_list(c.get("stage"))]


def method_of(c) -> str:
    if "grep" in c:
        return "grep"
    if "run" in c:
        return "run"
    if c.get("builtin") == "floors":
        return "floors"
    if "mcp" in c:
        return "mcp"
    return "manual"


def runnable(c) -> bool:
    return method_of(c) in ("grep", "run", "floors")


STAGE_MARK = re.compile(r"(?:^|\s)(S1|S2|S4|S6|S7|S8)\s")


def stage_text(c, stage: str) -> str:
    """The part of `check` for this stage: text after 'S4 ' up to the next stage marker."""
    text = " ".join(str(c.get("check", "")).split())
    marks = list(STAGE_MARK.finditer(text))
    if not marks:
        return text
    lead = text[: marks[0].start()].strip()
    for i, m in enumerate(marks):
        if m.group(1) == stage:
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            part = text[m.end(): end].strip()
            part = part[:1].upper() + part[1:]
            return f"{lead} {part}".strip()
    return lead or text


def sev_key(c):
    return (SEVERITIES.index(c.get("severity", "medium")), c["id"])


# ---------------------------------------------------------------- repository access

class Repo:
    def __init__(self, root: Path):
        self.root = root
        self._all = None
        self._text = {}

    def _walk(self, base: Path, skip: bool):
        if base.is_file():
            yield base.relative_to(self.root).as_posix()
            return
        for dirpath, dirnames, filenames in os.walk(base):
            if skip:
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                yield Path(dirpath, name).relative_to(self.root).as_posix()

    def all_files(self) -> list:
        if self._all is None:
            self._all = sorted(self._walk(self.root, skip=True))
        return self._all

    def files(self, globs) -> list:
        found = set()
        for g in as_list(globs):
            rx = glob_re(g)
            prefix = static_prefix(g)
            if prefix and any(p in SKIP_DIRS for p in prefix.split("/")):
                base = self.root / prefix
                cands = list(self._walk(base, skip=False)) if base.exists() else []
            else:
                cands = self.all_files()
            found.update(f for f in cands if rx.match(f))
        return sorted(found)

    def text(self, f: str):
        if f not in self._text:
            p = self.root / f
            try:
                self._text[f] = None if p.stat().st_size > MAX_BYTES else p.read_text("utf-8", "ignore")
            except OSError:
                self._text[f] = None
        return self._text[f]

    def git(self, *args) -> str:
        try:
            p = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return p.stdout if p.returncode == 0 else ""


def find_root(arg) -> Path:
    if arg:
        return Path(arg).resolve()
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            return Path(p.stdout.strip())
    except OSError:
        pass
    return Path.cwd()


# ---------------------------------------------------------------- registry and stack

def load_registry(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"registry not found: {path}")
    reg = yaml.safe_load(path.read_text("utf-8")) or {}
    reg.setdefault("checks", [])
    return reg


def cargo_deps(data: dict) -> set:
    names = set()

    def take(table):
        for key, value in (table or {}).items():
            names.add(value.get("package", key) if isinstance(value, dict) else key)

    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        take(data.get(section))
    take((data.get("workspace") or {}).get("dependencies"))
    for target in (data.get("target") or {}).values():
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            take(target.get(section))
    return names


def derive(tags: set, det: dict) -> set:
    tags = set(tags)
    for tag, sources in (det.get("derive") or {}).items():
        if tags & set(sources):
            tags.add(tag)
    return tags


def detect(repo: Repo, reg: dict) -> set:
    det = reg.get("detect") or {}
    files = repo.all_files()
    tags = set()
    for g, tg in (det.get("files") or {}).items():
        rx = glob_re(g)
        if any(rx.match(f) for f in files):
            tags.update(tg)
    npm_map, cargo_map = det.get("npm") or {}, det.get("cargo") or {}
    for f in files:
        name = f.rsplit("/", 1)[-1]
        if name == "package.json":
            try:
                data = json.loads(repo.text(f) or "{}")
            except json.JSONDecodeError:
                continue
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                for dep in (data.get(section) or {}):
                    tags.update(npm_map.get(dep, []))
        elif name == "Cargo.toml":
            try:
                data = tomllib.loads(repo.text(f) or "")
            except tomllib.TOMLDecodeError:
                continue
            for dep in cargo_deps(data):
                tags.update(cargo_map.get(dep, []))
    pending = {tag: re.compile(rx, re.M) for tag, rx in (det.get("content") or {}).items()}
    for f in files:
        if not pending:
            break
        if Path(f).suffix not in SOURCE_EXT:
            continue
        text = repo.text(f)
        if not text:
            continue
        for tag, rx in list(pending.items()):
            if rx.search(text):
                tags.add(tag)
                del pending[tag]
    return derive(tags, det)


class Stack:
    def __init__(self, tags, envs, vars_, from_file):
        self.tags, self.envs, self.vars, self.from_file = tags, envs, vars_, from_file


def load_stack(repo: Repo, reg: dict, path: Path) -> Stack:
    vars_ = dict(reg.get("vars") or {})
    if path.exists():
        st = yaml.safe_load(path.read_text("utf-8")) or {}
        tags = set(as_list(st.get("tech"))) | set(as_list(st.get("deploy"))) | set(as_list(st.get("extra")))
        tags = derive(tags, reg.get("detect") or {}) - set(as_list(st.get("exclude")))
        vars_.update(st.get("vars") or {})
        return Stack(tags, set(as_list(st.get("envs")) or ENVS), vars_, True)
    return Stack(detect(repo, reg), set(ENVS), vars_, False)


def applies(c, stack: Stack) -> bool:
    need = set(as_list(c.get("tech"))) | set(as_list(c.get("deploy")))
    envs = set(as_list(c.get("env")) or ENVS)
    return need <= stack.tags and bool(envs & stack.envs)


# ---------------------------------------------------------------- item selection

def item_files(repo: Repo, item: str, base: str, extra) -> list:
    entries = list(extra or [])
    impact = repo.root / "docs" / "items" / item / "impact.json"
    if impact.exists():
        try:
            data = json.loads(impact.read_text("utf-8"))
        except json.JSONDecodeError:
            data = {}
        for key in ("paths", "files", "new_files"):
            entries += [p for p in as_list(data.get(key)) if isinstance(p, str)]
    files = set()
    for p in entries:
        p = p.strip()
        if not p or p.startswith("hot:"):
            continue
        if p.startswith("./"):
            p = p[2:]
        if p.endswith("/") or (repo.root / p).is_dir():
            prefix = p.rstrip("/") + "/"
            files.update(f for f in repo.all_files() if f.startswith(prefix))
        else:
            files.add(p)
    for args in (["diff", "--name-only", f"{base}...HEAD"], ["diff", "--name-only"],
                 ["diff", "--name-only", "--cached"], ["ls-files", "--others", "--exclude-standard"]):
        files.update(line for line in repo.git(*args).splitlines() if line)
    return sorted(f for f in files if not f.startswith(META_PATHS))


def triggered(c, repo: Repo, files: list, vars_: dict) -> bool:
    paths, content = as_list(c.get("paths")), c.get("content")
    if not paths and not content:
        return True
    cands = files
    if paths:
        rxs = [glob_re(g) for g in paths]
        cands = [f for f in cands if any(r.match(f) for r in rxs)]
    if content:
        rx = re.compile(subst(content, vars_), re.M)
        cands = [f for f in cands if (t := repo.text(f)) is not None and rx.search(t)]
    return bool(cands)


def resolve_base(repo: Repo, wanted: str) -> str:
    for cand in (wanted, "origin/main", "main", "origin/master", "master"):
        if cand and repo.git("rev-parse", "--verify", "--quiet", cand).strip():
            return cand
    return wanted


# ---------------------------------------------------------------- executing checks

def run_grep(repo: Repo, rule: dict, vars_: dict):
    globs = as_list(rule.get("files"))
    allow = [glob_re(g) for g in as_list(rule.get("allow"))]
    files = [f for f in repo.files(globs) if not any(a.match(f) for a in allow)]
    flags = re.M | (re.I if "i" in str(rule.get("flags", "")) else 0)

    def rx(key):
        return re.compile(subst(rule[key], vars_), flags) if key in rule else None

    if not files:
        if rule.get("missing") == "fail" or "present" in rule:
            return "FAIL", [f"no file matches {', '.join(globs)}"]
        return "SKIP", ["no matching files"]
    if "present" in rule:
        present = rx("present")
        if any(present.search(repo.text(f) or "") for f in files):
            return "PASS", []
        return "FAIL", [f"no file in {', '.join(globs)} contains /{short(rule['present'])}/"]
    when, unless, forbid, require = rx("when"), rx("unless"), rx("forbid"), rx("require")
    found, evaluated = [], 0
    for f in files:
        text = repo.text(f)
        if text is None or (unless and unless.search(text)) or (when and not when.search(text)):
            continue
        evaluated += 1
        if forbid:
            for m in forbid.finditer(text):
                hit = f"{f}:{text.count(chr(10), 0, m.start()) + 1}"
                if hit not in found:
                    found.append(hit)
        elif require and not require.search(text):
            found.append(f"{f}: missing /{short(require.pattern)}/")
    if found:
        return "FAIL", found
    if not evaluated:   # nothing this rule applies to: not a failure, even with --strict
        return "N/A", ["no file contains /" + short(when.pattern) + "/" if when else "no readable files"]
    return "PASS", []


def run_command(repo: Repo, cmd: str, vars_: dict, env_extra: dict, timeout: int):
    shell = shutil.which("bash") or "/bin/sh"
    try:
        p = subprocess.run(cmd, shell=True, executable=shell, cwd=repo.root, capture_output=True,
                           text=True, timeout=timeout, env={**os.environ, **env_extra})
    except subprocess.TimeoutExpired:
        return "ERROR", [f"timed out after {timeout}s"]
    lines = [ln for ln in (p.stdout + "\n" + p.stderr).splitlines() if ln.strip()]
    if p.returncode == 0:
        return "PASS", []
    if p.returncode == 127 or any("command not found" in ln or "no such command" in ln for ln in lines[-6:]):
        return "MISSING", ["tool not installed here: " + short(cmd)] + lines[-3:]
    return "FAIL", lines[-12:] or [f"exit code {p.returncode}"]


# ---------------------------------------------------------------- version floors

def split_pnpm_key(key: str):
    key = key.lstrip("/").split("(", 1)[0]
    at = key.find("@", 1)
    if at > 0:
        return key[:at], key[at + 1:]
    if "/" in key:
        name, _, ver = key.rpartition("/")
        return name, ver
    return key, ""


def npm_versions(repo: Repo, pkg: str) -> list:
    found = set()
    for lf in repo.files(["**/pnpm-lock.yaml"]):
        try:
            data = yaml.safe_load(repo.text(lf) or "") or {}
        except yaml.YAMLError:
            continue
        for section in ("packages", "snapshots"):
            for key in (data.get(section) or {}):
                name, ver = split_pnpm_key(str(key))
                if name == pkg and vtuple(ver):
                    found.add((lf, ver))
    for lf in repo.files(["**/package-lock.json"]):
        try:
            data = json.loads(repo.text(lf) or "{}")
        except json.JSONDecodeError:
            continue
        for key, val in (data.get("packages") or {}).items():
            if key.endswith("node_modules/" + pkg) and isinstance(val, dict) and val.get("version"):
                found.add((lf, val["version"]))
    for lf in repo.files(["**/yarn.lock"]):
        names = []
        for line in (repo.text(lf) or "").splitlines():
            if line and not line[0].isspace() and line.rstrip().endswith(":"):
                names = []
                for part in line.rstrip()[:-1].split(","):
                    part = part.strip().strip('"')
                    at = part.rfind("@")
                    names.append(part[:at] if at > 0 else part)
            elif line.strip().startswith("version") and pkg in names:
                m = VER_RX.search(line)
                if m:
                    found.add((lf, m.group(0)))
    for lf in repo.files(["**/bun.lock"]):
        for m in re.finditer(r'"(@?[^"@\s]+(?:/[^"@\s]+)?)@(\d+\.\d+\.\d+[^"]*)"', repo.text(lf) or ""):
            if m.group(1) == pkg:
                found.add((lf, m.group(2)))
    return sorted(found)


def cargo_versions(repo: Repo, pkg: str) -> list:
    found = set()
    for lf in repo.files(["**/Cargo.lock"]):
        try:
            data = tomllib.loads(repo.text(lf) or "")
        except tomllib.TOMLDecodeError:
            continue
        for p in data.get("package") or []:
            if p.get("name") == pkg and p.get("version"):
                found.add((lf, p["version"]))
    return sorted(found)


def runtime_versions(repo: Repo, fl: dict) -> list:
    found = []
    cmd = fl.get("command")
    if cmd and shutil.which(cmd.split()[0]):
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            m = VER_RX.search(p.stdout + p.stderr)
            if m:
                found.append(("this machine", m.group(0)))
        except subprocess.TimeoutExpired:
            pass
    if fl.get("runtime") == "node":
        for f in repo.files(["**/Dockerfile*"]):
            for m in re.finditer(r"(?im)^FROM\s+(?:--platform=\S+\s+)?node:(\d+\.\d+\.\d+)", repo.text(f) or ""):
                found.append((f, m.group(1)))
        for f in repo.files(["**/.nvmrc", "**/.node-version"]):
            m = VER_RX.search(repo.text(f) or "")
            if m:
                found.append((f, m.group(0)))
        for f in repo.files([".github/workflows/*.{yml,yaml}"]):
            for m in re.finditer(r"node-version:\s*[\x27\"]?(\d+\.\d+\.\d+)", repo.text(f) or ""):
                found.append((f, m.group(1)))
    return found


def floor_eval(version: str, minmap: dict):
    v = vtuple(version)
    if v is None:
        return "unknown", ""
    best = None
    for key, minimum in minmap.items():
        key = str(key)
        spec = () if key == "*" else tuple(int(x) for x in key.split("."))
        if v[: len(spec)] == spec and (best is None or len(spec) > len(best[0])):
            best = (spec, str(minimum))
    if best:
        return ("ok" if v >= vtuple(best[1]) else "below"), best[1]
    majors = [int(str(k).split(".")[0]) for k in minmap if str(k) != "*"]
    if majors and v[0] > max(majors):
        return "newer", ""
    return "unsupported", " or ".join(str(m) for m in minmap.values())


def run_floors(repo: Repo, reg: dict):
    lines, failed, checked = [], False, 0
    for fl in reg.get("floors") or []:
        if fl.get("ecosystem") == "npm":
            found = npm_versions(repo, fl["package"])
        elif fl.get("ecosystem") == "cargo":
            found = cargo_versions(repo, fl["package"])
        elif fl.get("runtime"):
            found = runtime_versions(repo, fl)
        else:
            found = []
        for where, ver in found:
            checked += 1
            status, need = floor_eval(ver, fl["min"])
            if status == "below":
                failed = True
                lines.append(f"{fl['id']} {ver} in {where}: below the floor {need} ({fl.get('note', '')})")
            elif status == "unsupported":
                failed = True
                lines.append(f"{fl['id']} {ver} in {where}: release line has no patched version, move to {need}")
            elif status == "newer":
                lines.append(f"{fl['id']} {ver} in {where}: newer line than the floors list, check the vendor page")
    if not checked:
        return "SKIP", ["no listed package or runtime found"]
    return ("FAIL" if failed else "PASS"), lines


def execute(c, repo: Repo, reg: dict, stack: Stack, env_extra: dict):
    try:
        kind = method_of(c)
        if kind == "grep":
            return run_grep(repo, c["grep"], stack.vars)
        if kind == "run":
            return run_command(repo, subst(c["run"], stack.vars), stack.vars, env_extra, int(c.get("timeout", 900)))
        if kind == "floors":
            return run_floors(repo, reg)
    except MissingVar as e:
        return "SKIP", [f"set vars.{e.args[0]} in security/stack.yaml"]
    return "SKIP", ["manual check"]


# ---------------------------------------------------------------- commands

def cmd_detect(a, repo, reg, stack_path):
    tags = detect(repo, reg)
    deploy_tags = set((reg.get("detect") or {}).get("deploy_tags") or [])
    deploy, tech = sorted(tags & deploy_tags), sorted(tags - deploy_tags)
    print("tech:  ", ", ".join(tech) or "-")
    print("deploy:", ", ".join(deploy) or "-")
    if a.write:
        old = yaml.safe_load(stack_path.read_text("utf-8")) if stack_path.exists() else {}
        old = old or {}
        out = {
            "detected": dt.date.today().isoformat(),
            "tech": tech,
            "deploy": deploy,
            "envs": as_list(old.get("envs")) or ENVS,
            "extra": as_list(old.get("extra")),
            "exclude": as_list(old.get("exclude")),
            "vars": old.get("vars") or {},
        }
        stack_path.parent.mkdir(parents=True, exist_ok=True)
        header = ("# Written by `uv run security/checks.py detect --write`.\n"
                  "# tech and deploy are regenerated; edit envs, extra (tags detection cannot see,\n"
                  "# e.g. gamification, remote-dev), exclude and vars by hand.\n")
        stack_path.write_text(header + yaml.safe_dump(out, sort_keys=False, default_flow_style=None), "utf-8")
        print(f"wrote {stack_path.relative_to(repo.root)}")
    return 0


def cmd_list(a, repo, reg, stack):
    checks = [c for c in reg["checks"] if a.all or applies(c, stack)]
    if a.stage:
        checks = [c for c in checks if a.stage in stages_of(c)]
    if a.env:
        checks = [c for c in checks if a.env in (as_list(c.get("env")) or ENVS)]
    checks.sort(key=sev_key)
    if a.md:
        print("| ID | Check | Severity | Stages | Env | How |")
        print("|---|---|---|---|---|---|")
        for c in checks:
            env = ", ".join(as_list(c.get("env")) or ENVS)
            print(f"| {c['id']} | {c['title']} | {c['severity']} | {', '.join(stages_of(c))} | {env} | {method_of(c)} |")
    else:
        for c in checks:
            print(f"{c['id']:<10} {c['severity']:<8} {','.join(stages_of(c)):<18} {method_of(c):<7} {c['title']}")
    print(f"\n{len(checks)} checks" + ("" if a.all else f" for stack: {', '.join(sorted(stack.tags))}"), file=sys.stderr)
    return 0


def render_item(item, groups, stack, files, base):
    out = [f"# {item} security checks", "",
           f"Generated {dt.date.today().isoformat()} by security/checks.py for stack: {', '.join(sorted(stack.tags))}.",
           f"Files considered: {len(files)} (impact.json, plus changes since {base}). "
           "Tick a box when it is done; for a high check you skip, tick it and write the exception after it.", ""]
    for stage in ITEM_STAGES:
        checks = sorted(groups[stage], key=sev_key)
        if not checks:
            continue
        title = STAGE_TITLES[stage]
        if stage == "S6":
            title += f" (`uv run security/checks.py run --stage S6 --item {item}`)"
        out += [f"## {title}", ""]
        for c in checks:
            if stage == "S6" and runnable(c):
                out.append(f"- {c['severity']} {c['id']} ({method_of(c)}) {c['title']}")
            else:
                text = stage_text(c, stage)
                mcp = f" Use: {c['mcp']}." if c.get("mcp") else ""
                out.append(f"- [ ] **{c['severity']}** {c['id']} {c['title']}. {text}{mcp}")
        out.append("")
    return "\n".join(out)


def cmd_item(a, repo, reg, stack):
    base = resolve_base(repo, stack.vars.get("base_ref", "origin/main"))
    files = item_files(repo, a.item, base, a.paths)
    groups = {s: [] for s in ITEM_STAGES}
    for c in reg["checks"]:
        if not applies(c, stack):
            continue
        for s in stages_of(c):
            if s in groups and (s not in TRIGGERED or triggered(c, repo, files, stack.vars)):
                groups[s].append(c)
    md = render_item(a.item, groups, stack, files, base)
    if a.write:
        path = repo.root / "docs" / "items" / a.item / "security.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md + "\n", "utf-8")
        print(f"wrote {path.relative_to(repo.root)}")
    else:
        print(md)
    return 0


def cmd_run(a, repo, reg, stack):
    base = resolve_base(repo, stack.vars.get("base_ref", "origin/main"))
    stack.vars = {**stack.vars, "base_ref": base}
    files = item_files(repo, a.item, base, None) if a.item else None
    selected, manual = [], []
    for c in sorted(reg["checks"], key=sev_key):
        if not applies(c, stack) or a.stage not in stages_of(c):
            continue
        if a.only and c["id"] not in a.only:
            continue
        if files is not None and a.stage in TRIGGERED and not triggered(c, repo, files, stack.vars):
            continue
        (selected if runnable(c) else manual).append(c)
    env_extra = {"BASE_REF": base, "ITEM": a.item or ""}
    label = f"{a.stage}" + (f" for {a.item}" if a.item else "")
    print(f"{label}: {len(selected)} automated checks, stack: {', '.join(sorted(stack.tags))}")
    if not stack.from_file:
        print("(no security/stack.yaml; tags were detected now; run `detect --write` to pin them)")
    counts, failing = {}, []
    for c in selected:
        status, details = execute(c, repo, reg, stack, env_extra)
        if status == "MISSING":
            status = "SKIP" if a.missing_ok else "ERROR"
        gating = c.get("severity") in GATING
        if status in ("FAIL", "ERROR") and gating:
            failing.append(c["id"])
        elif status == "SKIP" and gating and a.strict:
            failing.append(c["id"])
        shown = "WARN" if status in ("FAIL", "ERROR") and not gating else status
        counts[shown] = counts.get(shown, 0) + 1
        print(f"{shown:<6} {c['severity']:<8} {c['id']:<10} {c['title']}")
        for d in details[:12]:
            print(f"         {d}")
        if len(details) > 12:
            print(f"         ... {len(details) - 12} more")
    if manual:
        print(f"\nManual for {a.stage} ({len(manual)}):")
        for c in manual:
            mcp = f"  [{c['mcp']}]" if c.get("mcp") else ""
            print(f"  {c['severity']:<8} {c['id']:<10} {c['title']}{mcp}")
    summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items())) or "nothing to run"
    print(f"\n{summary}" + (f"; blocking: {', '.join(failing)}" if failing else ""))
    return 1 if failing else 0


def cmd_floors(a, repo, reg, stack):
    status, lines = run_floors(repo, reg)
    print(status)
    for ln in lines:
        print("  " + ln)
    return 1 if status == "FAIL" else 0


def cmd_gate(a, repo, reg, stack):
    path = repo.root / "docs" / "items" / a.item / "security.md"
    if not path.exists():
        print(f"{path.relative_to(repo.root)} not found; run: uv run security/checks.py item {a.item} --write")
        return 2
    open_items = [ln for ln in path.read_text("utf-8").splitlines()
                  if re.match(r"^- \[ \] \*\*(blocker|high)\*\*", ln)]
    for ln in open_items:
        print(short(ln[6:], 110))
    print(f"{len(open_items)} unticked blocker/high checks" if open_items else "all blocker/high checks ticked")
    return 1 if open_items else 0


def cmd_validate(a, repo, reg, stack):
    errors, ids = [], set()
    dummy = {k: "x" for k in VAR_RX.findall(yaml.safe_dump(reg))}
    dummy.update({k: v for k, v in (reg.get("vars") or {}).items() if v})

    def regex_ok(where, pattern):
        try:
            re.compile(subst(str(pattern), dummy), re.M)
        except (re.error, MissingVar) as e:
            errors.append(f"{where}: bad regex: {e}")

    for c in reg["checks"]:
        cid = c.get("id", "?")
        if cid in ids:
            errors.append(f"{cid}: duplicate id")
        ids.add(cid)
        for field in ("id", "title", "severity", "stage", "check"):
            if not c.get(field):
                errors.append(f"{cid}: missing {field}")
        if c.get("severity") not in SEVERITIES:
            errors.append(f"{cid}: severity must be one of {SEVERITIES}")
        for s in stages_of(c):
            if s not in STAGES:
                errors.append(f"{cid}: unknown stage {s}")
        for e in as_list(c.get("env")):
            if e not in ENVS:
                errors.append(f"{cid}: unknown env {e}")
        if sum(k in c for k in ("grep", "run", "builtin")) > 1:
            errors.append(f"{cid}: use only one of grep, run, builtin")
        if c.get("content"):
            regex_ok(f"{cid}.content", c["content"])
        rule = c.get("grep")
        if rule:
            kinds = [k for k in ("forbid", "require", "present") if k in rule]
            if len(kinds) != 1:
                errors.append(f"{cid}: grep needs exactly one of forbid, require, present")
            if not rule.get("files"):
                errors.append(f"{cid}: grep needs files")
            for key in ("forbid", "require", "present", "when", "unless"):
                if key in rule:
                    regex_ok(f"{cid}.grep.{key}", rule[key])
    for tag, rx in ((reg.get("detect") or {}).get("content") or {}).items():
        regex_ok(f"detect.content.{tag}", rx)
    for fl in reg.get("floors") or []:
        if not fl.get("id") or not isinstance(fl.get("min"), dict):
            errors.append(f"floor {fl.get('id', '?')}: needs id and a min mapping")
            continue
        if not (fl.get("ecosystem") in ("npm", "cargo") or fl.get("runtime")):
            errors.append(f"floor {fl['id']}: needs ecosystem npm|cargo or runtime")
        for key, value in fl["min"].items():
            if vtuple(value) is None or not re.fullmatch(r"\*|\d+(\.\d+)?", str(key)):
                errors.append(f"floor {fl['id']}: bad line {key!r}: {value!r}")
    for e in errors:
        print(e)
    print(f"{len(reg['checks'])} checks, {len(reg.get('floors') or [])} floors: "
          + ("OK" if not errors else f"{len(errors)} problems"))
    return 2 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="repository root (default: git top level or cwd)")
    ap.add_argument("--registry", help="checks.yaml path (default: next to this script, or $SECURITY_CHECKS)")
    ap.add_argument("--stack", help="stack.yaml path (default: <root>/security/stack.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("detect")
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("list")
    p.add_argument("--stage", choices=STAGES)
    p.add_argument("--env", choices=ENVS)
    p.add_argument("--md", action="store_true")
    p.add_argument("--all", action="store_true", help="ignore the stack, list the whole registry")
    p = sub.add_parser("item")
    p.add_argument("item")
    p.add_argument("--write", action="store_true")
    p.add_argument("--paths", nargs="*", help="extra touched paths")
    p = sub.add_parser("run")
    p.add_argument("--stage", required=True, choices=STAGES)
    p.add_argument("--item")
    p.add_argument("--only", nargs="*")
    p.add_argument("--strict", action="store_true", help="a skipped blocker/high check fails")
    p.add_argument("--missing-ok", action="store_true",
                   help="a check whose tool is not installed is skipped instead of failing (used by the Stop hook)")
    sub.add_parser("floors")
    p = sub.add_parser("gate")
    p.add_argument("item")
    sub.add_parser("validate")
    a = ap.parse_args(argv)

    root = find_root(a.root)
    registry = Path(a.registry or os.environ.get("SECURITY_CHECKS") or Path(__file__).with_name("checks.yaml"))
    reg = load_registry(registry)
    repo = Repo(root)
    stack_path = Path(a.stack) if a.stack else root / "security" / "stack.yaml"
    if a.cmd == "detect":
        return cmd_detect(a, repo, reg, stack_path)
    stack = load_stack(repo, reg, stack_path)
    handlers = {"list": cmd_list, "item": cmd_item, "run": cmd_run, "floors": cmd_floors,
                "gate": cmd_gate, "validate": cmd_validate}
    return handlers[a.cmd](a, repo, reg, stack)


if __name__ == "__main__":
    sys.exit(main())
