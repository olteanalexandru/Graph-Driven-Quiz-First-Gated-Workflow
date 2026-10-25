---
name: build
description: Stages S4-S6 for one planned item - claim its files, open its worktree session with the budget's settings, write failing acceptance specs, implement until they pass.
argument-hint: "<ITEM-ID>"
disable-model-invocation: true
---

# Build $0

One item, one branch, one worktree, one writer at a time. Never run two agents on this
item at the same time and never split it across parallel agents.

Read `budget:` from docs/items/$0/spec.md and that level in `.flow/config.json` `budgets`.

If the current branch name does not contain $0 (you are in the planning session):
1. Claim: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py claim $0`. If it refuses
   (overlap, unmet dependency, WIP limit), show why and stop.
2. Create the worktree from the current branch, which holds the item's planning commit:
   `git worktree add "../$(basename "$PWD")-$0" -b $0`
3. Tell the user to open the item session there with the budget's settings, run
   `/flow:build $0` in it, and stop:
   `cd ../<repo>-$0 && claude --model <session.model> --effort <session.effort> --autocompact <session.autocompact>`

In the item worktree:
1. Set `status: in-progress` in the spec.
2. S4 acceptance specs: run the `e2e-author` subagent with the model from
   `agents["e2e-author"]`; when it is null, write the specs in this session following
   `${CLAUDE_PLUGIN_ROOT}/agents/e2e-author.md`. They must fail because the behavior is
   missing.
3. If context is past the budget's `handoff_at`, run the `handoff` skill and ask the user
   to `/clear`.
4. S5: run the `implementer` subagent with `agents.implementer` (null: implement here,
   following `${CLAUDE_PLUGIN_ROOT}/agents/implementer.md`).
5. S6: the Stop hook runs the budget's `stop_checks` and the item's security checks.
   If the same test fails twice: ask the advisor one question (the advisor tool when
   /advisor is on, otherwise the `second-opinion` subagent) with the failing test and
   the 1-3 files involved, then retry once. Still failing: raise the budget one level
   with the `budget` skill (reason: "test kept failing").
6. When everything is green, commit on the item branch (`$0: build`) and ask the user to
   run `/flow:review $0`.
