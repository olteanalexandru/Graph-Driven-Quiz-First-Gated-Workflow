#!/usr/bin/env python3
"""Claude Code hooks for the workflow. Standard library only (Python 3.9+).

  flow_hook.py pre-edit       PreToolUse on Edit|Write|MultiEdit|NotebookEdit: design guard, spec and
                              design gates, file claims
  flow_hook.py post-edit      PostToolUse on the same tools: remember what Claude wrote to design files
  flow_hook.py pre-bash       PreToolUse on Bash: destructive git commands, shell writes to the config
  flow_hook.py stop           Stop: the item's checks (tests, baseline tests, security) before Claude finishes
  flow_hook.py session-start  SessionStart (startup, resume, clear, compact): put the item's state back

Does nothing in repositories without .flow/config.json. With "mode": "warn" problems are
reported but nothing is blocked; with "mode": "enforce" they block. The claim check covers
Claude's edit tools; files written by shell commands are caught in review (scope check).
The design guard works in both modes: a design file the user changed by hand, or an
approved design, is never overwritten without the user's approval (a permission prompt).
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
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
NO_PROMPT_MODES = ("bypassPermissions", "dontAsk")   # permission modes where an "ask" reaches nobody
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

def design_guard(data, root, item, sub):
    """Ask before Claude changes a design file the user edited by hand, or any file of an approved design."""
    state = lib.design_state(root, item)
    if sub in state["unlocked"]:
        return 0
    reasons = []
    if (lib.item_dir(root, item) / sub).is_file() and sub in state["hand_edited"]:
        reasons.append(f"{item} {sub} was changed outside Claude since the workflow last wrote it; "
                       f"those edits are the user's design decisions")
    if state["approved_at"]:
        reasons.append(f"the design of {item} was approved {state['approved_at']} and this edit changes it")
    if not reasons:
        return 0
    reason = "; ".join(reasons)
    if data.get("permission_mode") in NO_PROMPT_MODES:
        script = lib.shell_quote(PLUGIN_ROOT / "scripts" / "design.py")
        sys.stderr.write(f"[flow] {reason}. Ask the user in chat before changing it. If they agree, run "
                         f"`python3 {script} unlock {item} {sub}` and retry. A subagent cannot ask: report the file and the "
                         f"intended change in your final message and do not retry.\n")
        return 2
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                 "permissionDecisionReason": f"[flow] {reason}. Allow only if this change "
                                                             f"is what you want; otherwise your version stays."}})
    return 0


def pre_edit(data, root, cfg):
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    rel = lib.rel_path(path, root) if path else None
    if rel is None:
        return 0
    if rel in PROTECTED and cfg.get("mode") == "enforce":
        sys.stderr.write(f"[flow] {rel} sets enforcement; the user changes it, not an agent.\n")
        return 2
    doc_item, sub = lib.split_item_doc(rel)
    if sub == lib.LOCK_NAME:
        sys.stderr.write(f"[flow] {rel} is kept by the hooks and design.py; approve with "
                         f"`design.py approve {doc_item}` after the user agrees instead of editing it.\n")
        return 2
    if doc_item and lib.is_design_file(sub) and design_guard(data, root, doc_item, sub) == 2:
        return 2
    item = lib.current_item(root, cfg)
    if not item or item in rel:                       # planning on the base branch, or the item's own files
        return 0
    spec = lib.read_spec(root, item)
    if not spec or spec.get("status") not in ("approved", "in-progress", "done"):
        return problem(cfg, "PreToolUse", f"{item} has no approved spec at docs/items/{item}/spec.md. Run /flow:quiz first.")
    if spec.get("tier") != "S" and lib.design_approval_required(cfg, spec):
        state = lib.design_state(root, item)
        if state["status"] != "approved":
            what = {"none": "has not been written or approved yet", "draft": "is not approved yet"}.get(
                state["status"], "changed since it was approved: " + ", ".join(state["changed"]))
            return problem(cfg, "PreToolUse",
                           f"The design of {item} {what}. Code waits for the user's approval: run /flow:plan "
                           f"{item} to bring the spec and tests in line and ask for approval.")
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


# ---------------------------------------------------------------- PostToolUse: edits

def post_edit(data, root, cfg):
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    rel = lib.rel_path(path, root) if path else None
    item, sub = lib.split_item_doc(rel)
    if not item or not lib.is_design_file(sub) or not lib.item_dir(root, item).is_dir():
        return 0
    if lib.load_lock(root, item).get("_error"):
        emit({"systemMessage": f"[flow warn] docs/items/{item}/{lib.LOCK_NAME} is not valid JSON; "
                               f"design edits are not being recorded until it is fixed"})
        return 0
    lib.record_design_write(root, item, sub)
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


# A redirect into a file (not 2>&1, >&2 or /dev/null), or a command that writes or restores files.
SHELL_WRITE = re.compile(r"(?<![\d&>])>{1,2}(?!>|\s*(?:&|/dev/null\b))|\bsed\s+-i|\btee\b|\bmv\b|\bcp\b|\brm\b"
                         r"|\bperl\s+-i|\btruncate\b|write_text|open\(|\bgit\s+(?:checkout|restore)\b")
DESIGN_PATH = re.compile(r"docs/items/[^/\s'\"]+/(?:uml\b|data-structures\.md|messages\.md|performance\.md|baseline\.md)")


def bash_problems(command, base, branch):
    found = []
    if any(p in command for p in PROTECTED) and SHELL_WRITE.search(command):
        found.append(f"a shell command writes {', '.join(PROTECTED)}; the user changes that file")
    if lib.LOCK_NAME in command and SHELL_WRITE.search(command):
        found.append(f"a shell command writes {lib.LOCK_NAME}; use design.py after the user agrees")
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
    design = sorted(set(DESIGN_PATH.findall(command)))
    if design and SHELL_WRITE.search(command):
        reason = (f"this shell command changes design files ({', '.join(design)}). Shell writes are not recorded, "
                  f"so the result would count as the user's own edit; Claude should use Edit or Write instead")
        if data.get("permission_mode") in NO_PROMPT_MODES:
            sys.stderr.write(f"[flow] {reason}.\n")
            return 2
        emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                     "permissionDecisionReason": f"[flow] {reason}. Allow only if you want this change."}})
    if found:
        return problem(cfg, "PreToolUse", "; ".join(found) + ". Ask the user to run it if it is really needed.")
    return 0


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


def baseline_commands(root, cfg, item):
    """Commands that run the existing tests guarding the item's kept features (baseline.md)."""
    files = lib.baseline_test_files(root, item)
    spec = cfg.get("baseline_check")
    if not files or not spec:
        return []
    if isinstance(spec, str):
        spec = {"**/*": spec}
    groups = {}
    for f in files:
        for glob, cmd in spec.items():
            if lib.entry_matches(f, glob):
                groups.setdefault(cmd, []).append(f)
                break
    return [cmd.replace("{files}", " ".join(lib.shell_quote(f) for f in fs)) for cmd, fs in groups.items()]


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
    quoted = lib.shell_quote(item)
    commands = [c.replace("{item}", quoted) for c in checks or []]
    commands += baseline_commands(root, cfg, item)
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
            p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=left,
                               encoding="utf-8", errors="replace")
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
    if failures:
        state["gate_failures"] = int(state.get("gate_failures") or 0) + 1
    else:
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
             f"Stage {lib.item_stage(root, item, cfg)}, tier {spec.get('tier', '?')}, budget {level}, "
             f"clarify {spec.get('clarify', 'quick')}"
             + (f" (agents: {agents}; reading: {budget.get('read_limit')}; handoff at {budget.get('handoff_at')}% context)"
                if budget else ""),
             f"Claim: {len(claim.get('paths', []))} paths, hot: {', '.join(claim.get('hot', [])) or 'none'}",
             f"Last checks: {state.get('last_result', 'not run')} {', '.join(state.get('last_failures', []))}".rstrip()]
    design = lib.design_state(root, item)
    if design["status"] != "none":
        note = {"draft": "draft", "approved": f"approved {design['approved_at']}",
                "changed": "changed since approval: " + ", ".join(design["changed"])}[design["status"]]
        if design["hand_edited"]:
            note += "; edited by hand (authoritative, do not overwrite without asking): " + ", ".join(design["hand_edited"])
        lines.append(f"Design: {note}")
    audit = lib.read_audit(root, item)
    if audit:
        lines.append(f"Audit: round {audit.get('round', '?')} {audit.get('phase', '')}, verdict {audit.get('verdict', '?')}")
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
    handlers = {"pre-edit": pre_edit, "post-edit": post_edit, "pre-bash": pre_bash, "stop": stop,
                "session-start": session_start}
    if event not in handlers:
        sys.stderr.write(f"flow_hook: unknown event {event!r}\n")
        return 1
    try:
        try:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            data = {}
        start = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
        root = lib.repo_root(start)
        cfg = lib.load_config(root)
        if cfg is None:
            return 0
        if cfg.get("_error") and event in ("pre-edit", "pre-bash"):
            emit({"systemMessage": f"[flow warn] {cfg['_error']}"})
        code = handlers[event](data, root, cfg)
    except Exception as e:                            # a crash must not wave an edit through unchecked
        reason = f"[flow] the {event} hook failed ({type(e).__name__}: {e})"
        if event == "pre-edit":
            emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                         "permissionDecisionReason": reason + "; asking instead of guessing."}})
        else:
            emit({"systemMessage": reason})
        code = 0
    if _OUT:
        sys.stdout.write(json.dumps(_OUT))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
