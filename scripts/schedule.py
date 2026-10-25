#!/usr/bin/env python3
"""Decide which work items may run at the same time, and manage their file claims.

Items live in docs/items/<ID>/ (spec.md with frontmatter, impact.json with the claim).
Two items may run in parallel only when they are independent:
  hard: their claims (files, folders, hot tokens) do not overlap, and neither changes a
        contract (api:, schema:, bus:, config:, state:, ui:) the other changes or uses;
  soft: they do not share most of their graph nodes (the same context read twice), and
        they are not both high rework risk in one domain.
Within those, the scheduler starts only what the WIP limit, the 5-hour rate limit and
the global cost budget allow (`orchestration` in .flow/config.json). Usage comes from
the ledger the status line writes; without it, estimates are conservative. Claims are
stored in the git common dir (.git/flow-claims/), so every worktree sees the same set.

  python3 schedule.py status            items, stages, active claims and conflicts
  python3 schedule.py next [--wip N]    which ready items can start now, and why the others wait
  python3 schedule.py waves [--wip N]   a full plan: batches that can run together
  python3 schedule.py claim ITEM-042    claim the item's files, or refresh the claim after re-planning
  python3 schedule.py release ITEM-042  drop the claim after merge
  python3 schedule.py usage             spend in the budget window, per-level estimates, capacity left
  python3 schedule.py record ITEM-042   write docs/items/ITEM-042/metrics.json (cost, rework) before merge

Exit codes: 0 ok, 1 conflict or nothing to do, 2 usage or setup error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_lib as lib  # noqa: E402
from flow_lib import (all_items, claim_conflicts, claim_from_impact, claims_dir, item_dir,  # noqa: E402
                     item_stage, load_claims, load_config, repo_root)


def as_list(x):
    if not x:
        return []
    return x if isinstance(x, list) else [x]


def critical_path(items):
    """Length of the longest chain of items that depend on each item (memoized DFS)."""
    by_id = {i["id"]: i for i in items}
    dependents = {i["id"]: [] for i in items}
    for i in items:
        for d in as_list(i["deps"]):
            if d in dependents:
                dependents[d].append(i["id"])
    memo, visiting = {}, set()

    def length(node):
        if node in memo:
            return memo[node]
        if node in visiting:            # dependency cycle: stop counting
            return 0
        visiting.add(node)
        memo[node] = 1 + max((length(c) for c in dependents[node]), default=0)
        visiting.discard(node)
        return memo[node]

    return {i: length(i) for i in by_id}


def unit(item_id, claim=None, item=None):
    """What the pair checks compare, from the stored claim when there is one."""
    claim, item = claim or {}, item or {}
    return {"id": item_id,
            "claim": {"paths": claim.get("paths", (item.get("claim") or {}).get("paths", [])),
                      "hot": claim.get("hot", (item.get("claim") or {}).get("hot", []))},
            "contracts": claim.get("contracts") or item.get("contracts") or {"changes": [], "uses": []},
            "nodes": claim.get("nodes") or item.get("nodes") or [],
            "risk": claim.get("risk") or item.get("risk") or "",
            "budget": claim.get("budget") or item.get("budget") or "",
            "deps": item.get("deps", [])}


def hard_conflicts(a, b):
    return claim_conflicts(a["claim"], b["claim"]) + lib.contract_conflicts(a["contracts"], b["contracts"])


def uses_changes_of(a, b):
    """a uses a contract that b changes (so a should run after b)."""
    return any(lib.contract_match(u, c) for u in a["contracts"].get("uses", []) for c in b["contracts"].get("changes", []))


def implicit_deps(units):
    """The item that uses a contract goes after the item that changes it, unless that would close a cycle
    with the explicit or earlier implicit order (the hard conflict check still keeps such a pair apart)."""
    graph = {u["id"]: set(as_list(u.get("deps"))) for u in units}
    out = {u["id"]: set() for u in units}

    def reaches(start, goal):
        seen, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node == goal:
                return True
            if node not in seen:
                seen.add(node)
                stack.extend(graph.get(node, ()))
        return False

    for a in units:
        for b in units:
            if a["id"] != b["id"] and uses_changes_of(a, b) and not reaches(b["id"], a["id"]):
                out[a["id"]].add(b["id"])
                graph[a["id"]].add(b["id"])
    return out


def level_of(cfg, u):
    return u.get("budget") or cfg.get("budget_default") or "standard"


def capacity_lines(cap):
    lines = []
    if cap["budget"] is None:
        lines.append("budget: no global cost budget set (orchestration.cost_budget_usd)")
    else:
        lines.append(f"budget: ${cap['remaining']:.2f} of ${cap['budget']:.2f} left this {cap['window']} "
                     f"(spent ${cap['spent']:.2f}, active items expected to need ${cap['committed']:.2f} more)")
    if not cap["live"]:
        lines.append("usage: no live data (the status line writes it); active items count at their estimate "
                     "times orchestration.unknown_usage_factor")
    if cap["five_hour_pct"] is not None:
        lines.append(f"5-hour window: {cap['five_hour_pct']:.0f}% used, keeping {cap['rate_floor']:.0f}% free")
    return lines


def ready_items(items, active_ids):
    done = {i["id"] for i in items if i["status"] == "done"}
    return [i for i in items
            if i["status"] in ("approved", "in-progress") and i["id"] not in active_ids
            and i["claim"] and all(d in done for d in as_list(i["deps"]))]


def cmd_status(root, cfg, args):
    items, claims = all_items(root), load_claims(root)
    if not items:
        print("no items in docs/items/")
    for i in items:
        mark = "active" if i["id"] in claims else ""
        print(f"{i['id']:<10} {i['status']:<12} stage {item_stage(root, i['id'], cfg):<5} tier {i['tier']:<2} "
              f"budget {i['budget'] or cfg.get('budget_default'):<9} {mark}")
        if i.get("error"):
            print(f"  {i['id']}: {i['error']}")
    ids = sorted(claims)
    by_id = {i["id"]: i for i in items}
    units = {c: unit(c, claims[c], by_id.get(c)) for c in ids}
    clashes = [(a, b, hard_conflicts(units[a], units[b])) for n, a in enumerate(ids) for b in ids[n + 1:]]
    clashes = [c for c in clashes if c[2]]
    for a, b, why in clashes:
        print(f"CONFLICT {a} and {b}: {', '.join(why[:5])}")
    return 1 if clashes else 0


def cmd_next(root, cfg, args):
    items, claims = all_items(root), load_claims(root)
    by_id = {i["id"]: i for i in items}
    active = [unit(c, claims[c], by_id.get(c)) for c in sorted(claims)]
    wip = args.wip or int(cfg.get("wip_limit", 2))
    cap = lib.capacity(root, cfg, items, claims)
    cp = critical_path(items)
    ready = [unit(i["id"], None, i) for i in sorted(ready_items(items, set(claims)), key=lambda i: (-cp[i["id"]], i["id"]))]
    pending = [unit(i["id"], None, i) for i in items
               if i["status"] in ("approved", "in-progress") and i["claim"] and i["id"] not in claims]
    order = implicit_deps(pending)          # a changer still waiting on its own deps also goes first
    remaining = cap["remaining"]
    chosen, blocked = [], []
    for cand in ready:
        first = sorted(order[cand["id"]])
        if first:
            blocked.append((cand["id"], f"uses a contract that {', '.join(first)} changes; that goes first"))
            continue
        if len(claims) + len(chosen) >= wip:
            blocked.append((cand["id"], f"WIP limit {wip} reached"))
            continue
        if cap["rate_blocked"]:
            blocked.append((cand["id"], f"5-hour window at {cap['five_hour_pct']:.0f}%, keeping {cap['rate_floor']:.0f}% free"))
            continue
        hard = [(o["id"], hard_conflicts(cand, o)) for o in active + chosen]
        hard = [(o, why) for o, why in hard if why]
        if hard:
            blocked.append((cand["id"], "overlaps " + "; ".join(f"{o} ({why[0]})" for o, why in hard)))
            continue
        soft = [(o["id"], lib.soft_blockers(cand, o, cfg)) for o in active + chosen]
        soft = [(o, why) for o, why in soft if why]
        if soft:
            blocked.append((cand["id"], "run after " + "; ".join(f"{o}: {why[0]}" for o, why in soft)))
            continue
        est = cap["start_cost"](level_of(cfg, cand))
        if remaining is not None and remaining < est:
            blocked.append((cand["id"], f"budget: needs about ${est:.2f}, ${max(remaining, 0):.2f} left this {cap['window']}"))
            continue
        chosen.append(cand)
        if remaining is not None:
            remaining -= est
    done = {i["id"] for i in items if i["status"] == "done"}
    for i in items:
        if i["status"] in ("approved", "in-progress") and i["id"] not in claims:
            missing = [d for d in as_list(i["deps"]) if d not in done]
            if missing:
                blocked.append((i["id"], "needs " + ", ".join(missing) + " merged first"))
            elif not i["claim"]:
                blocked.append((i["id"], "no impact.json yet (run /flow:plan)"))
    print(f"active: {', '.join(sorted(claims)) or 'none'} (WIP limit {wip})")
    for line in capacity_lines(cap):
        print(line)
    print("start now: " + (", ".join(c["id"] for c in chosen) or "nothing"))
    for item, why in blocked:
        print(f"wait: {item}: {why}")
    return 0 if chosen else 1


def cmd_waves(root, cfg, args):
    """List scheduling: each item goes to the earliest wave after its deps with no overlap."""
    items = [i for i in all_items(root) if i["status"] != "done"]
    wip = args.wip or int(cfg.get("wip_limit", 2))
    unknown = [i["id"] for i in items if not i["claim"]]
    plan = [i for i in items if i["claim"]]
    units = {i["id"]: unit(i["id"], None, i) for i in plan}
    order = implicit_deps(list(units.values()))
    for i in plan:
        i["deps"] = sorted(set(as_list(i["deps"])) | order[i["id"]])
    cp = critical_path(items)
    order, placed, waves = sorted(plan, key=lambda i: (-cp[i["id"]], i["id"])), {}, []
    planned = {i["id"] for i in plan}
    done = {i["id"] for i in all_items(root) if i["status"] == "done"}
    blocked = {}
    for i in plan:
        missing = [d for d in as_list(i["deps"]) if d not in done and d not in planned]
        if missing:
            blocked[i["id"]] = missing
    pending = [i for i in order if i["id"] not in blocked]
    while pending:
        progressed = False
        for cand in list(pending):
            deps = [d for d in as_list(cand["deps"]) if d in planned]
            if any(d in blocked for d in deps):
                blocked[cand["id"]] = [d for d in deps if d in blocked]
                pending.remove(cand)
                progressed = True
                continue
            if any(d not in placed for d in deps):
                continue
            w = max((placed[d] + 1 for d in deps), default=0)
            while True:
                while len(waves) <= w:
                    waves.append([])
                if len(waves[w]) < wip and not any(hard_conflicts(units[cand["id"]], units[o["id"]])
                                                   or lib.soft_blockers(units[cand["id"]], units[o["id"]], cfg)
                                                   for o in waves[w]):
                    break
                w += 1
            waves[w].append(cand)
            placed[cand["id"]] = w
            pending.remove(cand)
            progressed = True
        if not progressed:
            print("dependency cycle or missing dependency among: " + ", ".join(i["id"] for i in pending))
            break
    for n, wave in enumerate(waves, 1):
        print(f"wave {n}: " + ", ".join(i["id"] for i in wave))
    for item, deps in sorted(blocked.items()):
        print(f"not placed: {item} waits for {', '.join(deps)} (not planned or not placed)")
    if unknown:
        print("no impact.json yet (run /flow:plan): " + ", ".join(unknown))
    return 0


def cmd_claim(root, cfg, args):
    impact_path = item_dir(root, args.item) / "impact.json"
    if not impact_path.exists():
        print(f"{impact_path.relative_to(root)} not found; run /flow:plan {args.item} first")
        return 2
    try:
        impact = json.loads(impact_path.read_text("utf-8"))
        claim = claim_from_impact(impact)
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"{impact_path.relative_to(root)} is not a valid impact file ({e})")
        return 2
    items = {i["id"]: i for i in all_items(root)}
    me = items.get(args.item, {})
    claim.update({"contracts": lib.contracts_of(impact), "nodes": me.get("nodes", []),
                  "risk": me.get("risk", ""), "budget": me.get("budget", "")})
    claims = load_claims(root)
    others = {k: v for k, v in claims.items() if k != args.item}
    if not args.force:
        if me.get("status") not in ("approved", "in-progress"):
            print(f"{args.item} is '{me.get('status', 'missing')}'; approve its spec first (/flow:quiz)")
            return 1
        waiting = [d for d in as_list(me.get("deps")) if items.get(d, {}).get("status") != "done"]
        if waiting:
            print(f"{args.item} waits for {', '.join(waiting)} to be done")
            return 1
        wip = int(cfg.get("wip_limit", 2))
        if args.item not in claims and len(claims) >= wip:
            print(f"WIP limit {wip} reached ({', '.join(sorted(claims))}); ship one first")
            return 1
        if args.item not in claims:
            cap = lib.capacity(root, cfg, list(items.values()), claims)
            if cap["rate_blocked"]:
                print(f"the 5-hour window is at {cap['five_hour_pct']:.0f}% (keeping {cap['rate_floor']:.0f}% free); "
                      f"not claimed. The user can approve it with --force")
                return 1
            est, source = cap["start_cost"](level_of(cfg, claim)), cap["estimate"](level_of(cfg, claim))[1]
            if cap["remaining"] is not None and cap["remaining"] < est:
                print(f"{args.item} needs about ${est:.2f} ({source}) and ${max(cap['remaining'], 0):.2f} is left "
                      f"in this {cap['window']}'s budget; not claimed. The user can approve it with --force")
                return 1
    mine = unit(args.item, claim)
    clash = [(o, hard_conflicts(mine, unit(o, c, items.get(o)))) for o, c in others.items()]
    clash = [(o, why) for o, why in clash if why]
    if clash and not args.force:
        for o, why in clash:
            print(f"overlaps {o}: {', '.join(why[:8])}")
        print("not claimed; wait for the other item to merge, or narrow the impact set")
        return 1
    for o, c in others.items():
        for why in lib.soft_blockers(mine, unit(o, c, items.get(o)), cfg):
            print(f"note: {o} {why}; running them one after the other is cheaper")
    d = claims_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    old = claims.get(args.item) or {}
    claim.update({"item": args.item, "claimed_at": old.get("claimed_at") or dt.datetime.now().isoformat(timespec="seconds"),
                  "concurrent": sorted(set(old.get("concurrent") or []) | set(others))})
    lib.write_json(d / f"{args.item}.json", claim)
    for o, c in others.items():
        if args.item not in (c.get("concurrent") or []):
            c["concurrent"] = sorted(set(c.get("concurrent") or []) | {args.item})
            lib.write_json(d / f"{o}.json", c)
    verb = "refreshed claim for" if args.item in claims else "claimed"
    print(f"{verb} {args.item}: {len(claim['paths'])} paths, hot: {', '.join(claim['hot']) or 'none'}"
          + (f", contracts: {', '.join(claim['contracts']['changes'])}" if claim["contracts"]["changes"] else ""))
    return 0


def cmd_usage(root, cfg, args):
    items, claims = all_items(root), load_claims(root)
    cap = lib.capacity(root, cfg, items, claims)
    for line in capacity_lines(cap):
        print(line)
    for level in ("lean", "standard", "deep"):
        est, source = cap["estimate"](level)
        print(f"estimate {level}: ${est:.2f} ({source})")
    by_id = {i["id"]: i for i in items}
    for c in sorted(claims):
        level = level_of(cfg, unit(c, claims[c], by_id.get(c)))
        print(f"active {c}: ${cap['by_item'].get(c, 0.0):.2f} spent, estimate ${cap['estimate'](level)[0]:.2f} ({level})")
    return 0


def cmd_record(root, cfg, args):
    """Cost and rework of one item, written before merge so the scheduler can learn from it."""
    item = args.item
    spec = lib.read_spec(root, item) or {}
    records = [r for r in lib.usage_records(root) if r.get("item") == item]
    base = cfg.get("base_branch", "main")
    since = lib.git(root, "merge-base", base, "HEAD")
    subjects = (lib.git(root, "log", "--format=%s", f"{since}..HEAD") if since else "") or ""
    rework = {kind: sum(1 for ln in subjects.splitlines() if ln.startswith(f"{item}: {kind} fixes"))
              for kind in ("review", "audit", "integration")}
    state_file = Path(root) / ".flow" / "state" / f"{item}.json"
    try:
        state = json.loads(state_file.read_text("utf-8")) if state_file.exists() else {}
    except json.JSONDecodeError:
        state = {}
    spec_text = (item_dir(root, item) / "spec.md").read_text("utf-8", "ignore") if spec else ""
    escalations = [ln.strip() for ln in lib.md_section(spec_text, "Budget log").splitlines() if "->" in ln]
    claim = load_claims(root).get(item) or {}
    metrics = {
        "item": item, "tier": spec.get("tier", ""), "budget": spec.get("budget", ""),
        "clarify": spec.get("clarify", "quick"),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in records), 2) if records else None,
        "sessions": len(records),
        "peak_context_pct": max((r.get("context_pct") or 0 for r in records), default=None),
        "usage_data": "live" if records else "none",
        "gate_failures": int(state.get("gate_failures") or 0),
        "rework_rounds": rework,
        "escalations": len(escalations),
        "parallel_with": claim.get("concurrent", []),
        "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    lib.write_json(item_dir(root, item) / "metrics.json", metrics)
    print(f"recorded {item}: cost {('$%.2f' % metrics['cost_usd']) if records else 'unknown (no usage data)'}, "
          f"rework {sum(rework.values())} rounds, {metrics['gate_failures']} gate failures, "
          f"{len(escalations)} escalations")
    return 0


def cmd_release(root, cfg, args):
    f = claims_dir(root) / f"{args.item}.json"
    if f.exists():
        f.unlink()
        print(f"released {args.item}")
    else:
        print(f"{args.item} had no claim")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("next", "waves"):
        p = sub.add_parser(name)
        p.add_argument("--wip", type=int)
    p = sub.add_parser("claim")
    p.add_argument("item")
    p.add_argument("--force", action="store_true",
                   help="claim despite overlaps, unmet dependencies or the WIP limit (you handle the merge)")
    p = sub.add_parser("release")
    p.add_argument("item")
    sub.add_parser("usage")
    p = sub.add_parser("record")
    p.add_argument("item")
    args = ap.parse_args(argv)
    root = repo_root(args.root or ".")
    if root is None:
        print("not inside a git repository")
        return 2
    cfg = load_config(root)
    if cfg is None:
        print("this repo has no .flow/config.json; run /flow:init first")
        return 2
    return {"status": cmd_status, "next": cmd_next, "waves": cmd_waves, "claim": cmd_claim,
            "release": cmd_release, "usage": cmd_usage, "record": cmd_record}[args.cmd](root, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
