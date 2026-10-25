#!/usr/bin/env python3
"""Editable design: which design files the user changed by hand, and the user's approval.

Design files are docs/items/<ID>/uml/*, data-structures.md, messages.md, performance.md
and baseline.md. The hooks record a hash each time Claude writes one
(docs/items/<ID>/design.lock.json); a file whose content differs from that hash was
edited outside Claude and is authoritative. The approval is a snapshot of every design
file; any later change shows as "changed since approval".

  python3 design.py status ITEM-042 [--json] [--gate]   --gate: exit 1 while the item needs an approval it lacks
  python3 design.py approve ITEM-042 [--note TEXT]      after the user approved the design
  python3 design.py unlock ITEM-042 uml/x.mmd ...       one Claude edit of these files, after the user agreed in chat

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_lib as lib  # noqa: E402


def cmd_status(root, cfg, args):
    state = lib.design_state(root, args.item)
    spec = lib.read_spec(root, args.item) or {}
    required = spec.get("tier") != "S" and lib.design_approval_required(cfg, spec)
    state["approval_required"] = required
    if args.json:
        print(json.dumps(state, indent=2))
    else:
        print(f"{args.item} design: {state['status']}"
              + (f" (approved {state['approved_at']})" if state["approved_at"] else "")
              + ("; approval required before code" if required else ""))
        if state["error"]:
            print(f"  problem: {state['error']}")
        for f in state["hand_edited"]:
            print(f"  edited by hand: {f}")
        for f in state["changed"]:
            print(f"  changed since approval: {f}")
    if args.gate and required and state["status"] != "approved":
        return 1
    return 0


def cmd_approve(root, cfg, args):
    if not lib.item_dir(root, args.item).is_dir():
        print(f"docs/items/{args.item}/ not found")
        return 2
    state = lib.approve_design(root, args.item, args.note or "")
    print(f"approved the design of {args.item}: {len(state['files'])} files"
          + (f", including hand edits: {', '.join(state['hand_edited'])}" if state["hand_edited"] else ""))
    return 0


def cmd_unlock(root, cfg, args):
    if not lib.item_dir(root, args.item).is_dir():
        print(f"docs/items/{args.item}/ not found")
        return 2
    bad = [f for f in args.files if not lib.is_design_file(f)]
    if bad:
        print(f"not design files: {', '.join(bad)}")
        return 2
    lib.unlock_design(root, args.item, args.files)
    print(f"next Claude edit allowed for {args.item}: {', '.join(args.files)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("item")
    p.add_argument("--json", action="store_true")
    p.add_argument("--gate", action="store_true")
    p = sub.add_parser("approve")
    p.add_argument("item")
    p.add_argument("--note")
    p = sub.add_parser("unlock")
    p.add_argument("item")
    p.add_argument("files", nargs="+")
    args = ap.parse_args(argv)
    root = lib.repo_root(args.root or ".")
    if root is None:
        print("not inside a git repository")
        return 2
    cfg = lib.load_config(root) or dict(lib.DEFAULT_CONFIG)
    return {"status": cmd_status, "approve": cmd_approve, "unlock": cmd_unlock}[args.cmd](root, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
