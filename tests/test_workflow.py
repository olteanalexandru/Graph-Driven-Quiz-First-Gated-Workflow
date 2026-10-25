"""End-to-end checks for the scheduler, hooks, status line, coupling scan and bus generator.

Runs in a temporary folder; standard library only. Usage: python3 tests/test_workflow.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))
import flow_lib as lib  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="flow-test-"))
BASE = TMP / "wf-repo"
HOOK = PLUGIN / "hooks" / "flow_hook.py"
SCHED = PLUGIN / "scripts" / "schedule.py"
STATUS = PLUGIN / "templates" / "statusline.py"
failures = []


def sh(*args, cwd=BASE, check=True):
    p = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"command failed: {args}\n{p.stdout}\n{p.stderr}")
    return p


def sched(*args):
    return sh("python3", str(SCHED), *args, check=False)


def write(rel, text, root=BASE):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def expect(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def spec(item, status="approved", deps="[]", tier="M"):
    return f"---\nid: {item}\ntitle: t\nstatus: {status}\ntier: {tier}\nbudget: standard\ndeps: {deps}\n---\n# {item}\n"


def hook(event, payload, cwd=None):
    p = subprocess.run([sys.executable, str(HOOK), event], input=json.dumps(payload),
                       cwd=cwd or BASE, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def context_of(out):
    return json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "") if out else ""


# ---------------------------------------------------------------- plugin files

hooks_cfg = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
events = {h["command"].split()[-1] for groups in hooks_cfg["hooks"].values() for g in groups for h in g["hooks"]}
expect("hooks.json: every hook event has a handler",
       events == {"pre-edit", "post-edit", "pre-bash", "stop", "session-start"}, str(events))
expect("hooks.json: no PreCompact hook", "PreCompact" not in hooks_cfg["hooks"])

missing = []
for md in sorted(list((PLUGIN / "skills").rglob("*.md")) + list((PLUGIN / "agents").glob("*.md"))):
    text = md.read_text()
    refs = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", text) + \
        ["templates/" + r for r in re.findall(r"`templates/([\w./-]+)`", text)]
    for ref in refs:
        if not (PLUGIN / ref.rstrip(".,")).exists():
            missing.append(f"{md.relative_to(PLUGIN)}: {ref}")
expect("skills and agents name only files the plugin ships", not missing, "; ".join(missing))

# ---------------------------------------------------------------- claim entries

for path, entry, want in [
    ("src/a/x.ts", "src/a/", True),
    ("src/ab/x.ts", "src/a/", False),
    ("src/a/deep/x.ts", "src/a/*.ts", False),
    ("src/a/deep/x.ts", "src/a/**/*.ts", True),
    ("src/a/x.ts", "src/a/**/*.ts", True),
    ("src/b/x.ts", "src/{a,b}/x.ts", True),
    ("src/app/notes/[id]/page.tsx", "src/app/notes/[id]/", True),
    ("src/app/notes/[id]/page.tsx", "src/app/notes/[id]/page.tsx", True),
]:
    expect(f"claim entry {entry} {'covers' if want else 'does not cover'} {path}", lib.entry_matches(path, entry) == want)
for a, b, want in [
    ("src/a/**/*.ts", "src/a/", True),
    ("src/a/*.ts", "src/b/", False),
    ("src/{a,b}/x.ts", "src/b/x.ts", True),
    ("**/*.test.ts", "src/b/", True),
    ("src/a/", "src/a/one.ts", True),
    ("src/a/", "src/ab/", False),
]:
    expect(f"claim entries {a} and {b} {'overlap' if want else 'do not overlap'}",
           lib.entries_overlap(a, b) == want and lib.entries_overlap(b, a) == want)

# ---------------------------------------------------------------- repository with planned items

shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir(parents=True)
sh("git", "init", "-q", "-b", "main")
sh("git", "config", "user.email", "t@example.com")
sh("git", "config", "user.name", "t")
write("README.md", "x\n")
write("src/a/one.ts", "export const a = 1\n")
write("src/b/two.ts", "export const b = 2\n")
write("src/bus/dispatch.ts", "// generated\n")
write(".gitignore", ".flow/state/\n")
sh("git", "add", "-A")
sh("git", "commit", "-q", "-m", "base")

# Hooks must do nothing before init.
code, out, err = hook("pre-edit", {"tool_input": {"file_path": str(BASE / "src/a/one.ts")}, "cwd": str(BASE)})
expect("no config: hook is a no-op", code == 0 and out == "", out + err)

config = json.loads((PLUGIN / "templates" / "config.json").read_text())
config["mode"] = "enforce"
config["wip_limit"] = 3
write(".flow/config.json", json.dumps(config, indent=2))
write("docs/items/ITEM-001/spec.md", spec("ITEM-001"))
write("docs/items/ITEM-001/impact.json", json.dumps({"item": "ITEM-001", "paths": ["src/a/"], "hot": []}))
write("docs/items/ITEM-002/spec.md", spec("ITEM-002"))
write("docs/items/ITEM-002/impact.json", json.dumps({"item": "ITEM-002", "paths": ["src/b/"], "hot": ["bus-registry"]}))
write("docs/items/ITEM-003/spec.md", spec("ITEM-003", deps="[ITEM-002]"))
write("docs/items/ITEM-003/impact.json", json.dumps({"paths": ["src/a/one.ts", "hot:bus-registry"]}))
write("docs/items/ITEM-004/spec.md", spec("ITEM-004", status="draft"))
sh("git", "add", "-A")
sh("git", "commit", "-q", "-m", "plan")

p = sched("next")
expect("next: ITEM-002 (on the critical path) and ITEM-001 start together", "start now: ITEM-002, ITEM-001" in p.stdout, p.stdout)
expect("next: ITEM-003 waits for its dependency", "wait: ITEM-003: needs ITEM-002" in p.stdout, p.stdout)
p = sched("waves")
expect("waves: ITEM-003 after ITEM-002 and apart from ITEM-001",
       "wave 1: ITEM-002, ITEM-001" in p.stdout and "wave 2: ITEM-003" in p.stdout, p.stdout)
expect("waves: draft item without impact is listed", "ITEM-004" in p.stdout, p.stdout)

p = sched("claim", "ITEM-001")
expect("claim ITEM-001", p.returncode == 0, p.stdout)
p = sched("claim", "ITEM-002")
expect("claim ITEM-002", p.returncode == 0, p.stdout)
p = sched("claim", "ITEM-003")
expect("claim refused while a dependency is not done", p.returncode == 1 and "waits for ITEM-002" in p.stdout, p.stdout)
p = sched("status")
expect("status lists stages", "ITEM-001" in p.stdout and "active" in p.stdout, p.stdout)

# Items that exist only for the refusal checks below (not committed, removed afterwards).
extra = {
    "ITEM-005": (spec("ITEM-005", status="draft"), {"paths": ["src/e/"]}),
    "ITEM-006": (spec("ITEM-006"), {"paths": ["src/a/**/*.ts"], "hot": ["bus-registry"]}),
    "ITEM-007": (spec("ITEM-007"), {"paths": ["src/c/"]}),
    "ITEM-008": (spec("ITEM-008", deps="[ITEM-004]"), {"paths": ["src/d/"]}),
}
for item, (text, impact) in extra.items():
    write(f"docs/items/{item}/spec.md", text)
    write(f"docs/items/{item}/impact.json", json.dumps(impact))
write("docs/items/ITEM-009/spec.md", spec("ITEM-009"))
write("docs/items/ITEM-009/impact.json", "{not json")

p = sched("claim", "ITEM-005")
expect("claim refused for a spec that is not approved", p.returncode == 1 and "approve its spec first" in p.stdout, p.stdout)
p = sched("claim", "ITEM-006")
expect("claim refused on overlap: a glob inside a claimed folder, and a held hot token",
       p.returncode == 1 and "overlaps ITEM-001" in p.stdout and "overlaps ITEM-002: hot:bus-registry" in p.stdout, p.stdout)
p = sched("waves")
expect("waves: an item whose dependency is not planned is reported",
       "not placed: ITEM-008 waits for ITEM-004" in p.stdout, p.stdout)
p = sched("status")
expect("status reports a broken impact.json", "ITEM-009: impact.json is not valid JSON" in p.stdout, p.stdout)
p = sched("claim", "ITEM-009")
expect("claim refuses a broken impact.json", p.returncode == 2 and "not a valid impact file" in p.stdout, p.stdout)
config["wip_limit"] = 2
write(".flow/config.json", json.dumps(config, indent=2))
p = sched("claim", "ITEM-007")
expect("claim refused at the WIP limit", p.returncode == 1 and "WIP limit 2 reached" in p.stdout, p.stdout)
p = sched("claim", "ITEM-001")
expect("claiming again at the WIP limit refreshes the claim",
       p.returncode == 0 and "refreshed claim for ITEM-001" in p.stdout, p.stdout)
for item in [*extra, "ITEM-009"]:
    shutil.rmtree(BASE / "docs" / "items" / item)

# Work on ITEM-001 in its worktree, created with the same command /flow:build uses.
wt = TMP / "wf-repo-ITEM-001"
sh("bash", "-c", 'git worktree add -q "../$(basename "$PWD")-ITEM-001" -b ITEM-001')
expect("worktree created beside the repo on branch ITEM-001",
       sh("git", "branch", "--show-current", cwd=wt).stdout.strip() == "ITEM-001")


def edit(rel, cwd=wt, mode=None):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(cwd / rel)}, "cwd": str(cwd)}
    if mode:
        payload["permission_mode"] = mode
    return hook("pre-edit", payload, cwd)


def save_config():
    (wt / ".flow" / "config.json").write_text(json.dumps(config, indent=2))


code, out, err = edit("src/a/one.ts")
expect("edit inside claim allowed", code == 0, err)
code, out, err = edit("src/b/two.ts")
expect("edit in another item's claim blocked", code == 2 and "outside ITEM-001" in err, err)
code, out, err = edit("src/bus/dispatch.ts")
expect("hot file without its token blocked", code == 2 and "bus-registry" in err, err)
code, out, err = edit("tests/e2e/ITEM-001-flow.spec.ts")
expect("file named with the item ID allowed", code == 0, err)
code, out, err = edit("docs/items/ITEM-001/review.md")
expect("item docs allowed", code == 0, err)
code, out, err = edit(".flow/config.json")
expect("config protected in enforce mode", code == 2, err)
code, out, err = edit("../outside.txt")
expect("file outside the repo ignored", code == 0, err)


def bash(cmd, cwd=wt):
    return hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd)}, cwd)


for cmd, blocked in [
    ("git push --force origin ITEM-001", True),
    ("git push --force-with-lease origin ITEM-001", False),
    ("git -C . push --force origin ITEM-001", True),
    ("git -c core.hooksPath=/dev/null push origin main", True),
    ("git --git-dir=.git push origin main", True),
    ("git push origin main", True),
    ("git push origin HEAD:main", True),
    ("git push origin feature-main", False),
    ("git status && git reset --hard HEAD~1", True),
    ("FOO=1 git commit --no-verify -m x", True),
    ("git checkout -- .", True),
    ("git clean -fd", True),
    ("git branch -D ITEM-002", True),
    ("rm -rf node_modules", False),
    ("rm -rf /", True),
    ("echo '{}' > .flow/config.json", True),
    ("sed -i 's/enforce/warn/' .flow/config.json", True),
    ("python3 -c \"open('.flow/config.json', 'w').write('{}')\"", True),
    ("cat .flow/config.json", False),
    ("npx playwright test --grep @item:ITEM-001", False),
]:
    code, out, err = bash(cmd)
    expect(f"bash {'blocked' if blocked else 'allowed'}: {cmd}", (code == 2) == blocked, err)
code, out, err = bash("git push", cwd=BASE)
expect("bare git push on main blocked", code == 2, err)

# Warn mode reports instead of blocking.
config["mode"] = "warn"
save_config()
code, out, err = edit("src/b/two.ts")
warn = json.loads(out) if out else {}
expect("warn mode: allowed with a warning", code == 0 and "outside ITEM-001" in warn.get("systemMessage", ""), out + err)
expect("warn mode: Claude gets context", "additionalContext" in warn.get("hookSpecificOutput", {}), out)
config["mode"] = "enforce"
save_config()

# Status line: item and budget from the branch and spec, percentages as Claude Code sends them.
def status_line(payload):
    p = subprocess.run([sys.executable, str(STATUS)], input=json.dumps(payload), capture_output=True, text=True)
    return p.stdout.strip()


line = status_line({"cwd": str(wt), "model": {"display_name": "Sonnet 5.5"}, "context_window": {"used_percentage": 1},
                    "prompt_cache": {"hit_ratio": 0.91}, "cost": {"total_cost_usd": 1.842}})
expect("status line: item, budget, 1% context, cache ratio, cost",
       line == "ITEM-001 standard | Sonnet 5.5 | ctx 1% | cache 91% | $1.84", line)
line = status_line({"cwd": str(wt), "context_window": {"used_percentage": 63}})
expect("status line: handoff prompt past the budget's limit", line == "ITEM-001 standard | ctx 63% -> /flow:handoff", line)


def stop():
    code, out, err = hook("stop", {"stop_hook_active": False, "cwd": str(wt)}, wt)
    return (json.loads(out) if out else {}), out + err


# Stop hook runs the checks of the item's budget level (the spec says budget: standard).
config["budgets"]["standard"]["stop_checks"] = ["test -f src/a/one.ts", "echo 'AC1 failed for {item}' && false"]
save_config()
res, raw = stop()
expect("stop: failing budget check blocks with reason",
       res.get("decision") == "block" and "AC1 failed for ITEM-001" in res.get("reason", ""), raw)
code, out, err = hook("stop", {"stop_hook_active": True, "cwd": str(wt)}, wt)
expect("stop: no loop when stop_hook_active", out == "" and code == 0, out)
# Without budgets in the config, the top-level stop_checks apply.
del config["budgets"]
config["stop_checks"] = ["echo 'top-level check failed' && false"]
save_config()
res, raw = stop()
expect("stop: falls back to top-level stop_checks", "top-level check failed" in res.get("reason", ""), raw)
config["stop_checks"] = ["true"]
save_config()
res, raw = stop()
expect("stop: passing checks let Claude finish", res == {}, raw)
state = json.loads((wt / ".flow" / "state" / "ITEM-001.json").read_text())
expect("stop: green fingerprint saved", state.get("last_result") == "pass" and state.get("green"), str(state))

# A change inside an untracked file must rerun the checks.
write("src/a/new.ts", "ok\n", root=wt)
config["stop_checks"] = ["! grep -q BAD src/a/new.ts"]
save_config()
res, raw = stop()
expect("stop: new untracked file passes", res == {}, raw)
(wt / "src/a/new.ts").write_text("BAD\n")
res, raw = stop()
expect("stop: editing an untracked file reruns the checks", res.get("decision") == "block", raw)
(wt / "src/a/new.ts").unlink()

# A check whose tool is not installed (exit 127) is reported, not treated as a failure.
config["stop_checks"] = ["definitely-not-installed-xyz --run"]
save_config()
res, raw = stop()
expect("stop: missing tool warns instead of blocking",
       "decision" not in res and "Not run" in res.get("systemMessage", ""), raw)

# Session start (after /clear or compaction) puts the item's state back.
(wt / "docs/items/ITEM-001/handoff.md").write_text("# Handoff\nNext: make AC2 pass.\n")
for source in ("compact", "clear"):
    code, out, err = hook("session-start", {"source": source, "cwd": str(wt)}, wt)
    ctx = context_of(out)
    expect(f"session-start ({source}) restores stage, claim and handoff",
           "Stage S" in ctx and "Claim:" in ctx and "make AC2 pass" in ctx, out + err)
code, out, err = hook("session-start", {"source": "startup", "cwd": str(BASE)}, BASE)
expect("session-start on base branch stays silent", out == "", out)

# A config that is not valid JSON is reported, never silently ignored.
good = (wt / ".flow" / "config.json").read_text()
(wt / ".flow" / "config.json").write_text("{ not json")
code, out, err = edit("src/a/one.ts")
msg = json.loads(out).get("systemMessage", "") if out else ""
expect("invalid config: edit goes on with a warning", code == 0 and "not valid JSON" in msg, out + err)
code, out, err = hook("session-start", {"source": "startup", "cwd": str(wt)}, wt)
expect("invalid config: session start reports it", "Workflow config problem" in context_of(out), out + err)
(wt / ".flow" / "config.json").write_text(good)


# ---------------------------------------------------------------- editable design (lock, guard, approval)

DESIGN = PLUGIN / "scripts" / "design.py"
TRACE = PLUGIN / "scripts" / "trace.py"
D1 = wt / "docs/items/ITEM-001"
UML = "docs/items/ITEM-001/uml/sequence-award.mmd"
config["stop_checks"] = ["true"]
save_config()


def design(*args):
    return sh("python3", str(DESIGN), *args, cwd=wt, check=False)


def post(rel, cwd=wt):
    return hook("post-edit", {"tool_name": "Write", "tool_input": {"file_path": str(cwd / rel)}, "cwd": str(cwd)}, cwd)


def decision(out):
    return (json.loads(out).get("hookSpecificOutput") or {}) if out else {}


code, out, err = edit(UML)
expect("design guard: a new draft file is allowed", code == 0 and "permissionDecision" not in out, out + err)
write(UML, "sequenceDiagram\n  participant C as XpCard\n  C->>C: award (D1)\n", root=wt)
post(UML)
lock = json.loads((D1 / "design.lock.json").read_text())
expect("design post-edit records the hash", "uml/sequence-award.mmd" in lock["files"], str(lock))
code, out, err = edit(UML)
expect("design guard: a file Claude wrote last is allowed", code == 0 and "permissionDecision" not in out, out)
write(UML, (D1 / "uml/sequence-award.mmd").read_text() + "  Note over C: the user's change\n", root=wt)
code, out, err = edit(UML)
dec = decision(out)
expect("design guard: hand edit asks", code == 0 and dec.get("permissionDecision") == "ask"
       and "changed outside Claude" in dec.get("permissionDecisionReason", ""), out + err)
code, out, err = edit(UML, mode="bypassPermissions")
expect("design guard: bypass denies and says how to unlock", code == 2 and "design.py unlock ITEM-001" in err, err)
p = design("status", "ITEM-001")
expect("design.py status lists the hand edit", "edited by hand: uml/sequence-award.mmd" in p.stdout, p.stdout)
design("unlock", "ITEM-001", "uml/sequence-award.mmd")
code, out, err = edit(UML, mode="bypassPermissions")
expect("design guard: unlock allows the edit", code == 0, err)
post(UML)
expect("design post-edit uses up the unlock", json.loads((D1 / "design.lock.json").read_text())["unlocked"] == [])
code, out, err = edit(UML)
expect("design guard: after Claude's write the file is a draft again", "permissionDecision" not in out, out)

p = design("approve", "ITEM-001")
expect("design.py approve", p.returncode == 0 and "approved the design of ITEM-001" in p.stdout, p.stdout + p.stderr)
code, out, err = edit(UML)
dec = decision(out)
expect("design guard: approved design asks", dec.get("permissionDecision") == "ask"
       and "was approved" in dec.get("permissionDecisionReason", ""), out)
code, out, err = edit("docs/items/ITEM-001/data-structures.md")
expect("design guard: a new file in an approved design asks", decision(out).get("permissionDecision") == "ask", out)
write(UML, (D1 / "uml/sequence-award.mmd").read_text() + "  C->>C: refresh (D1)\n", root=wt)
post(UML)
p = design("status", "ITEM-001", "--json")
expect("design post-edit keeps approval (the edit went through the prompt)", json.loads(p.stdout)["status"] == "approved", p.stdout)
write(UML, (D1 / "uml/sequence-award.mmd").read_text().replace("refresh", "reload"), root=wt)
st = json.loads(design("status", "ITEM-001", "--json").stdout)
expect("design: a hand edit after approval shows as changed",
       st["status"] == "changed" and st["changed"] == ["uml/sequence-award.mmd"], str(st))
write(UML, (D1 / "uml/sequence-award.mmd").read_text().replace("\n", "\r\n"), root=wt)
st2 = json.loads(design("status", "ITEM-001", "--json").stdout)
expect("design: CRLF line endings are not an edit", st2["files"] == st["files"], str(st2))

code, out, err = edit("docs/items/ITEM-001/design.lock.json")
expect("design lock: Claude's edit tools may not change it", code == 2 and "design.py approve" in err, err)
code, out, err = bash("echo '{}' > docs/items/ITEM-001/design.lock.json")
expect("design lock: shell writes blocked", code == 2, err)
code, out, err = bash("cat docs/items/ITEM-001/design.lock.json 2>/dev/null | head -5; git log -1 2>&1 >&2")
expect("design lock: reads with 2>/dev/null or 2>&1 are allowed", code == 0 and out == "", out + err)
code, out, err = bash("sed -i 's/reload/refresh/' docs/items/ITEM-001/uml/sequence-award.mmd")
expect("design guard: a shell write to a design file asks", code == 0 and decision(out).get("permissionDecision") == "ask"
       and "not recorded" in decision(out).get("permissionDecisionReason", ""), out + err)
code, out, err = hook("pre-bash", {"tool_name": "Bash", "permission_mode": "bypassPermissions", "cwd": str(wt),
                                   "tool_input": {"command": "git checkout -- docs/items/ITEM-001/uml/"}}, wt)
expect("design guard: restoring design files from git is denied in bypass mode", code == 2 and "design files" in err, err)
code, out, err = bash("cat docs/items/ITEM-001/uml/sequence-award.mmd")
expect("design guard: reading a design file in the shell is allowed", code == 0 and out == "", out + err)
lock_text = (D1 / "design.lock.json").read_text()
for broken in ("null", '{"files": null}', '{"approved": true}', '{"files": {"uml/a.mmd": 3}}'):
    (D1 / "design.lock.json").write_text(broken)
    code, out, err = edit(UML)
    expect(f"design guard: a lock shaped {broken} still asks", code == 0 and decision(out).get("permissionDecision") == "ask", out + err)
(D1 / "design.lock.json").write_text(lock_text)

spec_path = D1 / "spec.md"
spec_before = spec_path.read_text()
spec_path.write_text(spec_before.replace("status: approved\n", "status: approved\nclarify: deep\n"))
code, out, err = edit("src/a/one.ts")
expect("design gate: code edit waits for approval (deep clarification)",
       code == 2 and "changed since it was approved" in err, err)
p = design("status", "ITEM-001", "--gate")
expect("design.py status --gate fails while approval is missing", p.returncode == 1, p.stdout)
expect("stage: S2 while the design waits for approval", lib.item_stage(wt, "ITEM-001") == "S2", lib.item_stage(wt, "ITEM-001"))
code, out, err = hook("session-start", {"source": "resume", "cwd": str(wt)}, wt)
expect("session-start shows the design state", "Design: changed since approval" in context_of(out), out)
design("approve", "ITEM-001")
code, out, err = edit("src/a/one.ts")
expect("design gate: code edit allowed once approved", code == 0, err)
config["design_approval"] = "never"
save_config()
write(UML, (D1 / "uml/sequence-award.mmd").read_text() + "  C->>C: again (D1)\n", root=wt)
code, out, err = edit("src/a/one.ts")
expect("design gate: design_approval never skips it", code == 0, err)
config["design_approval"] = "deep"
save_config()
design("approve", "ITEM-001")

# ---------------------------------------------------------------- trace: spec, design, baseline, budgets, tests

spec_path.write_text(spec_before.replace("status: approved\n", "status: approved\nclarify: deep\n") + """
## Decisions
| ID | Question | Chosen | Rejected | Why |
|----|----------|--------|----------|-----|
| D1 | Award on finish | yes | no | simple |
| D2 | Drop the old badge | yes | no | unused |
| D3 | Unused decision | x | y | z |

## Assumptions
| ID | Assumption | Basis | If wrong |
|----|------------|-------|----------|
| A1 | Users are signed in | route guard | add a sign-in AC |

## Acceptance criteria
- AC1 (D1, A1): Given a finished exercise, when it is graded, then XP is shown.
- AC2: Given nothing, when nothing, then nothing.
- AC3 (D9): Given a ghost decision, when checked, then it is reported.
- AC4 (U1): Given the audit finding, when fixed, then it is gone.

## Open questions
- Which currency do we show?
""")
write("docs/items/ITEM-001/uml/sequence-award.mmd",
      "sequenceDiagram\n  %% external: Supabase\n  actor U as User\n  participant C as XpCard\n"
      "  participant H as handleResetXp\n  participant S as Supabase\n  C->>H: reset (D1, D7)\n", root=wt)
post("docs/items/ITEM-001/uml/sequence-award.mmd")
write("src/a/XpCard.tsx", "export function XpCard() { return null }\n", root=wt)
write("tests/e2e/existing.spec.ts", "test('shows the profile', () => {})\n", root=wt)
write("docs/items/ITEM-001/baseline.md", """# ITEM-001 baseline
| ID | Feature | Where | Tests | Plan |
|----|---------|-------|-------|------|
| F1 | Shows the profile | src/a/one.ts | tests/e2e/existing.spec.ts:1 | keep |
| F2 | Shows the streak | src/a/one.ts | none | keep |
| F3 | Old badge | src/a/one.ts | none | change |
| F4 | Legacy menu | src/a/one.ts | none | remove (D2) |
""", root=wt)
write("docs/items/ITEM-001/performance.md", """# ITEM-001 workload
| ID | Path | Metric | Budget | Measured by |
|----|------|--------|--------|-------------|
| PB1 | grade answer | INP | 100 ms | PB1 test |
""", root=wt)
write("docs/items/ITEM-001/audit.md", """---
item: ITEM-001
kind: ux
phase: verify
round: 2
verdict: FAIL
---
# audit
| ID | Finding | Severity | Status |
|----|---------|----------|--------|
| U1 | Focus is invisible | 4 critical | open |
| U2 | Label wording | 1 | open |
""", root=wt)
write("tests/e2e/ITEM-001-flow.spec.ts",
      "test('AC1 shows XP @item:ITEM-001', () => {})\ntest('AC9 ghost @item:ITEM-001', () => {})\n", root=wt)
write("tests/e2e/ITEM-0010-other.spec.ts", "test('AC2 belongs to another item @item:ITEM-0010', () => {})\n", root=wt)
design("approve", "ITEM-001")


def trace(*args):
    p = sh("python3", str(TRACE), "check", "ITEM-001", "--json", *args, cwd=wt, check=False)
    return p.returncode, json.loads(p.stdout or "{}")


code, res = trace("--stage", "plan")
errs = "\n".join(res.get("errors", []))
expect("trace plan: AC without a citation", "AC2 cites no decision" in errs, errs)
expect("trace plan: AC citing an unknown decision", "AC3 cites D9" in errs, errs)
expect("trace plan: AC may cite an audit finding", "AC4" not in errs, errs)
expect("trace plan: design cites an unknown decision", "uml/sequence-award.mmd cites D7" in errs, errs)
expect("trace plan: a change row needs a decision", "baseline F3" in errs and "F4" not in errs, errs)
expect("trace plan: open questions block a deep spec", "open question" in errs, errs)
expect("trace plan: no test checks yet", "has no test" not in errs and code == 1, errs)
expect("trace plan: unused decision is a warning", any("D3 is not cited" in w for w in res.get("warnings", [])), str(res))
code, res = trace("--stage", "build")
errs, warns = "\n".join(res.get("errors", [])), "\n".join(res.get("warnings", []))
expect("trace build: AC without a test (another item's @item:ITEM-0010 does not count)",
       "AC2 has no test" in errs and "AC1 has no test" not in errs and "ITEM-0010" not in errs, errs)
expect("trace build: kept feature without a test", "baseline F2 (keep) has no test" in errs and "F1 (keep)" not in errs, errs)
expect("trace build: budget without a test", "performance budget PB1 has no test" in errs, errs)
expect("trace build: test for an unknown AC", "test AC9" in errs, errs)
expect("trace build: participant missing from the code",
       "handleResetXp" in warns and "XpCard" not in warns and "Supabase" not in warns and "User" not in warns, warns)
code, res = trace("--stage", "review")
errs = "\n".join(res.get("errors", []))
expect("trace review: open critical finding", "audit finding U1" in errs and "U2" not in errs, errs)

expect("stage: S6a while the audit fails", lib.item_stage(wt, "ITEM-001") == "S6a", lib.item_stage(wt, "ITEM-001"))
(D1 / "audit.md").write_text((D1 / "audit.md").read_text().replace("verdict: FAIL", "verdict: PASS"))
expect("stage: S7 once the audit passes", lib.item_stage(wt, "ITEM-001") == "S7", lib.item_stage(wt, "ITEM-001"))

# The Stop hook runs the tests that guard the kept features.
config["baseline_check"] = {"**/*.spec.ts": "echo BASELINE {files} && false"}
save_config()
res, raw = stop()
expect("stop: baseline tests run", res.get("decision") == "block" and "BASELINE tests/e2e/existing.spec.ts" in res.get("reason", ""), raw)
state = json.loads((wt / ".flow" / "state" / "ITEM-001.json").read_text())
expect("stop: gate failures are counted", state.get("gate_failures", 0) >= 1, str(state))
config["baseline_check"] = {}
save_config()

# Back to a clean item for the rest of the run.
for name in ("baseline.md", "performance.md", "audit.md", "design.lock.json"):
    (D1 / name).unlink()
shutil.rmtree(D1 / "uml")
spec_path.write_text(spec_before.replace("status: approved\n", "status: approved\nclarify: deep\n"))
code, out, err = edit("src/a/one.ts")
expect("design gate: no design at all still waits for approval", code == 2 and "has not been written or approved" in err, err)
spec_path.write_text(spec_before)
for rel in ("src/a/XpCard.tsx", "tests/e2e/existing.spec.ts", "tests/e2e/ITEM-001-flow.spec.ts",
            "tests/e2e/ITEM-0010-other.spec.ts"):
    (wt / rel).unlink()

p = sched("release", "ITEM-001")
p = sched("next")
expect("after release, ITEM-001 can start again and ITEM-003 still waits",
       "start now: ITEM-001" in p.stdout and "wait: ITEM-003: needs ITEM-002" in p.stdout, p.stdout)


# ---------------------------------------------------------------- token-aware scheduling (contracts, context, budget)

orch = TMP / "orch-repo"
orch.mkdir()
sh("git", "init", "-q", "-b", "main", cwd=orch)
sh("git", "config", "user.email", "t@example.com", cwd=orch)
sh("git", "config", "user.name", "t", cwd=orch)
ocfg = json.loads((PLUGIN / "templates" / "config.json").read_text())
ocfg.update({"mode": "enforce", "wip_limit": 3})
write(".flow/config.json", json.dumps(ocfg), root=orch)
for item, impact, budget in [
    ("ITEM-010", {"paths": ["src/x/"], "contracts": {"changes": ["bus:progress/awardXp"]}, "nodes": ["a", "b", "c", "d"]}, "standard"),
    ("ITEM-011", {"paths": ["src/y/"], "contracts": {"uses": ["bus:progress/*"]}, "nodes": ["e", "f"]}, "standard"),
    ("ITEM-012", {"paths": ["src/z/"], "nodes": ["a", "b", "c", "x"]}, "standard"),
    ("ITEM-013", {"paths": ["src/w/"], "nodes": ["q"]}, "deep"),
    ("ITEM-014", {"paths": ["src/v/"], "contracts": {"changes": ["api:/xp"]}}, "standard"),
    ("ITEM-015", {"paths": ["src/u/"], "contracts": {"uses": ["api:/xp"]}}, "standard"),
]:
    deps = "[ITEM-099]" if item == "ITEM-014" else "[]"
    write(f"docs/items/{item}/spec.md", spec(item, deps=deps).replace("budget: standard", f"budget: {budget}"), root=orch)
    write(f"docs/items/{item}/impact.json", json.dumps(impact), root=orch)
write("README.md", "x\n", root=orch)
sh("git", "add", "-A", cwd=orch)
sh("git", "commit", "-q", "-m", "plan", cwd=orch)


def osched(*args):
    return sh("python3", str(SCHED), *args, cwd=orch, check=False)


p = osched("next")
expect("next: the contract user waits for the item that changes the contract",
       "wait: ITEM-011: uses a contract that ITEM-010 changes" in p.stdout, p.stdout)
expect("next: shared context serializes", "wait: ITEM-012: run after ITEM-010: shares 3 of 4 graph nodes" in p.stdout, p.stdout)
expect("next: independent items start together", "start now: ITEM-010, ITEM-013" in p.stdout, p.stdout)
expect("next: a contract user waits even while the changer waits for its own dependency",
       "wait: ITEM-015: uses a contract that ITEM-014 changes" in p.stdout, p.stdout)
p = osched("waves")
expect("waves: contract user and context sharer go to wave 2",
       "wave 1: ITEM-010, ITEM-013" in p.stdout and "wave 2: ITEM-011, ITEM-012" in p.stdout, p.stdout)
p = osched("claim", "ITEM-010")
expect("contracts: claim ITEM-010", p.returncode == 0 and "contracts: bus:progress/awardXp" in p.stdout, p.stdout)
p = osched("claim", "ITEM-011")
expect("contracts: claim refused when the other item changes a contract this one uses",
       p.returncode == 1 and "contract bus:progress/awardXp ~ bus:progress/*" in p.stdout, p.stdout)
p = osched("claim", "ITEM-012")
expect("claim: shared context is a note, not a refusal", p.returncode == 0 and "note: ITEM-010 shares" in p.stdout, p.stdout)
claim10 = json.loads((orch / ".git/flow-claims/ITEM-010.json").read_text())
expect("claim: items that ran at the same time are recorded", claim10.get("concurrent") == ["ITEM-012"], str(claim10))

ocfg["orchestration"]["cost_budget_usd"] = 10
write(".flow/config.json", json.dumps(ocfg), root=orch)
p = osched("next")
expect("capacity: without usage data active items count at 1.5x their estimate",
       "no live data" in p.stdout and "expected to need $24.00" in p.stdout, p.stdout)
expect("capacity: a start that would pass the budget waits", "wait: ITEM-013: budget: needs about $25.00" in p.stdout, p.stdout)
p = osched("claim", "ITEM-013")
expect("capacity: claim refused past the budget without --force", p.returncode == 1 and "--force" in p.stdout, p.stdout)

# The status line keeps the usage ledger the scheduler reads.
sh("git", "checkout", "-q", "-b", "ITEM-010", cwd=orch)
future = int(__import__("time").time()) + 3600
line = status_line({"cwd": str(orch), "session_id": "s-1", "cost": {"total_cost_usd": 7.5},
                    "context_window": {"used_percentage": 30},
                    "rate_limits": {"five_hour": {"used_percentage": 40, "resets_at": future}}})
ledger = orch / ".git/flow-usage/s-1.json"
rec = json.loads(ledger.read_text()) if ledger.exists() else {}
expect("status line: usage ledger", rec.get("item") == "ITEM-010" and rec.get("cost_usd") == 7.5
       and rec.get("five_hour_pct") == 40, str(rec))
stamp = rec.get("updated")
status_line({"cwd": str(orch), "session_id": "s-1", "cost": {"total_cost_usd": 7.5},
             "context_window": {"used_percentage": 30},
             "rate_limits": {"five_hour": {"used_percentage": 40, "resets_at": future}}})
expect("status line: ledger untouched when nothing changed", json.loads(ledger.read_text()).get("updated") == stamp)
ocfg["orchestration"]["cost_budget_usd"] = 100
write(".flow/config.json", json.dumps(ocfg), root=orch)
p = osched("next")
expect("capacity: live usage replaces the conservative estimate",
       "no live data" not in p.stdout and "spent $7.50" in p.stdout and "start now: ITEM-013" in p.stdout, p.stdout)
write(".git/flow-usage/s-2.json", json.dumps({"session": "s-2", "item": None, "cost_usd": 1,
                                               "five_hour_pct": 85, "five_hour_resets_at": future,
                                               "updated": future - 3000}), root=orch)
p = osched("next")
expect("capacity: the 5-hour floor stops new starts", "wait: ITEM-013: 5-hour window at 85%" in p.stdout, p.stdout)
(orch / ".git/flow-usage/s-2.json").unlink()
for n, cost in enumerate([4, 5, 6]):
    write(f"docs/items/ITEM-09{n}/metrics.json", json.dumps({"item": f"ITEM-09{n}", "budget": "standard", "cost_usd": cost}), root=orch)
p = osched("usage")
expect("estimates: median of shipped items once there are 3", "estimate standard: $5.00 (median of 3 shipped)" in p.stdout
       and "estimate deep: $25.00 (config)" in p.stdout, p.stdout)

# Metrics for an item, written before merge.
write("src/x/a.ts", "1\n", root=orch)
sh("git", "add", "-A", cwd=orch)
sh("git", "commit", "-q", "-m", "ITEM-010: build", cwd=orch)
for n in range(2):
    write("src/x/a.ts", f"{n + 2}\n", root=orch)
    sh("git", "commit", "-q", "-am", "ITEM-010: review fixes", cwd=orch)
write(".flow/state/ITEM-010.json", json.dumps({"gate_failures": 3}), root=orch)
p = osched("record", "ITEM-010")
m = json.loads((orch / "docs/items/ITEM-010/metrics.json").read_text())
expect("record: cost, rework, gate failures and partners",
       m["cost_usd"] == 7.5 and m["rework_rounds"]["review"] == 2 and m["gate_failures"] == 3
       and m["parallel_with"] == ["ITEM-012"] and m["usage_data"] == "live", str(m))

# ---------------------------------------------------------------- integration gate

ir = TMP / "int-repo"
ir.mkdir()
sh("git", "init", "-q", "-b", "main", cwd=ir)
sh("git", "config", "user.email", "t@example.com", cwd=ir)
sh("git", "config", "user.name", "t", cwd=ir)
icfg = json.loads((PLUGIN / "templates" / "config.json").read_text())
icfg.update({"mode": "warn", "wip_limit": 3,
             "integration_checks": ["grep -q 'a = 2' src/a.ts && grep -q 'b = 3' src/b.ts", "echo {grep}"]})
write(".flow/config.json", json.dumps(icfg), root=ir)
write("src/a.ts", "export const a = 1\n", root=ir)
write("src/b.ts", "export const b = 1\n", root=ir)
write("README.md", "base\n", root=ir)
for item, path in [("ITEM-020", "src/a.ts"), ("ITEM-021", "src/b.ts")]:
    write(f"docs/items/{item}/spec.md", spec(item), root=ir)
    write(f"docs/items/{item}/impact.json", json.dumps({"paths": [path]}), root=ir)
    write(f"docs/items/{item}/uml/sequence-main.mmd", "sequenceDiagram\n", root=ir)
sh("git", "add", "-A", cwd=ir)
sh("git", "commit", "-q", "-m", "plan", cwd=ir)
INTEG = PLUGIN / "scripts" / "integrate.py"
iwt = {}
for item, path, value in [("ITEM-020", "src/a.ts", "export const a = 2\n"), ("ITEM-021", "src/b.ts", "export const b = 3\n")]:
    sh("python3", str(SCHED), "claim", item, cwd=ir)
    iwt[item] = TMP / f"int-repo-{item}"
    sh("git", "worktree", "add", "-q", str(iwt[item]), "-b", item, cwd=ir)
    write(path, value, root=iwt[item])
    write(f"docs/items/{item}/review.md", "Verdict: PASS\n", root=iwt[item])
    sh("git", "add", "-A", cwd=iwt[item])
    sh("git", "commit", "-q", "-m", f"{item}: build", cwd=iwt[item])


def integ(*args, cwd=ir):
    return sh("python3", str(INTEG), *args, cwd=cwd, check=False)


p = integ("verify", "ITEM-020", cwd=iwt["ITEM-020"])
expect("integrate: ship waits while another finished item is not integrated",
       p.returncode == 1 and "ITEM-021" in p.stdout, p.stdout)
expect("stage: S7a when another claimed item also passed review",
       lib.item_stage(iwt["ITEM-020"], "ITEM-020") == "S7a", lib.item_stage(iwt["ITEM-020"], "ITEM-020"))
p = integ("run")
run_id = next((ln.split()[2].rstrip(":") for ln in p.stdout.splitlines() if ln.startswith("integration run")), "")
expect("integrate: run merges both reviewed items and runs the checks",
       p.returncode == 0 and "ITEM-020, ITEM-021" in p.stdout and "'@item:ITEM-020|@item:ITEM-021'" in p.stdout, p.stdout)
rec = json.loads((ir / f".git/flow-integration/{run_id}.json").read_text()) if run_id else {}
expect("integrate: the run waits for the combined review", rec.get("verdict") == "REVIEW" and Path(rec.get("worktree", "/nonexistent")).exists(), str(rec))
p = integ("finish", run_id, "--verdict", "PASS")
rec = json.loads((ir / f".git/flow-integration/{run_id}.json").read_text())
expect("integrate: finish records PASS and removes the worktree",
       p.returncode == 0 and rec["verdict"] == "PASS" and rec["worktree"] is None, p.stdout + str(rec))
p = integ("verify", "ITEM-020", cwd=iwt["ITEM-020"])
expect("integrate: verify passes for the integrated head", p.returncode == 0, p.stdout)
expect("stage: S8 once integrated", lib.item_stage(iwt["ITEM-020"], "ITEM-020") == "S8", lib.item_stage(iwt["ITEM-020"], "ITEM-020"))
write("src/a.ts", "export const a = 2 // later\n", root=iwt["ITEM-020"])
sh("git", "commit", "-q", "-am", "ITEM-020: review fixes", cwd=iwt["ITEM-020"])
p = integ("verify", "ITEM-020", cwd=iwt["ITEM-020"])
expect("integrate: a new commit needs a new run", p.returncode == 1, p.stdout)
icfg["integration_checks"] = ["true"]
write(".flow/config.json", json.dumps(icfg), root=ir)
p = integ("run", "ITEM-020")
solo = next((ln.split()[2].rstrip(":") for ln in p.stdout.splitlines() if ln.startswith("integration run")), "")
integ("finish", solo, "--verdict", "PASS")
p = integ("verify", "ITEM-020", cwd=iwt["ITEM-020"])
expect("integrate: a run without the partner does not count", p.returncode == 1 and "ITEM-021" in p.stdout, p.stdout)
icfg["integration_checks"] = ["echo cross-component failure && false"]
write(".flow/config.json", json.dumps(icfg), root=ir)
p = integ("run")
expect("integrate: a failing check blocks", p.returncode == 1 and "FAIL" in p.stdout and "cross-component failure" in p.stdout, p.stdout)
for item in ("ITEM-020", "ITEM-021"):
    write("README.md", f"changed by {item}\n", root=iwt[item])
    sh("git", "commit", "-q", "-am", f"{item}: readme", cwd=iwt[item])
p = integ("run", "ITEM-020", "ITEM-021")
expect("integrate: a merge conflict blocks and names the file", p.returncode == 1 and "CONFLICT merging ITEM-021: README.md" in p.stdout, p.stdout)
leftover = [d for d in TMP.iterdir() if "integration" in d.name]
expect("integrate: blocked runs leave no worktree behind", not leftover, str(leftover))


# ---------------------------------------------------------------- UX probe (needs Node and Playwright)

def playwright_available():
    if not shutil.which("node"):
        return False
    probe = "const r=require('module').createRequire(process.cwd()+'/x.js');try{r('playwright')}catch(e){r('@playwright/test')}"
    return subprocess.run(["node", "-e", probe], cwd=TMP, capture_output=True).returncode == 0


if playwright_available():
    import functools
    import http.server
    import threading
    site = TMP / "probe-site"
    (site / "api").mkdir(parents=True)
    (site / "index.html").write_text("""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><style>
.wide{width:600px;height:10px} #plain:focus{outline:none} .tiny{display:inline-block;width:16px;height:16px}
</style></head><body><a href="#main">Skip</a>
<section data-testid="quiz"><h1>Intervals</h1><label for="answer">Answer</label><input id="answer">
<button id="icon"><svg width="20" height="20"></svg></button><button id="plain">Check</button>
<a class="tiny" href="#help">?</a><div class="wide"></div><p id="status">Loading...</p></section>
<script>fetch('/api/items').then(r => { if (!r.ok) throw new Error('bad'); return r.json() })
.then(i => { document.getElementById('status').textContent = i.length ? i.length + ' items' : 'No items yet' })
.catch(() => { document.getElementById('status').textContent = 'Something went wrong' })</script></body></html>""")
    (site / "api" / "items").write_text("[1, 2]")
    handler = functools.partial(type("Quiet", (http.server.SimpleHTTPRequestHandler,),
                                     {"log_message": lambda *a: None}), directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    states = TMP / "ux-states.json"
    states.write_text(json.dumps({
        "base_url": f"http://127.0.0.1:{server.server_address[1]}", "root": "[data-testid=quiz]",
        "viewports": [[375, 667], [1280, 800]],
        "states": [{"name": "default", "path": "/"},
                   {"name": "empty", "path": "/", "mock": [{"url": "**/api/items", "action": "json", "body": []}]},
                   {"name": "error", "path": "/", "mock": [{"url": "**/api/items", "action": "status", "status": 500}]},
                   {"name": "loading", "path": "/", "mock": [{"url": "**/api/items", "action": "hang"}]},
                   {"name": "unreachable", "url": "http://127.0.0.1:9/"}]}))
    p = sh("node", str(PLUGIN / "scripts" / "ux_probe.mjs"), "--states", str(states), "--out", str(TMP / "probe-out"),
           cwd=TMP, check=False)
    server.shutdown()
    report = json.loads((TMP / "probe-out" / "report.json").read_text()) if (TMP / "probe-out" / "report.json").exists() else {}
    by = {(s["name"], s["viewport"][0]): s for s in report.get("states", []) if "error" not in s}
    failed = [s for s in report.get("states", []) if "error" in s]
    expect("ux probe: every state at every viewport", p.returncode == 0 and len(by) == 8, p.stdout + p.stderr)
    expect("ux probe: an unreachable state is recorded and the rest still run",
           [s["name"] for s in failed] == ["unreachable", "unreachable"], str(failed))
    expect("ux probe: mocks reach the loading, empty and error states",
           "Loading..." in by.get(("loading", 375), {}).get("text", "") and "No items yet" in by.get(("empty", 375), {}).get("text", "")
           and "Something went wrong" in by.get(("error", 375), {}).get("text", ""), str(by.keys()))
    d375 = by.get(("default", 375), {})
    expect("ux probe: unnamed control", d375.get("unnamed") == ["button#icon"], str(d375.get("unnamed")))
    expect("ux probe: target under 24x24", any(t.startswith("a 16x16") for t in d375.get("smallTargets", [])), str(d375))
    expect("ux probe: horizontal overflow on mobile and at 320 px",
           d375.get("overflowX") is True and by.get(("default", 1280), {}).get("overflowX") is False
           and report.get("reflow", {}).get("overflowX") is True, str(report.get("reflow")))
    kb = report.get("keyboard", {})
    expect("ux probe: tab order and focus without a visible indicator",
           kb.get("order", [])[:1] == ['input#answer "Answer"'] and kb.get("noVisibleFocus") == ['button#plain "Check"']
           and kb.get("trap") is False, str(kb))
    expect("ux probe: screenshots written", by and all(Path(s["screenshot"]).exists() for s in by.values()))
else:
    print("skip ux probe tests (Node with Playwright not available)")

# ---------------------------------------------------------------- coupling scan

# TypeScript: two features importing each other through a cycle, one shared bus.
cr = TMP / "coupling-repo"
(cr / "src/features/ear").mkdir(parents=True)
(cr / "src/features/progress").mkdir(parents=True)
(cr / "src/bus").mkdir(parents=True)
(cr / ".flow").mkdir()
sh("git", "init", "-q", "-b", "main", cwd=cr)
(cr / "src/features/ear/grade.ts").write_text(
    "import { award } from '@/features/progress/award'\nimport { bus } from '@/bus/index'\n"
    "window.dispatchEvent(new CustomEvent('xp'))\nexport const grade = () => award()\n")
(cr / "src/features/progress/award.ts").write_text("import { grade } from '../ear/grade'\nexport const award = () => 1\n")
(cr / "src/bus/index.ts").write_text("export const bus = {}\n")
(cr / ".flow/config.json").write_text('{"shared": ["src/bus"]}')
p = sh("python3", str(PLUGIN / "scripts" / "coupling.py"), "--json", cwd=cr)
m = json.loads(p.stdout)
expect("coupling: cross-domain imports counted, shared bus excluded", m["cross_domain_imports"] == 2, p.stdout)
expect("coupling: domain and file cycles found", m["domain_cycles"] == 1 and m["file_cycles"] == 1, p.stdout)
expect("coupling: string-keyed messaging found", m["string_messaging_files"] == 1, p.stdout)

# Rust: `use crate::x` resolves to src/x.rs or src/x/mod.rs.
rr = TMP / "rust-repo"
(rr / "src/audio").mkdir(parents=True)
(rr / ".flow").mkdir()
sh("git", "init", "-q", "-b", "main", cwd=rr)
(rr / "Cargo.toml").write_text('[package]\nname = "r"\nversion = "0.1.0"\n')
(rr / "src/main.rs").write_text("mod audio;\nmod progress;\nfn main() {}\n")
(rr / "src/audio/mod.rs").write_text("pub mod detect;\nuse crate::progress::award;\n")
(rr / "src/audio/detect.rs").write_text("pub fn pitch() {}\n")
(rr / "src/progress.rs").write_text("use crate::audio::detect::pitch;\npub fn award() {}\n")
(rr / ".flow/config.json").write_text(json.dumps({"domains": {"audio": "src/audio", "progress": "src/progress.rs"}}))
p = sh("python3", str(PLUGIN / "scripts" / "coupling.py"), "--json", cwd=rr)
m = json.loads(p.stdout)
expect("coupling (Rust): use crate:: resolves to name.rs and name/mod.rs",
       m["import_edges"] == 2 and m["cross_domain_imports"] == 2, p.stdout)
expect("coupling (Rust): the module cycle is found", m["domain_cycles"] == 1 and m["file_cycles"] == 1, p.stdout)

# ---------------------------------------------------------------- bus generator (needs Node)

if shutil.which("node"):
    br = TMP / "bus-repo"
    shutil.copytree(PLUGIN / "templates/nextjs/bus", br / "src/bus")
    shutil.copytree(PLUGIN / "templates/nextjs/helpers", br / "src/helpers")
    gen = PLUGIN / "templates/nextjs/scripts/bus-gen.mjs"
    p = sh("node", str(gen), cwd=br, check=False)
    expect("bus-gen: writes the dispatcher",
           p.returncode == 0 and "handleAwardXp(cmd, ctx)" in (br / "src/bus/dispatch.ts").read_text(), p.stderr)
    p = sh("node", str(gen), "--check", cwd=br, check=False)
    expect("bus-gen: --check passes when up to date", p.returncode == 0, p.stderr)
    msgs = br / "src/bus/domains/progress/messages.ts"
    msgs.write_text(msgs.read_text().replace("amount: number; reason: string };\n\nexport type ProgressEvent",
                                             "amount: number; reason: string }\n  | { type: 'progress/resetXp' };\n\nexport type ProgressEvent"))
    p = sh("node", str(gen), cwd=br, check=False)
    expect("bus-gen: a command without a handler fails", p.returncode == 1 and "handleResetXp" in p.stderr, p.stderr)
else:
    print("skip bus-gen tests (node not installed)")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(failures)} failures" if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
