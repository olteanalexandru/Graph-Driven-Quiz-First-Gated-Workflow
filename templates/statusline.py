#!/usr/bin/env python3
"""Status line for the workflow: item, budget, context use, prompt cache, cost.

Example: ITEM-042 standard | ctx 63% -> /flow:handoff | cache 91% | $1.84 | 5h 22%
Claude Code pipes session JSON on stdin; missing fields are skipped. Standard library only.

It also keeps the usage ledger the scheduler reads (<git-common-dir>/flow-usage/<session>.json:
the session's item, cost and rate-limit use), written only when a value changes.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def git(cwd, *args):
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=2)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pct(value):
    """A 0-100 percentage as an int."""
    return None if value is None else round(float(value))


def ratio_pct(value):
    """A 0-1 ratio (prompt_cache.hit_ratio) as a percentage."""
    return None if value is None else round(float(value) * 100)


def write_usage(cwd, data, item, level):
    """One small file per session; the scheduler sums them per item and per budget window."""
    session = re.sub(r"[^\w-]", "", str(data.get("session_id") or ""))
    common = git(cwd, "rev-parse", "--git-common-dir")
    if not session or not common:
        return
    limits = data.get("rate_limits") or {}
    rec = {"session": session, "item": item, "budget": level,
           "cost_usd": (data.get("cost") or {}).get("total_cost_usd"),
           "context_pct": pct((data.get("context_window") or {}).get("used_percentage")),
           "five_hour_pct": (limits.get("five_hour") or {}).get("used_percentage"),
           "five_hour_resets_at": (limits.get("five_hour") or {}).get("resets_at"),
           "seven_day_pct": (limits.get("seven_day") or {}).get("used_percentage"),
           "model": (data.get("model") or {}).get("id")}
    path = Path(common) if Path(common).is_absolute() else Path(cwd) / common
    path = path / "flow-usage" / f"{session}.json"
    try:
        old = json.loads(path.read_text("utf-8")) if path.exists() else {}
        old.pop("updated", None)
        if old == rec:
            return
        rec["updated"] = int(time.time())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec), "utf-8")
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        data = {}
    cwd = data.get("cwd") or (data.get("workspace") or {}).get("current_dir") or "."
    parts = []
    model = (data.get("model") or {}).get("display_name")
    root = git(cwd, "rev-parse", "--show-toplevel")
    handoff_at = None
    item = level = None
    if root and (Path(root) / ".flow" / "config.json").exists():
        try:
            cfg = json.loads((Path(root) / ".flow" / "config.json").read_text("utf-8"))
        except json.JSONDecodeError:
            cfg = {}
        branch = git(cwd, "branch", "--show-current") or git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        m = re.search(cfg.get("item_pattern", r"ITEM-\d+"), branch)
        if m:
            item = m.group(0)
            spec = Path(root) / "docs" / "items" / item / "spec.md"
            level = cfg.get("budget_default", "standard")
            if spec.exists():
                b = re.search(r"(?m)^budget:\s*(\w+)", spec.read_text("utf-8", "ignore"))
                level = b.group(1) if b else level
            handoff_at = ((cfg.get("budgets") or {}).get(level) or {}).get("handoff_at")
            parts.append(f"{item} {level}")
        write_usage(cwd, data, item, level)
    if model:
        parts.append(model)
    ctx = pct((data.get("context_window") or {}).get("used_percentage"))
    if ctx is not None:
        parts.append(f"ctx {ctx}%" + (" -> /flow:handoff" if handoff_at and ctx >= handoff_at else ""))
    cache = data.get("prompt_cache") or {}
    hit = ratio_pct(cache.get("hit_ratio"))
    if hit is not None:
        parts.append(f"cache {hit}%" if cache.get("warm", True) else "cache cold")
    cost = (data.get("cost") or {}).get("total_cost_usd")
    if cost is not None:
        parts.append(f"${cost:.2f}")
    five = pct(((data.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage"))
    if five is not None:
        parts.append(f"5h {five}%")
    print(" | ".join(parts))


if __name__ == "__main__":
    main()
