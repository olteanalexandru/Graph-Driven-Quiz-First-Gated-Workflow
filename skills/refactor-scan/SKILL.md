---
name: refactor-scan
description: Baseline for cleaning up an existing codebase - coupling between features, import cycles, hub files, string-keyed messaging, untested flows and security findings - turned into a backlog of small refactor items in conflict-free waves.
disable-model-invocation: true
---

# Refactor scan

Refactor items change structure, never behavior. Write results to files under
docs/refactor/ (create it) and keep chat output to the summary.

1. Domains: if .flow/config.json has no `domains`, propose them from the folder layout
   (one per feature area, for example auth, payments, profile and each product feature)
   and confirm with the user in one AskUserQuestion. Put the bus, helpers and UI kit in
   `shared`.
2. Graph: `graphify update .` so the scan reads the current code.
3. Coupling: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/coupling.py --md > docs/refactor/baseline.md`
   and `--json > docs/refactor/metrics-<date>.json`. Confirm the three largest
   cross-domain pairs with graphify_callers, and list string-keyed messaging the graph
   cannot see.
4. Tests: for each main user flow (sign-in, paywall, each core feature, language
   switch), check graphify_tests_for on its entry point and list the flows with no
   Playwright coverage.
5. Security: `uv run security/checks.py run --stage S6 > docs/refactor/security-baseline.txt`
   and the same with `--stage weekly`.
6. Backlog: write docs/refactor/backlog.md as small items, each with tier, deps and
   expected claim paths, in this order:
   1. characterization specs that lock today's behavior of every uncovered flow
      (these must exist before any refactor item touches that flow);
   2. security blockers;
   3. the bus skeleton (`templates/nextjs/bus`) plus porting one interaction, and a
      check that Graphify sees the new edges;
   4. one item per cross-domain pair: move that interaction onto the bus;
   5. break import cycles, split files over 400 lines, delete code with no callers.
7. Ask the user to create the items with /flow:quiz, one per backlog line (refactor
   quizzes are short: boundaries and naming, since behavior stays the same); once they
   are planned, `schedule.py waves` shows which can run together.

Rules for every refactor item, also checked in review:
- no behavior change: its characterization specs and the full Playwright suite pass
  before and after;
- one coupling pair or one file split per item;
- `coupling.py --json` after the change shows no more cross-domain imports or cycles
  than before.
