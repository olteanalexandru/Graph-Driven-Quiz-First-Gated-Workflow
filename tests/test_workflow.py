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
expect("hooks.json: every hook event has a handler", events == {"pre-edit", "pre-bash", "stop", "session-start"}, str(events))
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


def edit(rel, cwd=wt):
    return hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": str(cwd / rel)}, "cwd": str(cwd)}, cwd)


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

p = sched("release", "ITEM-001")
p = sched("next")
expect("after release, ITEM-001 can start again and ITEM-003 still waits",
       "start now: ITEM-001" in p.stdout and "wait: ITEM-003: needs ITEM-002" in p.stdout, p.stdout)

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
