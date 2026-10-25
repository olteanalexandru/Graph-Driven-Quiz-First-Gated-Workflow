---
name: quiz
description: Stage S1 Clarify. Turns an idea, or a UX audit, into an approved spec by asking the user multiple-choice questions - quick (high-impact questions, documented assumptions) or deep (until nothing high-impact is open) - then writes docs/items/<ID>/spec.md.
argument-hint: "\"<idea>\" | <ITEM-ID> [--quick | --deep]"
disable-model-invocation: true
---

# S1 clarify: $ARGUMENTS

Run this in the main session (subagents cannot ask the user questions) on the base
branch. Specs are planning docs and live on the base branch so the scheduler can see
every item. Ask the questions in plan mode; write files only after approval.

## Setup

1. Item: an `ITEM-NNN` argument continues that item (a UX audit from `/flow:ux-audit`,
   or a draft spec); otherwise take the next free `ITEM-NNN` under `docs/items/`.
2. Mode: `--deep` or `--quick` if given, else `deep_clarification` in
   `.flow/config.json` (`true` = deep, default `false` = quick). Say the mode in one line.
3. Look before asking. Query the graph for existing code this touches
   (graphify_find_seeds / query_graph); read at most the 3 most relevant files, by
   line range. Questions must name real modules.
4. Get the security questions: `uv run security/checks.py item <ID>` and keep the S1 lines.
5. Slots, in order; skip the ones that do not apply: goal and user, entry point, happy
   path, failure and edge cases, data and who may read or change it, UI states
   (loading, empty, error, disabled), text and translations, free or paid,
   gamification, workload (how often it runs, how much data, how fast it must feel),
   the S1 security questions, acceptance criteria, out of scope.
6. Rate each unknown slot. **High impact**: the answer changes data, permissions, a
   user flow, a contract (API, schema, message), a security decision, or would cost
   more than a day to reverse. **Low impact**: copy, layout details, defaults that are
   cheap to change later.

Ask with AskUserQuestion, one or two questions per call, 2-4 options each with a
one-line consequence, the recommended option first. Never ask about something the
graph, the code or an earlier answer already settles, and never ask the same slot
twice; if an answer settles another open slot, close that one too.

## Quick mode (default)

7. Ask only the high-impact slots: 1-3 questions for tier S, up to about 8 for tier M.
8. Every other slot becomes an assumption A# with its basis (code, convention, the
   graph) and what changes if it is wrong.
9. Summary: decisions, assumptions and acceptance criteria on one screen. The approval
   question offers "Approve", "Change something" and "Switch to deep clarification";
   recommend deep when 3 or more high-impact slots were assumed rather than asked.

## Deep mode

7. Keep a question ledger in the plan: each open question with its slot and impact,
   each answered one as its D#, each "you decide" as an A#. Show it in one line per
   round: `open: 3 high, 2 low; decided: D1-D6; assumed: A1`.
8. Each round asks the 1-2 open questions of highest impact (the ones other answers
   depend on first). After each answer, add the follow-ups it opens (edge cases,
   failure paths, limits) and close what it settles.
9. Done when all of these hold, then stop asking:
   - no high-impact question is open (low-impact ones become assumptions);
   - every acceptance criterion is Given/When/Then with concrete values and a visible
     or testable result;
   - every failure and edge case in the ledger has an expected behavior (an AC or an
     out-of-scope line);
   - constraints are recorded: workload, security (the S1 lines), devices and browsers,
     data ownership.
   The user can stop the loop at any time ("enough"); the rest become assumptions and
   the summary says the loop ended early.
10. Read the full draft back (decisions, assumptions, ACs, out of scope) and ask for an
    explicit approval with AskUserQuestion ("Approve the spec" / "Change something").
    Only "Approve" counts; then ExitPlanMode. A deep item also needs its design approved
    before any code (`/flow:plan` asks).

## From a UX audit

When `docs/items/<ID>/audit.md` exists (round 1 of `/flow:ux-audit`), the questions are
about what to change: which findings to fix (multiSelect; critical and major first, as
the recommendation), which proposal for each, any feature in baseline.md the fixes
would change or remove (each needs its own explicit answer, recorded as a D#), and
which trade-offs are acceptable. Each AC cites the finding it fixes (U#) and the
decision (D#). Findings not chosen become `out of scope` in audit.md.

## Size and write

11. Size it. S: 1-3 questions, a fix with no new data or messages. M: 6-12 questions in
    quick mode. Past about 15 quick questions, about 12 acceptance criteria, or 3 user
    flows (deep mode counts scope, not questions), or tier L: split it into M items
    with deps and quiz each one.
12. After approval, write `docs/items/<ID>/spec.md` from
    `${CLAUDE_PLUGIN_ROOT}/templates/spec.md` with `status: approved` and `clarify:`
    set to the mode used. Each answer is a row in Decisions (D1, D2, ...); each
    assumption a row in Assumptions (A1, ...); each acceptance criterion cites its
    D#, A# or U# IDs. `Open questions` is `- none` in deep mode. Budget: lean for tier
    S, standard for tier M, deep when the item touches auth, payments or RLS. Set
    `complex: true` when it touches auth, payments or RLS, changes an API or schema
    contract, or spans more than 2 domains: planning then runs at the highest effort.
    Commit only that folder: `<ID>: spec`.
13. Next step for the user: `/flow:plan <ID>` (tier S skips design: `/flow:plan <ID> --no-design`).

Keep it short: no file dumps in questions, no restating answers back one by one.
