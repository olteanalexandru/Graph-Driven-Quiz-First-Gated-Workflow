"""Shared helpers for the workflow scripts and hooks. Standard library only (Python 3.9+)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shlex
import statistics
import subprocess
import time
from pathlib import Path

CONFIG_REL = ".flow/config.json"
DEFAULT_CONFIG = {
    "mode": "warn",                 # warn: hooks report problems; enforce: hooks block them
    "base_branch": "main",
    "wip_limit": 2,                 # items in progress at the same time (hard limit; claim --force to pass it)
    "item_pattern": r"ITEM-\d+",
    "hot_files": {},                # token -> files many items touch, e.g. {"bus-registry": ["src/bus/dispatch.ts"]}
    "domains": {},                  # domain -> folder, for the coupling scan and the scheduler
    "shared": [],                   # folders every domain may import (bus, helpers, ui kit)
    "aliases": {"@/": "src/"},      # import alias -> folder
    "stop_checks": [],              # commands the Stop hook runs when the budget has none; {item} is replaced
    "baseline_check": {},           # test-file glob -> command for the item's baseline tests; {files} is replaced
    "deep_clarification": False,    # /flow:quiz default: false = quick (documented assumptions), true = deep
    "design_approval": "deep",      # deep: approval before code for deep-clarified items | always | never
    "split_at": {"nodes": 40, "domains": 3},
    "ux_audit": {"max_rounds": 3},
    "orchestration": {},            # global cost budget and scheduling limits, see templates/config.json
    "integration_checks": [],       # commands run on the merged result of items that finished together
    "budget_default": "standard",
    "budgets": {},                  # lean | standard | deep settings, see templates/config.json
}
STAGES = ["S1", "S2", "S3", "S4", "S5", "S6", "S6a", "S7", "S7a", "S8", "done"]
GLOB_CHARS = "*?{"                   # not "[": Next.js route folders like app/notes/[id]/ are plain paths
DESIGN_DOCS = ("data-structures.md", "messages.md", "performance.md", "baseline.md")
LOCK_NAME = "design.lock.json"
SAFE_PATH = re.compile(r"[\w./@+\-\[\]() ]+")       # test paths handed to a shell: no metacharacters
ESTIMATE_USD = {"lean": 2.0, "standard": 8.0, "deep": 25.0}   # until 3 shipped items per level give a median


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


def verdict_pass(text):
    """True when the first non-empty line is the verdict PASS (a PASS bullet further down does not count)."""
    first = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    return bool(re.match(r"(?i)^\W*(verdict|result)?\W*PASS\b", first))


def item_stage(root, item, cfg=None):
    """Best guess of the item's stage from the files it has."""
    cfg = cfg if cfg is not None else (load_config(root) or DEFAULT_CONFIG)
    d = item_dir(root, item)
    spec = read_spec(root, item)
    if not spec or spec.get("status", "draft") == "draft":
        return "S1"
    if spec.get("status") == "done":
        return "done"
    tier = spec.get("tier", "M")
    if tier != "S" and not (d / "uml").exists():
        return "S2"
    if tier != "S" and design_approval_required(cfg, spec) and design_state(root, item)["status"] != "approved":
        return "S2"
    if not (d / "impact.json").exists():
        return "S3"
    review = d / "review.md"
    if review.exists():
        if not verdict_pass(review.read_text("utf-8", "ignore")):
            return "S5"
        partners = integration_partners(root, cfg, item)
        if partners and not integration_covered(root, item, partners):
            return "S7a"
        return "S8"
    tests = [p for p in Path(root).glob("tests/**/*") if item in p.name]
    tests += [p for p in Path(root).glob("specs/*") if item in p.name]
    if not tests:
        return "S4"
    if audit_required(root, item):
        audit = read_audit(root, item)
        if audit.get("verdict") == "FAIL":
            return "S6a"
        if audit.get("verdict") in ("PASS", "ACCEPTED") and audit.get("phase") != "discovery":
            return "S7"
    return "S5"


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
        impact = impact if isinstance(impact, dict) else None
        items.append({
            "id": d.name,
            "status": spec.get("status", "draft"),
            "tier": spec.get("tier", "M"),
            "budget": spec.get("budget", ""),
            "deps": as_list(spec.get("deps")),
            "claim": claim_from_impact(impact) if impact else None,
            "contracts": contracts_of(impact),
            "nodes": sorted({n for n in (impact or {}).get("nodes") or [] if isinstance(n, str)}),
            "risk": str((impact or {}).get("risk") or "").lower(),
            "error": impact_error,
        })
    return items


# ---------------------------------------------------------------- markdown helpers

def md_section(text, title):
    """Body of the first '## <title>' section (title matched case-insensitively at the start)."""
    m = re.search(r"(?im)^##\s+" + re.escape(title) + r"[^\n]*\n", text or "")
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"(?m)^#{1,2}\s", rest)
    return rest[:nxt.start()] if nxt else rest


def md_tables(text):
    """Every markdown table in text as a list of row dicts keyed by lower-case header."""
    tables, block = [], []
    for line in (text or "").splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append(line.strip())
            continue
        if len(block) >= 2 and re.fullmatch(r"\|?[\s:|-]+\|?", block[1]):
            head = [c.strip().lower() for c in block[0].strip("|").split("|")]
            rows = []
            for row in block[2:]:
                cells = [c.strip() for c in row.strip("|").split("|")]
                rows.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(head)})
            tables.append(rows)
        block = []
    return tables


def first_col(row):
    return next(iter(row.values()), "") if row else ""


# ---------------------------------------------------------------- editable design (lock and approval)

def split_item_doc(rel):
    """(item, path inside the item folder) for docs/items/<ID>/..., else (None, None)."""
    m = re.match(r"docs/items/([^/]+)/(.+)$", rel or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def is_design_file(sub):
    return bool(sub) and (sub in DESIGN_DOCS or (sub.startswith("uml/") and not sub.endswith("/")))


def text_hash(path):
    """sha256 of a text file with line endings and trailing whitespace normalized."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n").rstrip()
    return hashlib.sha256(data).hexdigest()


def design_files(root, item):
    d = item_dir(root, item)
    found = [name for name in DESIGN_DOCS if (d / name).is_file()]
    if (d / "uml").is_dir():
        found += sorted(p.relative_to(d).as_posix() for p in (d / "uml").rglob("*") if p.is_file())
    return found


def load_lock(root, item):
    """The item's design lock. A broken lock carries "_error" and counts every file as edited by hand."""
    path = item_dir(root, item) / LOCK_NAME
    lock = {}
    if path.exists():
        try:
            lock = json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            lock = {"_error": f"{path.name} is not valid JSON"}
    if not isinstance(lock, dict):
        lock = {"_error": f"{path.name} is not a JSON object"}
    shapes = {"files": dict, "approved": (dict, type(None)), "unlocked": list}
    bad = [k for k, t in shapes.items() if k in lock and not isinstance(lock[k], t)]
    if bad or not all(isinstance(v, str) for v in (lock.get("files") or {}).values()):
        lock = {"_error": f"{path.name} has an unexpected shape ({', '.join(bad) or 'files'})"}
    if lock.get("approved") is not None and not isinstance(lock["approved"].get("files", {}), dict):
        lock = {"_error": f"{path.name} has an unexpected shape (approved.files)"}
    lock.setdefault("item", item)
    lock.setdefault("files", {})
    lock.setdefault("approved", None)
    lock.setdefault("unlocked", [])
    return lock


def shell_quote(text):
    """Quote one argument for the platform shell that subprocess(shell=True) runs (cmd.exe on Windows)."""
    if os.name != "nt":
        return shlex.quote(str(text))
    return '"' + re.sub(r'["%!^]', "", str(text)) + '"'     # inside quotes cmd.exe keeps & | < > literal


def write_json(path, data):
    """Write JSON through a temp file and a rename, so readers never see half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    os.replace(tmp, path)


def save_lock(root, item, lock):
    lock = {k: v for k, v in lock.items() if not k.startswith("_")}
    write_json(item_dir(root, item) / LOCK_NAME, lock)


def design_state(root, item):
    """Design status (none, draft, approved, changed), files edited by hand, files changed since approval."""
    lock = load_lock(root, item)
    d = item_dir(root, item)
    current = {f: text_hash(d / f) for f in design_files(root, item)}
    hand = sorted(f for f, h in current.items() if lock["files"].get(f) != h)
    approved = lock.get("approved") or None
    changed = []
    if approved:
        snap = approved.get("files") or {}
        changed = sorted({f for f in current if snap.get(f) != current[f]} | {f for f in snap if f not in current})
        status = "changed" if changed else "approved"
    else:
        status = "draft" if current else "none"
    return {"status": status, "hand_edited": hand, "changed": changed, "files": current,
            "approved_at": (approved or {}).get("at"), "unlocked": lock.get("unlocked") or [],
            "error": lock.get("_error")}


def record_design_write(root, item, sub):
    """After Claude wrote a design file: remember its hash; an approved design is amended, not reopened."""
    path = item_dir(root, item) / sub
    lock = load_lock(root, item)
    if not path.is_file():
        lock["files"].pop(sub, None)
        if lock.get("approved"):
            lock["approved"].get("files", {}).pop(sub, None)
    else:
        h = text_hash(path)
        lock["files"][sub] = h
        if lock.get("approved"):
            lock["approved"].setdefault("files", {})[sub] = h
            lock["approved"]["amended"] = now_iso()
    lock["unlocked"] = [f for f in lock.get("unlocked") or [] if f != sub]
    save_lock(root, item, lock)


def approve_design(root, item, note=""):
    lock = load_lock(root, item)
    state = design_state(root, item)
    lock["files"] = dict(state["files"])
    lock["approved"] = {"at": now_iso(), "files": dict(state["files"])}
    if note:
        lock["approved"]["note"] = note
    lock["unlocked"] = []
    save_lock(root, item, lock)
    return state


def unlock_design(root, item, subs):
    lock = load_lock(root, item)
    lock["unlocked"] = sorted(set(lock.get("unlocked") or []) | set(subs))
    save_lock(root, item, lock)


def design_approval_required(cfg, spec):
    mode = (cfg or {}).get("design_approval", "deep")
    if mode == "always":
        return True
    return mode == "deep" and (spec or {}).get("clarify") == "deep"


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- baseline and audit

def baseline_rows(root, item):
    """Rows of the feature table in docs/items/<ID>/baseline.md (rows whose first cell is F<n>)."""
    path = item_dir(root, item) / "baseline.md"
    if not path.exists():
        return []
    rows = []
    for table in md_tables(path.read_text("utf-8", "ignore")):
        rows += [r for r in table if re.fullmatch(r"F\d+", first_col(r))]
    return rows


def test_paths(cell):
    """Test files named in a baseline Tests cell: 'a.spec.ts:12, b.spec.ts > title' -> [a.spec.ts, b.spec.ts]."""
    out = []
    for part in re.split(r"[,;]", cell or ""):
        part = part.strip().strip("`")
        if not part or part.lower().startswith(("none", "-")):
            continue
        out.append(re.split(r"[:\s>]", part, maxsplit=1)[0])
    return out


def baseline_test_files(root, item):
    """Existing test files that guard the item's kept features, excluding files named with the item ID."""
    files = set()
    for row in baseline_rows(root, item):
        if not row.get("plan", "keep").lower().startswith("keep"):
            continue
        for p in test_paths(row.get("tests", "")):
            rel = rel_path(p, root)
            if rel and item not in rel and SAFE_PATH.fullmatch(rel) and (Path(root) / rel).is_file():
                files.add(rel)
    return sorted(files)


def read_audit(root, item):
    path = item_dir(root, item) / "audit.md"
    return parse_frontmatter(path.read_text("utf-8", "ignore")) if path.exists() else {}


def audit_required(root, item):
    d = item_dir(root, item)
    return (d / "baseline.md").exists() or (d / "audit.md").exists()


# ---------------------------------------------------------------- contracts and parallel work

def contracts_of(impact):
    c = (impact or {}).get("contracts") or {}
    if not isinstance(c, dict):
        return {"changes": [], "uses": []}
    return {k: sorted({x for x in as_list(c.get(k)) if isinstance(x, str) and x.strip()}) for k in ("changes", "uses")}


def contract_match(a, b):
    """api:/progress matches api:/progress; bus:progress/* matches bus:progress/awardXp."""
    if a == b:
        return True
    if a.endswith("*") and b.startswith(a[:-1]):
        return True
    return b.endswith("*") and a.startswith(b[:-1])


def contract_conflicts(a, b):
    """A contract one item changes and the other changes or uses."""
    found = []
    for mine, theirs in ((a, b), (b, a)):
        for c in mine.get("changes", []):
            for o in theirs.get("changes", []) + theirs.get("uses", []):
                if contract_match(c, o):
                    found.append(f"contract {c}" if c == o else f"contract {c} ~ {o}")
    return sorted(set(found))


def item_domains(claim, cfg):
    out = set()
    for name, folder in (cfg.get("domains") or {}).items():
        folder = folder.rstrip("/")
        if any(entries_overlap(p, folder + "/") or p.rstrip("/") == folder for p in (claim or {}).get("paths", [])):
            out.add(name)
    return out


def soft_blockers(a, b, cfg):
    """Reasons to run two otherwise independent items one after the other."""
    orch = cfg.get("orchestration") or {}
    limit = float(orch.get("shared_context_limit", 0.5))
    found = []
    na, nb = set(a.get("nodes") or []), set(b.get("nodes") or [])
    small = min(len(na), len(nb))
    if small and len(na & nb) / small >= limit:
        found.append(f"shares {len(na & nb)} of {small} graph nodes (the same context would be read twice)")
    if a.get("risk") == "high" and b.get("risk") == "high":
        common = item_domains(a.get("claim"), cfg) & item_domains(b.get("claim"), cfg)
        if common:
            found.append(f"both high rework risk in {', '.join(sorted(common))}")
    return found


def integration_partners(root, cfg, item):
    """Other claimed items whose branch has a passing review: they are checked together with this one."""
    partners = []
    for other in load_claims(root):
        if other == item:
            continue
        text = git(root, "show", f"{other}:docs/items/{other}/review.md")
        if text and verdict_pass(text):
            partners.append(other)
    return sorted(partners)


def integration_runs(root):
    d = common_dir(root) / "flow-integration"
    runs = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            run = json.loads(f.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(run, dict):
            runs.append(run)
    return runs


def branch_head(root, item):
    return git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{item}")


def integration_covered(root, item, partners=()):
    """The PASS run that tested the item's current head together with every partner's current head, or None."""
    heads = {i: branch_head(root, i) for i in [item, *partners]}
    if not all(heads.values()):
        return None
    for run in reversed(integration_runs(root)):
        tested = run.get("items") or {}
        if run.get("verdict") == "PASS" and all(tested.get(i) == h for i, h in heads.items()):
            return run
    return None


# ---------------------------------------------------------------- usage ledger and estimates

def common_dir(root):
    common = git(root, "rev-parse", "--git-common-dir") or ".git"
    base = Path(common)
    return base if base.is_absolute() else Path(root) / base


def usage_records(root):
    d = common_dir(root) / "flow-usage"
    out = []
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def window_start(window, now=None):
    now = now or time.time()
    if window == "week":
        return now - 7 * 86400
    if window == "five_hour":
        return now - 5 * 3600
    local = dt.datetime.fromtimestamp(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def shipped_metrics(root):
    out = []
    for f in sorted((Path(root) / "docs" / "items").glob("*/metrics.json")):
        try:
            m = json.loads(f.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(m, dict):
            out.append(m)
    return out


def level_estimate(root, cfg, level, history=None):
    """(usd, source): the median cost of shipped items at this level once there are 3, else the config value."""
    history = shipped_metrics(root) if history is None else history
    costs = [float(m["cost_usd"]) for m in history
             if m.get("budget") == level and isinstance(m.get("cost_usd"), (int, float)) and m["cost_usd"] > 0]
    if len(costs) >= 3:
        return round(statistics.median(costs), 2), f"median of {len(costs)} shipped"
    configured = ((cfg.get("budgets") or {}).get(level) or {}).get("estimate_usd")
    if isinstance(configured, (int, float)):
        return float(configured), "config"
    return ESTIMATE_USD.get(level, ESTIMATE_USD["standard"]), "default"


def capacity(root, cfg, items, claims, now=None):
    """What the global budget and the rate limit still allow. Conservative when there is no usage data."""
    now = now or time.time()
    orch = cfg.get("orchestration") or {}
    budget = orch.get("cost_budget_usd")
    budget = float(budget) if isinstance(budget, (int, float)) else None
    window = orch.get("budget_window", "day")
    floor = float(orch.get("rate_limit_floor", 20))
    factor = float(orch.get("unknown_usage_factor", 1.5))
    records = usage_records(root)
    start = window_start(window, now)
    in_window = [r for r in records if float(r.get("updated") or 0) >= start]
    live = bool(records)
    spent = sum(float(r.get("cost_usd") or 0) for r in in_window)
    by_item = {}
    for r in records:
        if r.get("item"):
            by_item[r["item"]] = by_item.get(r["item"], 0.0) + float(r.get("cost_usd") or 0)
    history = shipped_metrics(root)
    levels = {i["id"]: (i.get("budget") or cfg.get("budget_default") or "standard") for i in items}
    committed = 0.0
    for item in claims:
        est = level_estimate(root, cfg, levels.get(item, "standard"), history)[0]
        committed += max(est - by_item.get(item, 0.0), 0.0) if live else est * factor
    latest = max(records, key=lambda r: float(r.get("updated") or 0), default=None)
    five = None
    if latest and latest.get("five_hour_pct") is not None:
        resets = latest.get("five_hour_resets_at")
        if not resets or float(resets) > now:
            five = float(latest["five_hour_pct"])
    remaining = None if budget is None else round(budget - spent - committed, 2)
    return {"budget": budget, "window": window, "spent": round(spent, 2), "committed": round(committed, 2),
            "remaining": remaining, "live": live, "five_hour_pct": five, "rate_floor": floor,
            "rate_blocked": five is not None and five >= 100 - floor, "by_item": by_item,
            "estimate": lambda level: level_estimate(root, cfg, level, history)}
