---
id: ITEM-000
title: Short title
status: draft          # draft | approved | in-progress | done
tier: M                # S small fix | M feature (L: split into M items before planning)
budget: standard       # lean (S) | standard (M) | deep (auth, payments, RLS); see /flow:budget
deps: []               # items that must merge first, e.g. [ITEM-012]
---
# ITEM-000 Short title

## Decisions
| ID | Question | Chosen | Rejected | Why |
|----|----------|--------|----------|-----|
| D1 |          |        |          |     |

## Acceptance criteria
- AC1 (D1): Given ..., when ..., then ...

## Security decisions
Answers to the S1 questions from `security/checks.py item <ID>`, also numbered D#.

## Out of scope
-

## Budget log
Escalations and the reason, one line each (see /flow:budget).
