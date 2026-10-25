---
item: ITEM-000
component: Name (src/path/Component.tsx)
kind: ux               # ux (from /flow:ux-audit) | features (baseline check for other items)
phase: discovery       # discovery (round 1, before any change) | verify (after a build)
round: 1
verdict: OPEN          # OPEN (discovery) | PASS | FAIL | ACCEPTED (the user accepted the trade-offs)
---
# ITEM-000 audit: Name

## Scope
The component, the routes where it renders, and what is out of scope (its parents, its
children, the rest of the app). Features: see baseline.md (F#).

## Interaction map
| Trigger | Response | Feedback the user gets | F# |
|---------|----------|------------------------|----|

## UI states
| State | How it is reached | 375 px | 1280 px | Keyboard | Notes |
|-------|-------------------|--------|---------|----------|-------|
| default | | | | | |
| loading | | | | | |
| empty | | | | | |
| error | | | | | |
| disabled | | | | | |
| long content | | | | | |
| signed out or free plan | | | | | |
| reduced motion | | | | | |

Evidence: `.flow/state/ux/<ID>/round-<n>/` (probe report, screenshots, ARIA snapshots).

## Findings
| ID | Dimension | Finding | Evidence | Severity | Proposal | Features | Status |
|----|-----------|---------|----------|----------|----------|----------|--------|

Dimension: intuitiveness, accessibility, responsive, consistency, visual hierarchy,
market appeal. Severity (Nielsen 0-4): 4 critical, 3 major, 2 minor, 1 cosmetic.
Status: open | fixed (test name) | accepted (D#) | out of scope.

## Rounds
### Round 1, discovery, <date>

## Unresolved trade-offs
