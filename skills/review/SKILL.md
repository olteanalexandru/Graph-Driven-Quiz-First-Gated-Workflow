---
name: review
description: Stage S7 for one item - read-only review against its claim, acceptance criteria, UML and security checklist; loops back to the implementer on BLOCK.
argument-hint: "<ITEM-ID>"
disable-model-invocation: true
---

# Review $0

1. Run the `reviewer` subagent with the item ID, the base branch from .flow/config.json,
   and the model from the budget's `agents.reviewer`. It writes docs/items/$0/review.md.
2. `Verdict: PASS`: ask the user to run `/flow:ship $0`.
3. `Verdict: BLOCK`: give the implementer the reasons only (not the whole review); once
   the checks are green again, commit (`$0: review fixes`) and review again. After two BLOCK rounds for the same reason, ask the advisor once and
   raise the budget one level with the `budget` skill (reason: "review blocked twice").
