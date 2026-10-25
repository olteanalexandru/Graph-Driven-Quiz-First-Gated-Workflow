---
name: build
description: Stages S4-S6a for one planned item - claim its files, open its worktree session with the budget's settings, write failing acceptance specs and passing baseline specs, implement until they pass, then audit that existing features and UX flows still work.
argument-hint: "<ITEM-ID>"
disable-model-invocation: true
---

# Build $0

One item, one branch, one worktree, one writer at a time. Never run two agents on this
item at the same time and never split it across parallel agents.

Read `budget:` from docs/items/$0/spec.md and that level in `.flow/config.json` `budgets`.

If the current branch name does not contain $0 (you are in the planning session):
1. Design: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/design.py status $0 --gate`. If it
   fails, the design needs the user's approval first (`/flow:plan $0`); stop. If it
   lists files edited by hand since planning, run `/flow:plan $0` first so the ACs,
   baseline and tests follow them.
2. Claim: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py claim $0`. If it refuses
   (overlap, contract, unmet dependency, WIP limit, budget), show why and stop. Only
   the user may approve `--force`.
3. Create the worktree from the current branch, which holds the item's planning commit:
   `git worktree add "../$(basename "$PWD")-$0" -b $0`
4. Tell the user to open the item session there with the budget's settings, run
   `/flow:build $0` in it, and stop:
   `cd ../<repo>-$0 && claude --model <session.model> --effort <session.effort> --autocompact <session.autocompact>`

In the item worktree:
1. Set `status: in-progress` in the spec.
2. S4 specs: run the `e2e-author` subagent with the model from `agents["e2e-author"]`;
   when it is null, write the specs in this session following
   `${CLAUDE_PLUGIN_ROOT}/agents/e2e-author.md`. Acceptance specs must fail because the
   behavior is missing; baseline specs (`F<n> ... @baseline`) must pass on today's
   code, and so must the existing tests named in baseline.md. A failing baseline spec
   means the baseline row is wrong: fix the row with the user, not the app. Commit
   `$0: specs (baseline green)`.
3. If context is past the budget's `handoff_at`, run the `handoff` skill and ask the user
   to `/clear`.
4. S5: run the `implementer` subagent with `agents.implementer` (null: implement here,
   following `${CLAUDE_PLUGIN_ROOT}/agents/implementer.md`). If it reports that an AC
   cannot pass without changing a kept feature or departing from a diagram, ask the
   user; the spec's D# rows, baseline.md, the diagram, the ACs and the tests change
   together, or not at all.
5. S6: the Stop hook runs the budget's `stop_checks`, the baseline tests and the item's
   security checks. If the same test fails twice: ask the advisor one question (the
   advisor tool when /advisor is on, otherwise the `second-opinion` subagent) with the
   failing test and the 1-3 files involved, then retry once. Still failing: raise the
   budget one level with the `budget` skill (reason: "test kept failing").
6. S6a audit, when docs/items/$0/ has baseline.md or audit.md: run the `auditor`
   subagent with `agents.auditor` (null: follow `${CLAUDE_PLUGIN_ROOT}/agents/auditor.md`
   here) in verify mode. It writes the next round in audit.md.
   - FAIL: give the implementer the open findings and regressions only (inside the
     claim), commit `$0: audit fixes`, let the gate pass, audit again.
   - After `ux_audit.max_rounds` rounds (default 3) with findings still open: show the
     trade-offs and ask the user to accept each one, keep working, or drop it from scope.
     Accepted ones become D# rows; then set `verdict: ACCEPTED` in audit.md.
7. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py check $0 --stage build` must show
   no errors.
8. When everything is green, commit on the item branch (`$0: build`) and ask the user to
   run `/flow:review $0`.
