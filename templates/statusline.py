#!/usr/bin/env python3
"""Status line for the workflow: item, budget, context use, prompt cache, cost.

Example: ITEM-042 standard | ctx 63% -> /flow:handoff | cache 91% | $1.84 | 5h 22%
Claude Code pipes session JSON on stdin; missing fields are skipped. Standard library only.
"""
import json
import re
import subprocess
import sys
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
