---
name: quiz
description: Stage S1. Turns an idea into an approved spec by asking the user multiple-choice questions, then writes docs/items/<ID>/spec.md.
argument-hint: "<idea>"
disable-model-invocation: true
---

# S1 quiz: $ARGUMENTS

Run this in the main session (subagents cannot ask the user questions) on the base
branch. Specs are planning docs and live on the base branch so the scheduler can see
every item. Ask the questions in plan mode; write files only after approval.

1. Pick the ID: the next free `ITEM-NNN` under `docs/items/`.
2. Look before asking. Query the graph for existing code this touches
   (graphify_find_seeds / query_graph); read at most the 3 most relevant files, by
   line range. Questions must name real modules.
3. Get the security questions: `uv run security/checks.py item <ID>` and keep the S1 lines.
4. Ask with AskUserQuestion, one or two questions per call, 2-4 options each with a
   one-line consequence, the recommended option first. Cover these slots in order, and
   skip the ones that do not apply:
   goal and user, entry point, happy path, failure and edge cases, data and who may
   read or change it, UI states, text and translations, free or paid, gamification,
   performance budget, the S1 security questions, acceptance criteria, out of scope.
5. Size it. S: 1-3 questions, a fix with no new data or messages. M: 6-12 questions.
   Past about 15 questions, or tier L, the item is too big: split it into M items with
   deps and quiz each one.
6. Show a one-screen summary of the decisions and ask for approval with ExitPlanMode.
7. After approval, write `docs/items/<ID>/spec.md` from
   `${CLAUDE_PLUGIN_ROOT}/templates/spec.md` with `status: approved`. Each answer is a
   row in Decisions (D1, D2, ...); each acceptance criterion cites its D-IDs. Budget:
   lean for tier S, standard for tier M, deep when the item touches auth, payments or
   RLS. Commit only that folder: `<ID>: spec`.
8. Next step for the user: `/flow:plan <ID>` (tier S skips design: `/flow:plan <ID> --no-design`).

Keep it short: no file dumps in questions, no restating answers back one by one.
