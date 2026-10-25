#!/usr/bin/env python3
"""Claude Code hooks for the workflow. Standard library only (Python 3.9+).

  flow_hook.py pre-edit       PreToolUse on Edit|Write|MultiEdit|NotebookEdit: spec gate and file claims
  flow_hook.py pre-bash       PreToolUse on Bash: destructive git commands, shell writes to the config
  flow_hook.py stop           Stop: the item's checks (tests, security) before Claude finishes
  flow_hook.py session-start  SessionStart (startup, resume, clear, compact): put the item's state back

Does nothing in repositories without .flow/config.json. With "mode": "warn" problems are
reported but nothing is blocked; with "mode": "enforce" they block. The claim check covers
Claude's edit tools; files written by shell commands are caught in review (scope check).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import flow_lib as lib  # noqa: E402

PROTECTED = (".flow/config.json",)          # agents may not switch enforcement off
STOP_BUDGET_SECONDS = 870                   # stays under the 900 s hook timeout in hooks.json
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


_OUT = {}


def emit(obj):
    """Collect hook output; main() writes it once as a single JSON object."""
    for key, value in obj.items():
        if key == "systemMessage" and key in _OUT:
            _OUT[key] += " | " + value
        elif key == "hookSpecificOutput" and key in _OUT:
            old = _OUT[key].get("additionalContext")
            _OUT[key].update(value)
            if old and value.get("additionalContext"):
                _OUT[key]["additionalContext"] = old + "\n" + value["additionalContext"]
        else:
            _OUT[key] = value


def problem(cfg, event, reason):
    """Block in enforce mode; in warn mode tell Claude and the user, and let the action run."""
    if cfg.get("mode") == "enforce":
        sys.stderr.write(f"[flow] {reason}\n")
        return 2
    emit({"systemMessage": f"[flow warn] {reason}",
          "hookSpecificOutput": {"hookEventName": event,
                                 "additionalContext": f"Workflow warning (warn mode, not blocked): {reason}"}})
    return 0


# ---------------------------------------------------------------- PreToolUse: edits

def pre_edit(data, root, cfg):
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    rel = lib.rel_path(path, root) if path else None
    if rel is None:
        return 0
    if rel in PROTECTED and cfg.get("mode") == "enforce":
        sys.stderr.write(f"[flow] {rel} sets enforcement; the user changes it, not an agent.\n")
        return 2
    item = lib.current_item(root, cfg)
    if not item or item in rel:                       # planning on the base branch, or the item's own files
        return 0
    spec = lib.read_spec(root, item)
    if not spec or spec.get("status") not in ("approved", "in-progress", "done"):
        return problem(cfg, "PreToolUse", f"{item} has no approved spec at docs/items/{item}/spec.md. Run /flow:quiz first.")
    claims = lib.load_claims(root)
    mine = claims.get(item)
    if not mine:
        return problem(cfg, "PreToolUse",
                       f"{item} has no file claim. Run `python3 <plugin>/scripts/schedule.py claim {item}` "
                       f"(the /flow:build step does this).")
    tokens = lib.hot_tokens_for(rel, cfg)
    if tokens:
        missing = [t for t in tokens if t not in mine.get("hot", [])]
        if missing:
            return problem(cfg, "PreToolUse",
                           f"{rel} is a shared file (hot: {', '.join(missing)}) that {item} did not claim. "
                           f"Add it to docs/items/{item}/impact.json and refresh the claim, or wait for the item that holds it.")
    elif not any(lib.entry_matches(rel, e) for e in mine.get("paths", [])):
        return problem(cfg, "PreToolUse",
                       f"{rel} is outside {item}'s claim. If the item really needs it, stop: add it to "
                       f"docs/items/{item}/impact.json and refresh the claim (schedule.py claim {item}). "
                       f"Do not work around this check.")
    for other, claim in claims.items():
        if other != item and (any(lib.entry_matches(rel, e) for e in claim.get("paths", []))
                              or set(tokens) & set(claim.get("hot", []))):
            return problem(cfg, "PreToolUse", f"{rel} is also claimed by {other}. Wait for it to merge.")
    return 0


# ---------------------------------------------------------------- PreToolUse: shell

def split_commands(command):
    return [c for c in re.split(r"&&|\|\||;|\||\n", command) if c.strip()]


def git_subcommand(tokens):
    """Skip git's global options (-C dir, -c key=value, --git-dir=...) to find the subcommand."""
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        opt = tokens[i].split("=", 1)[0]
        i += 2 if (opt in GIT_OPTS_WITH_VALUE and "=" not in tokens[i]) else 1
    return (tokens[i], tokens[i + 1:]) if i < len(tokens) else (None, [])


def bash_problems(command, base, branch):
    found = []
    if any(p in command for p in PROTECTED) and re.search(r">|\bsed\s+-i|\btee\b|\bmv\b|\bcp\b|\brm\b|\bperl\s+-i|write_text|open\(", command):
        found.append(f"a shell command writes {', '.join(PROTECTED)}; the user changes that file")
    for part in split_commands(command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        while tokens and re.fullmatch(r"\w+=\S*", tokens[0]):      # leading VAR=value
            tokens = tokens[1:]
        if not tokens:
            continue
        if tokens[0] == "git" and len(tokens) > 1:
            sub, rest = git_subcommand(tokens)
            if sub is None:
                continue
            if "--no-verify" in rest:
                found.append("--no-verify skips the git hooks")
            if sub == "push":
                if "--force" in rest or "-f" in rest or any(t.startswith("+") for t in rest):
                    found.append("force push (use --force-with-lease on the item branch)")
                refs = [t for t in rest if not t.startswith("-")][1:]
                targets = [r.split(":")[-1].replace("refs/heads/", "", 1) for r in refs]
                if base in targets or (not refs and branch == base):
                    found.append(f"push to {base}; merge through a pull request or let the user push")
            elif sub == "reset" and "--hard" in rest:
                found.append("git reset --hard throws away work")
            elif sub == "clean" and any(t.startswith("-") and "f" in t for t in rest):
                found.append("git clean -f deletes untracked files")
            elif sub in ("checkout", "restore") and rest and rest[-1] == ".":
                found.append(f"git {sub} . discards all local changes")
            elif sub == "branch" and "-D" in rest:
                found.append("git branch -D deletes unmerged work")
        elif tokens[0] == "rm":
            flags = "".join(t for t in tokens[1:] if t.startswith("-"))
            targets = [t for t in tokens[1:] if not t.startswith("-")]
            if "r" in flags.lower() and any(t in ("/", "~", ".", "..", "*", "/*", "~/") for t in targets):
                found.append(f"recursive delete of {' '.join(targets)}")
    return found


def pre_bash(data, root, cfg):
    command = (data.get("tool_input") or {}).get("command") or ""
    found = bash_problems(command, cfg.get("base_branch", "main"), lib.current_branch(root))
    if not found:
        return 0
    return problem(cfg, "PreToolUse", "; ".join(found) + ". Ask the user to run it if it is really needed.")


# ---------------------------------------------------------------- Stop: item checks

def fingerprint(root):
    """Changes since the last commit, including the contents of untracked files."""
    h = hashlib.sha256()
    for part in (lib.git(root, "rev-parse", "HEAD") or "", lib.git(root, "diff", "HEAD") or ""):
        h.update(part.encode())
    for name in sorted((lib.git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines()):
        h.update(name.encode())
        try:
            path = Path(root) / name
            if path.is_file() and path.stat().st_size <= 5_000_000:
                h.update(path.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def tail(text, n):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def stop(data, root, cfg):
    if data.get("stop_hook_active"):
        return 0
    item = lib.current_item(root, cfg)
    if not item or item not in lib.load_claims(root):
        return 0
    state_dir = Path(root) / ".flow" / "state"
    state_file = state_dir / f"{item}.json"
    fp = fingerprint(root)
    try:
        state = json.loads(state_file.read_text("utf-8")) if state_file.exists() else {}
    except json.JSONDecodeError:
        state = {}
    if state.get("green") == fp:                      # nothing changed since the last green run
        return 0
    _, budget = lib.item_budget(root, cfg, item)
    checks = budget.get("stop_checks") if budget.get("stop_checks") is not None else cfg.get("stop_checks")
    quoted = shlex.quote(item)
    commands = [c.replace("{item}", quoted) for c in checks or []]
    if (Path(root) / "security" / "checks.py").exists():
        commands.append(f"uv run -q security/checks.py run --stage S6 --item {quoted} --missing-ok")
    failures, missing = [], []
    deadline = time.monotonic() + STOP_BUDGET_SECONDS
    for cmd in commands:
        left = deadline - time.monotonic()
        if left < 5:
            missing.append(f"{cmd} (no time left)")
            continue
        try:
            p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=left)
            if p.returncode == 127:                   # the tool is not installed: warn, do not block
                missing.append(cmd)
            elif p.returncode != 0:
                failures.append(f"$ {cmd}\n{tail(p.stdout + chr(10) + p.stderr, 25)}")
        except subprocess.TimeoutExpired:
            failures.append(f"$ {cmd}\ntimed out")
    state_dir.mkdir(parents=True, exist_ok=True)
    state.update({"last_run": dt.datetime.now().isoformat(timespec="seconds"),
                  "last_result": "fail" if failures else "pass",
                  "last_failures": [f.splitlines()[0] for f in failures],
                  "not_run": missing})
    if not failures:
        state["green"] = fp
    state_file.write_text(json.dumps(state, indent=2), "utf-8")
    note = ("Not run (tool missing or out of time): " + "; ".join(missing)) if missing else ""
    if not failures:
        if note:
            emit({"systemMessage": f"[flow warn] {note}"})
        return 0
    reason = (f"{item} checks failed. Fix them before finishing (output trimmed):\n\n"
              + "\n\n".join(failures) + (f"\n\n{note}" if note else ""))[:6000]
    if cfg.get("mode") == "enforce":
        emit({"decision": "block", "reason": reason})
    else:
        emit({"systemMessage": f"[flow warn] {item} checks failed: "
                               + ", ".join(f.splitlines()[0][2:] for f in failures)})
    return 0


# ---------------------------------------------------------------- session start: restore the item's state

def snapshot(root, cfg, item):
    spec = lib.read_spec(root, item) or {}
    claim = lib.load_claims(root).get(item) or {}
    base = cfg.get("base_branch", "main")
    stat = lib.git(root, "diff", "--stat", f"{base}...HEAD") or lib.git(root, "diff", "--stat") or ""
    state_file = Path(root) / ".flow" / "state" / f"{item}.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    level, budget = lib.item_budget(root, cfg, item)
    agents = ", ".join(f"{k} {v or 'inline'}" for k, v in (budget.get("agents") or {}).items())
    lines = [f"Active item {item}: {spec.get('title', '')}",
             f"Stage {lib.item_stage(root, item)}, tier {spec.get('tier', '?')}, budget {level}"
             + (f" (agents: {agents}; reading: {budget.get('read_limit')}; handoff at {budget.get('handoff_at')}% context)"
                if budget else ""),
             f"Claim: {len(claim.get('paths', []))} paths, hot: {', '.join(claim.get('hot', [])) or 'none'}",
             f"Last checks: {state.get('last_result', 'not run')} {', '.join(state.get('last_failures', []))}".rstrip()]
    if stat:
        lines += ["Changes since base:", tail(stat, 12)]
    return "\n".join(lines)


def session_start(data, root, cfg):
    parts = []
    if cfg.get("_error"):
        parts.append(f"Workflow config problem: {cfg['_error']}")
    item = lib.current_item(root, cfg)
    if item:
        parts.append(snapshot(root, cfg, item))
        handoff = lib.item_dir(root, item) / "handoff.md"
        if handoff.exists():
            text = handoff.read_text("utf-8", "ignore").strip().splitlines()
            parts.append("Handoff (docs/items/%s/handoff.md):\n%s" % (item, "\n".join(text[:60])))
        else:
            parts.append(f"No handoff yet; read docs/items/{item}/spec.md for the decisions.")
    if parts:
        emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n\n".join(parts)}})
    return 0


def main(argv):
    event = argv[1] if len(argv) > 1 else ""
    handlers = {"pre-edit": pre_edit, "pre-bash": pre_bash, "stop": stop, "session-start": session_start}
    if event not in handlers:
        sys.stderr.write(f"flow_hook: unknown event {event!r}\n")
        return 1
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        data = {}
    start = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
    root = lib.repo_root(start)
    cfg = lib.load_config(root)
    if cfg is None:
        return 0
    if cfg.get("_error") and event in ("pre-edit", "pre-bash"):
        emit({"systemMessage": f"[flow warn] {cfg['_error']}"})
    code = handlers[event](data, root, cfg)
    if _OUT:
        sys.stdout.write(json.dumps(_OUT))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
