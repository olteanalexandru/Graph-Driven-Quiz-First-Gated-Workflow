#!/usr/bin/env python3
"""Decide which work items may run at the same time, and manage their file claims.

Items live in docs/items/<ID>/ (spec.md with frontmatter, impact.json with the claim).
Two items may run in parallel only when their claims do not overlap. Claims are stored
in the git common dir (.git/flow-claims/), so every worktree sees the same set.

  python3 schedule.py status            items, stages, active claims and conflicts
  python3 schedule.py next [--wip N]    which ready items can start now
  python3 schedule.py waves [--wip N]   a full plan: batches that can run together
  python3 schedule.py claim ITEM-042    claim the item's files, or refresh the claim after re-planning
  python3 schedule.py release ITEM-042  drop the claim after merge

Exit codes: 0 ok, 1 conflict or nothing to do, 2 usage or setup error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        print(f"{i['id']:<10} {i['status']:<12} stage {item_stage(root, i['id']):<5} tier {i['tier']:<2} "
              f"budget {i['budget'] or cfg.get('budget_default'):<9} {mark}")
        if i.get("error"):
            print(f"  {i['id']}: {i['error']}")
    ids = sorted(claims)
    clashes = [(a, b, claim_conflicts(claims[a], claims[b])) for n, a in enumerate(ids) for b in ids[n + 1:]]
    clashes = [c for c in clashes if c[2]]
    for a, b, why in clashes:
        print(f"CONFLICT {a} and {b}: {', '.join(why[:5])}")
    return 1 if clashes else 0


def cmd_next(root, cfg, args):
    items, claims = all_items(root), load_claims(root)
    wip = args.wip or int(cfg.get("wip_limit", 2))
    cp = critical_path(items)
    ready = sorted(ready_items(items, set(claims)), key=lambda i: (-cp[i["id"]], i["id"]))
    chosen, blocked = [], []
    for cand in ready:
        if len(claims) + len(chosen) >= wip:
            blocked.append((cand["id"], f"WIP limit {wip} reached"))
            continue
        clash = [(o, claim_conflicts(cand["claim"], c)) for o, c in claims.items()]
        clash += [(o["id"], claim_conflicts(cand["claim"], o["claim"])) for o in chosen]
        clash = [(o, why) for o, why in clash if why]
        if clash:
            blocked.append((cand["id"], "overlaps " + "; ".join(f"{o} ({why[0]})" for o, why in clash)))
        else:
            chosen.append(cand)
    done = {i["id"] for i in items if i["status"] == "done"}
    for i in items:
        if i["status"] in ("approved", "in-progress") and i["id"] not in claims:
            missing = [d for d in as_list(i["deps"]) if d not in done]
            if missing:
                blocked.append((i["id"], "needs " + ", ".join(missing) + " merged first"))
            elif not i["claim"]:
                blocked.append((i["id"], "no impact.json yet (run /flow:plan)"))
    print(f"active: {', '.join(sorted(claims)) or 'none'} (WIP limit {wip})")
    print("start now: " + (", ".join(c["id"] for c in chosen) or "nothing"))
    for item, why in blocked:
        print(f"wait: {item}: {why}")
    return 0 if chosen else 1


def cmd_waves(root, cfg, args):
    """List scheduling: each item goes to the earliest wave after its deps with no overlap."""
    items = [i for i in all_items(root) if i["status"] != "done"]
    wip = args.wip or int(cfg.get("wip_limit", 2))
    cp = critical_path(items)
    unknown = [i["id"] for i in items if not i["claim"]]
    plan = [i for i in items if i["claim"]]
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
                if len(waves[w]) < wip and not any(claim_conflicts(cand["claim"], o["claim"]) for o in waves[w]):
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
        claim = claim_from_impact(json.loads(impact_path.read_text("utf-8")))
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"{impact_path.relative_to(root)} is not a valid impact file ({e})")
        return 2
    claims = load_claims(root)
    others = {k: v for k, v in claims.items() if k != args.item}
    if not args.force:
        items = {i["id"]: i for i in all_items(root)}
        me = items.get(args.item, {})
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
    clash = [(o, claim_conflicts(claim, c)) for o, c in others.items()]
    clash = [(o, why) for o, why in clash if why]
    if clash and not args.force:
        for o, why in clash:
            print(f"overlaps {o}: {', '.join(why[:8])}")
        print("not claimed; wait for the other item to merge, or narrow the impact set")
        return 1
    d = claims_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    claim.update({"item": args.item, "claimed_at": dt.datetime.now().isoformat(timespec="seconds")})
    (d / f"{args.item}.json").write_text(json.dumps(claim, indent=2) + "\n", "utf-8")
    verb = "refreshed claim for" if args.item in claims else "claimed"
    print(f"{verb} {args.item}: {len(claim['paths'])} paths, hot: {', '.join(claim['hot']) or 'none'}")
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
    args = ap.parse_args(argv)
    root = repo_root(args.root or ".")
    if root is None:
        print("not inside a git repository")
        return 2
    cfg = load_config(root)
    if cfg is None:
        print("this repo has no .flow/config.json; run /flow:init first")
        return 2
    return {"status": cmd_status, "next": cmd_next, "waves": cmd_waves,
            "claim": cmd_claim, "release": cmd_release}[args.cmd](root, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
