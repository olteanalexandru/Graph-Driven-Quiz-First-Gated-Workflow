# ITEM-000 baseline: what the touched code does today

Every existing component, route, handler or table this item changes, and what a user
or caller can do with it now. Rows are kept unless a decision in spec.md says
otherwise; changing or removing a feature needs the user's approval as a D# row.
You can edit this file; your edits win.

| ID | Feature (what a user or caller can do or see today) | Where | Tests | Plan |
|----|------------------------------------------------------|-------|-------|------|
| F1 |                                                      |       | none  | keep |

Tests: existing test files (`tests/e2e/progress.spec.ts:12`), or `none`, which means S4
adds a characterization spec `F<n> ... @baseline @item:<ID>` that passes on today's code.
Plan: `keep`, `change (D#)` or `remove (D#)`.
