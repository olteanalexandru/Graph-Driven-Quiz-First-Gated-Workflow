---
name: plan
description: Stages S2-S3 for an approved item - an editable design (UML, data structures, messages, workload and budgets, baseline of touched features), the impact set, contracts and file claim, the security checklist, and whether it can start now. Keeps the user's hand edits and asks before changing an approved design.
argument-hint: "<ITEM-ID> [--no-design]"
disable-model-invocation: true
---

# Plan $0

Run on the base branch; these are planning docs. To re-plan an item that is already
being built, run this in its worktree instead: the claim is refreshed in step 11.
Every design file is editable by the user, and their edits win: the hooks ask before
Claude changes a file the user edited by hand, or any file of an approved design.

1. Read docs/items/$0/spec.md. Stop if `status` is `draft` or `done` (a re-plan in the
   worktree keeps `in-progress`). Note `tier`,
   `budget`, `clarify` and `complex`. Read that budget in `.flow/config.json`.
2. Effort: planning is complex when `complex: true`, the budget is deep, or step 5
   finds it so. Complex planning runs at effort `max` (Claude Code uses the highest
   level the model supports); otherwise at the budget's `planning_effort`. Pass it as
   the Agent tool's `effort` for the architect. When planning happens in this session
   (null model, or an Agent tool without an effort setting), ask the user to run
   `/effort <level>` first.
3. Re-planning (design files exist): run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/design.py status $0` and
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py check $0 --stage plan --drift`.
   Files edited by hand are inputs, not outputs: they are the user's design decisions.
   Pass the list to the architect.
4. Design and impact (tier S or `--no-design`: skip the design and compute the impact
   here, direct callers and callees only): run the `architect` subagent with the item
   ID, the model from the budget's `agents.architect` and the effort from step 2, plus
   the hand-edited files from step 3 (null model: do it in this session following
   `${CLAUDE_PLUGIN_ROOT}/agents/architect.md`). It writes docs/items/$0/uml/,
   data-structures.md, messages.md, performance.md, baseline.md and impact.json, and
   returns seeds, contracts, complexity signals, baseline proposals, conflicts with
   hand edits, and drift between diagrams and code.
5. Size and budget, from the architect's report:
   - past `split_at` in .flow/config.json (nodes or domains): stop and propose a split
     into smaller items with deps and contracts; on the user's yes, set this spec back
     to `status: draft`, delete its impact.json so the scheduler cannot start it, and
     quiz the parts one by one with `/flow:quiz`;
   - more than 2 domains or about 25 nodes, or security blockers for auth, payments or
     RLS: raise the budget one level, add a line to the spec's Budget log, set
     `complex: true`, and if step 4 ran below `max`, run the architect once more at
     `max` in review mode (impact set and contracts only).
6. Conflicts and drift: for each conflict with a hand-edited file, and each drift where
   the code and a diagram disagree, ask the user which side wins (AskUserQuestion).
   The losing side is updated; a hand-edited file changes only with the user's yes.
   Design edits the hook denied the architect (it lists them in its report): ask the
   user, and on yes run `design.py unlock $0 <file>` and make the edit in this session.
7. Features: for each baseline row the architect proposes to change or remove, ask the
   user (keep / change as proposed / remove). Record each yes as a D# row in the spec,
   then set the row's plan to `change (D#)` or `remove (D#)`. Never change or remove a
   feature without that answer. Update the spec's ACs to match; ACs and tests follow
   the design, and the user approves AC changes.
8. Check: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py check $0 --stage plan` must
   show no errors; fix the docs, not the check.
9. Approval. Show one screen: diagrams, messages and contracts, performance budgets,
   baseline changes, and the hand edits that were kept. Then:
   - deep clarification, or `design_approval: "always"`: ask with AskUserQuestion
     ("Approve the design" / "I will edit the files first" / "Change something").
     On approve run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/design.py approve $0`. On
     edit, stop: the user edits the files and runs `/flow:plan $0` again. Code edits
     wait for this approval (the pre-edit hook enforces it).
   - quick: say that any file under docs/items/$0/ can still be edited before
     `/flow:build`, and that the edits win.
10. Security: `uv run security/checks.py item $0 --write`.
11. Schedule: if $0 already has a claim (re-planning), refresh it with
    `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py claim $0`; otherwise run
    `schedule.py next` and report whether $0 can start now or what it waits for
    (files, contracts, shared context, budget).
12. Commit only docs/items/$0/ (with design.lock.json): `$0: design and impact`. Next
    step for the user: `/flow:build $0`.
