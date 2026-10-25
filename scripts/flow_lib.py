"""Shared helpers for the workflow scripts and hooks. Standard library only (Python 3.9+)."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

CONFIG_REL = ".flow/config.json"
DEFAULT_CONFIG = {
    "mode": "warn",                 # warn: hooks report problems; enforce: hooks block them
    "base_branch": "main",
    "wip_limit": 2,                 # items in progress at the same time
    "item_pattern": r"ITEM-\d+",
    "hot_files": {},                # token -> files many items touch, e.g. {"bus-registry": ["src/bus/dispatch.ts"]}
    "domains": {},                  # domain -> folder, for the coupling scan
    "shared": [],                   # folders every domain may import (bus, helpers, ui kit)
    "aliases": {"@/": "src/"},      # import alias -> folder
    "stop_checks": [],              # commands the Stop hook runs when the budget has none; {item} is replaced
    "budget_default": "standard",
    "budgets": {},                  # lean | standard | deep settings, see templates/config.json
}
STAGES = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "done"]
GLOB_CHARS = "*?{"                   # not "[": Next.js route folders like app/notes/[id]/ are plain paths


def git(cwd, *args):
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def repo_root(start=None):
    out = git(start or ".", "rev-parse", "--show-toplevel")
    return Path(out) if out else None


def load_config(root):
    """The merged config, or None when the repo has not opted in (no .flow/config.json).

    A config that is not valid JSON falls back to the defaults (warn mode) and carries an
    "_error" key so the hooks can report it instead of failing silently.
    """
    if root is None:
        return None
    path = Path(root) / CONFIG_REL
    if not path.exists():
        return None
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("top level is not an object")
        cfg.update(data)
    except (json.JSONDecodeError, ValueError) as e:
        cfg["_error"] = f"{CONFIG_REL} is not valid JSON ({e}); using defaults (warn mode) until it is fixed"
    return cfg


def current_branch(root):
    return git(root, "branch", "--show-current") or git(root, "rev-parse", "--abbrev-ref", "HEAD") or ""


def current_item(root, cfg):
    m = re.search(cfg.get("item_pattern", DEFAULT_CONFIG["item_pattern"]), current_branch(root))
    return m.group(0) if m else None


def item_dir(root, item):
    return Path(root) / "docs" / "items" / item


def claims_dir(root):
    common = git(root, "rev-parse", "--git-common-dir") or ".git"
    base = Path(common)
    if not base.is_absolute():
        base = Path(root) / base
    return base / "flow-claims"


def load_claims(root):
    claims = {}
    d = claims_dir(root)
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                claims[f.stem] = json.loads(f.read_text("utf-8"))
            except json.JSONDecodeError:
                continue
    return claims


def rel_path(path, root):
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    try:
        return p.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None


def glob_re(glob):
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


def static_prefix(entry):
    """The folder part of a claim entry before any glob character."""
    parts = []
    for seg in entry.strip().rstrip("/").split("/"):
        if any(ch in seg for ch in GLOB_CHARS):
            break
        parts.append(seg)
    return "/".join(parts)


def is_glob(entry):
    return any(ch in entry for ch in GLOB_CHARS)


def entry_matches(path, entry):
    """A claim entry is a file, a folder ending in '/', or a glob."""
    entry = entry.strip()
    if is_glob(entry):
        return bool(glob_re(entry).match(path))
    if entry.endswith("/"):
        return path.startswith(entry)
    return path == entry or path.startswith(entry + "/")


def entries_overlap(a, b):
    """Conservative: two entries overlap when one could contain a file the other covers."""
    if not is_glob(a) and not is_glob(b):
        a, b = a.rstrip("/"), b.rstrip("/")
        return a == b or a.startswith(b + "/") or b.startswith(a + "/")
    if is_glob(a) and not is_glob(b) and entry_matches(b.rstrip("/"), a):
        return True
    if is_glob(b) and not is_glob(a) and entry_matches(a.rstrip("/"), b):
        return True
    pa, pb = static_prefix(a), static_prefix(b)
    if not pa or not pb:
        return True                  # a glob anchored at the repo root may touch anything
    return pa == pb or pa.startswith(pb + "/") or pb.startswith(pa + "/")


def claim_from_impact(impact):
    """Normalize impact.json into {'paths': [...], 'hot': [...]} (accepts 'hot:x' entries in paths)."""
    paths, hot = [], set(impact.get("hot") or [])
    for key in ("paths", "new_files"):
        for p in impact.get(key) or []:
            if not isinstance(p, str) or not p.strip():
                continue
            if p.startswith("hot:"):
                hot.add(p[4:])
            else:
                paths.append(p[2:] if p.startswith("./") else p)
    return {"paths": sorted(set(paths)), "hot": sorted(hot)}


def claim_conflicts(a, b):
    found = [f"hot:{t}" for t in sorted(set(a.get("hot", [])) & set(b.get("hot", [])))]
    for pa in a.get("paths", []):
        for pb in b.get("paths", []):
            if entries_overlap(pa, pb):
                found.append(f"{pa} <> {pb}")
    return found


def hot_tokens_for(path, cfg):
    return [tok for tok, files in (cfg.get("hot_files") or {}).items()
            if any(entry_matches(path, f) for f in files)]


def parse_frontmatter(text):
    """Tiny YAML-frontmatter reader: key: value, and key: [a, b]."""
    out = {}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    for line in text[3:end].splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        value = value.split(" #", 1)[0].strip()
        if value.startswith("[") and value.endswith("]"):
            out[key.strip()] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            out[key.strip()] = value.strip("'\"")
    return out


def read_spec(root, item):
    path = item_dir(root, item) / "spec.md"
    if not path.exists():
        return None
    return parse_frontmatter(path.read_text("utf-8", "ignore"))


def item_budget(root, cfg, item):
    """(level, settings) for the item; settings is {} when the config has no budgets."""
    spec = read_spec(root, item) or {}
    level = spec.get("budget") or cfg.get("budget_default") or "standard"
    return level, (cfg.get("budgets") or {}).get(level) or {}


def item_stage(root, item):
    """Best guess of the item's stage from the files it has."""
    d = item_dir(root, item)
    spec = read_spec(root, item)
    if not spec or spec.get("status", "draft") == "draft":
        return "S1"
    if spec.get("status") == "done":
        return "done"
    tier = spec.get("tier", "M")
    if tier != "S" and not (d / "uml").exists():
        return "S2"
    if not (d / "impact.json").exists():
        return "S3"
    review = d / "review.md"
    if review.exists():
        text = review.read_text("utf-8", "ignore")
        return "S8" if re.search(r"(?im)^\W*(verdict|result)?\W*PASS\b", text) else "S5"
    tests = [p for p in Path(root).glob("tests/**/*") if item in p.name]
    tests += [p for p in Path(root).glob("specs/*") if item in p.name]
    return "S5" if tests else "S4"


def as_list(x):
    if not x:
        return []
    return x if isinstance(x, list) else [x]


def all_items(root):
    items = []
    base = Path(root) / "docs" / "items"
    if not base.exists():
        return items
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        spec = read_spec(root, d.name) or {}
        impact, impact_error = None, None
        if (d / "impact.json").exists():
            try:
                impact = json.loads((d / "impact.json").read_text("utf-8"))
            except json.JSONDecodeError as e:
                impact_error = f"impact.json is not valid JSON ({e})"
        items.append({
            "id": d.name,
            "status": spec.get("status", "draft"),
            "tier": spec.get("tier", "M"),
            "budget": spec.get("budget", ""),
            "deps": as_list(spec.get("deps")),
            "claim": claim_from_impact(impact) if isinstance(impact, dict) else None,
            "error": impact_error,
        })
    return items
