---
name: review
description: Stage S7 for one item - read-only review against its claim, acceptance criteria, baseline, UML, audit and security checklist; loops back to the implementer on BLOCK and hands finished parallel items to the integration gate.
argument-hint: "<ITEM-ID>"
disable-model-invocation: true
---

# Review $0

1. Preconditions: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py check $0 --stage review`
   shows no errors, and when docs/items/$0/ has baseline.md or audit.md, audit.md is in
   phase `verify` with `verdict: PASS` or `ACCEPTED`. Otherwise finish S6a first
   (`/flow:build $0`).
2. Run the `reviewer` subagent with the item ID, the base branch from .flow/config.json,
   and the model from the budget's `agents.reviewer`. It writes docs/items/$0/review.md.
3. `Verdict: PASS`: commit review.md, then run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py verify $0`. If another claimed
   item has also passed review, ask the user to run `/flow:integrate`; otherwise
   `/flow:ship $0`.
4. `Verdict: BLOCK`: give the implementer the reasons only (not the whole review); once
   the checks are green again, commit (`$0: review fixes`) and review again. After two BLOCK rounds for the same reason, ask the advisor once and
   raise the budget one level with the `budget` skill (reason: "review blocked twice").
