#!/usr/bin/env python3
"""Integration gate (S7a): items that finished together are tested on one merged tree before either ships.

  python3 integrate.py run [ITEM ...] [--keep]   merge the items into a temporary worktree from the base
                                                 branch and run `integration_checks` there
  python3 integrate.py finish RUN --verdict PASS|BLOCK [--notes FILE] [--keep]
                                                 record the combined review and remove the worktree
  python3 integrate.py verify ITEM               exit 0 when ITEM needs no integration, or its branch head
                                                 is in a PASS run; exit 1 otherwise
  python3 integrate.py status                    recent runs

Without items, `run` takes every claimed item whose branch has `Verdict: PASS` in its review.md.
In `integration_checks`, {grep} becomes a quoted "@item:ITEM-1|@item:ITEM-2" for test filters.
A check whose tool is missing counts as a failure: a gate that ran nothing is not a pass.
Runs are stored in the git common dir (.git/flow-integration/), so every worktree sees them.

Exit codes: 0 ok, 1 blocked or failed, 2 usage or setup error. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_lib as lib  # noqa: E402

MERGE_IDENTITY = ["-c", "user.name=flow integration", "-c", "user.email=flow-integration@localhost"]


def runs_dir(root):
    return lib.common_dir(root) / "flow-integration"


def save_run(root, run):
    lib.write_json(runs_dir(root) / f"{run['run']}.json", run)


def load_run(root, run_id):
    for run in lib.integration_runs(root):
        if run.get("run") == run_id:
            return run
    return None


def head_of(root, item):
    return lib.branch_head(root, item)


def run_check(cmd, cwd, timeout):
    """Run one check in its own process group with CI set, so it starts its own server and a timeout
    kills everything it started. Returns (ok, output)."""
    env = {k: v for k, v in os.environ.items() if k not in ("BASE_URL", "PLAYWRIGHT_BASE_URL", "PORT")}
    env.update({"CI": "1", "FLOW_INTEGRATION": "1"})
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    p = subprocess.Popen(cmd, shell=True, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", **kw)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
        p.communicate()
        return False, "timed out"
    out = tail(out)
    return p.returncode == 0, ("tool not installed\n" + out) if p.returncode == 127 else out


def tail(text, n=25):
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def remove_worktree(root, path):
    if path and Path(path).exists():
        lib.git(root, "worktree", "remove", "--force", str(path))


def cmd_run(root, cfg, args):
    items = args.items or [i for i in sorted(lib.load_claims(root))
                           if lib.verdict_pass(lib.git(root, "show", f"{i}:docs/items/{i}/review.md") or "")]
    if not items:
        print("nothing to integrate: no claimed item has a passing review yet")
        return 1
    heads = {i: head_of(root, i) for i in items}
    missing = [i for i, h in heads.items() if not h]
    if missing:
        print(f"no branch for {', '.join(missing)} (an item's branch is named with its ID)")
        return 2
    base = cfg.get("base_branch", "main")
    base_sha = lib.git(root, "rev-parse", "--verify", "--quiet", base)
    if not base_sha:
        print(f"base branch {base} not found")
        return 2
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    main = lib.common_dir(root).resolve().parent
    wt = main.parent / f"{main.name}-integration-{run_id}"
    if lib.git(root, "worktree", "add", "--detach", str(wt), base_sha) is None:
        print(f"could not create the integration worktree at {wt}")
        return 2
    run = {"run": run_id, "at": lib.now_iso(), "base": base, "base_sha": base_sha, "items": heads,
           "order": items, "conflicts": [], "checks": [], "review": None, "worktree": str(wt)}
    for item in items:
        p = subprocess.run(["git", *MERGE_IDENTITY, "merge", "--no-ff", "--no-edit", "-m", f"integrate {item}",
                            heads[item]], cwd=str(wt), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            files = (lib.git(wt, "diff", "--name-only", "--diff-filter=U") or "").splitlines()
            lib.git(wt, "merge", "--abort")
            run["conflicts"] = [{"item": item, "files": files or [tail(p.stdout + p.stderr, 5)]}]
            break
    if not run["conflicts"]:
        checks = cfg.get("integration_checks") or []
        if not checks:
            run["checks"].append({"cmd": "(none)", "ok": False,
                                  "tail": "no integration_checks in .flow/config.json; add the project's test commands"})
        grep = lib.shell_quote("|".join(f"@item:{i}" for i in items))
        timeout = int(cfg.get("integration_timeout", 1800))
        for cmd in checks:
            cmd = cmd.replace("{grep}", grep)
            ok, out = run_check(cmd, wt, timeout)
            run["checks"].append({"cmd": cmd, "ok": ok, "tail": "" if ok else out})
    failed = run["conflicts"] or not all(c["ok"] for c in run["checks"])
    run["verdict"] = "BLOCK" if failed else "REVIEW"
    print(f"integration run {run_id}: {', '.join(items)} on {base} {base_sha[:8]}")
    for c in run["conflicts"]:
        print(f"CONFLICT merging {c['item']}: {', '.join(c['files'])}")
    for c in run["checks"]:
        print(("ok   " if c["ok"] else "FAIL ") + c["cmd"] + ("" if c["ok"] else "\n" + c["tail"]))
    if failed:
        if not args.keep:
            remove_worktree(root, wt)
            run["worktree"] = None
        save_run(root, run)
        print("BLOCK: fix it in the item that owns the cause (commit '<ID>: integration fixes'), then run again")
        return 1
    save_run(root, run)
    print(f"checks passed. Next: the combined review in {wt}, then\n"
          f"  python3 {Path(__file__).resolve()} finish {run_id} --verdict PASS|BLOCK --notes {wt / 'INTEGRATION-REVIEW.md'}")
    return 0


def cmd_finish(root, cfg, args):
    run = load_run(root, args.run)
    if not run:
        print(f"no integration run {args.run}")
        return 2
    if run.get("verdict") != "REVIEW":
        print(f"run {args.run} is {run.get('verdict')}, not waiting for a review")
        return 2
    notes = ""
    if args.notes and Path(args.notes).is_file():
        notes = Path(args.notes).read_text("utf-8", "ignore")[:4000]
    run["review"] = {"verdict": args.verdict, "at": lib.now_iso(), "notes": notes}
    run["verdict"] = "PASS" if args.verdict == "PASS" else "BLOCK"
    if not args.keep:
        remove_worktree(root, run.get("worktree"))
        run["worktree"] = None
    save_run(root, run)
    print(f"integration run {args.run}: {run['verdict']} for {', '.join(run['order'])}")
    return 0 if run["verdict"] == "PASS" else 1


def cmd_verify(root, cfg, args):
    partners = lib.integration_partners(root, cfg, args.item)
    if not partners:
        print(f"{args.item}: no integration needed (no other claimed item has passed review)")
        return 0
    run = lib.integration_covered(root, args.item, partners)
    if run:
        print(f"{args.item}: integrated with {', '.join(partners)} in run {run['run']}")
        return 0
    print(f"{args.item}: finished together with {', '.join(partners)}, and no passing integration run covers "
          f"the current heads of all of them; run /flow:integrate before shipping")
    return 1


def cmd_status(root, cfg, args):
    runs = lib.integration_runs(root)
    if not runs:
        print("no integration runs")
    for run in runs[-10:]:
        print(f"{run.get('run')} {run.get('verdict', '?'):<7} {', '.join(run.get('order') or [])}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("items", nargs="*")
    p.add_argument("--keep", action="store_true", help="keep the worktree even when the run is blocked")
    p = sub.add_parser("finish")
    p.add_argument("run")
    p.add_argument("--verdict", choices=["PASS", "BLOCK"], required=True)
    p.add_argument("--notes")
    p.add_argument("--keep", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("item")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    root = lib.repo_root(args.root or ".")
    if root is None:
        print("not inside a git repository")
        return 2
    cfg = lib.load_config(root)
    if cfg is None:
        print("this repo has no .flow/config.json; run /flow:init first")
        return 2
    return {"run": cmd_run, "finish": cmd_finish, "verify": cmd_verify, "status": cmd_status}[args.cmd](root, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
