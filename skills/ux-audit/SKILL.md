---
name: ux-audit
description: Component UX audit - inventories one component's features, maps its interactions and UI states (loading, empty, error, keyboard, mobile, accessibility), measures them with a Playwright probe and writes a persistent audit report with severity-rated findings. The findings then go through the normal gated flow; with an item ID it re-audits after a build.
argument-hint: "<component> [--states <file>] | <ITEM-ID>"
disable-model-invocation: true
---

# UX audit: $ARGUMENTS

One component, not the whole app. The audit never changes code: fixes go through
`/flow:quiz` -> `/flow:plan` -> `/flow:build` (which re-audits at S6a) -> `/flow:review`
-> `/flow:ship`, with every existing feature kept unless the user approves a change.

## A component name or path: round 1 (S1)

Run in the main session on the base branch, outside plan mode (the probe runs a browser).

1. Item: the next free `ITEM-NNN` under `docs/items/`.
2. Find the component in the graph (graphify_find, graphify_find_seeds): its file, the
   routes that render it (callers up to a page), its children, the bus messages it
   sends or handles, and its tests (graphify_tests_for). Several matches: ask the user
   which one with AskUserQuestion. Parents and children are context, not scope.
3. States: write docs/items/<ID>/ux-states.json from
   `${CLAUDE_PLUGIN_ROOT}/templates/ux-states.json` (or copy the `--states` file): the
   route, a selector for the component's root, and how to reach each state. Browser
   requests can be mocked (`hang` for loading, `json` for empty, `status` for error);
   data the server renders needs the app's E2E fixtures or query parameters instead.
   Use `ux_audit` in .flow/config.json for the base URL and viewports.
4. The app must be running at the base URL. If it is not, ask the user to start it (or
   ask before starting the dev server yourself).
5. Run the `auditor` subagent in discover mode with the item ID, the component, its
   files and tests, and the model from the standard budget's `agents.auditor`. It runs
   `node ${CLAUDE_PLUGIN_ROOT}/scripts/ux_probe.mjs --states docs/items/<ID>/ux-states.json --out .flow/state/ux/<ID>/round-1`
   and writes docs/items/<ID>/baseline.md (features F#) and docs/items/<ID>/audit.md
   (round 1, `phase: discovery`, `verdict: OPEN`).
6. Show the user one screen: features found, states reached and missing, findings by
   severity, and the top five findings with their proposals.
7. Commit docs/items/<ID>/ (audit.md, baseline.md, ux-states.json): `<ID>: ux audit`.
   Screenshots stay in `.flow/state/` (git-ignored).
8. Next step for the user: `/flow:quiz <ID>` to choose which findings to fix.

## An item ID: another round (S6a)

Run in the item's worktree after the test gate is green. `/flow:build` does this
itself at S6a; run it by hand after manual fixes.

1. Run the `auditor` subagent in verify mode with the item ID and the budget's
   `agents.auditor`. It probes into `.flow/state/ux/<ID>/round-<n>`, compares with the
   previous round, and appends round n to audit.md: each finding fixed (with the test
   that proves it), open, regressed or new; each kept feature still working.
2. The verdict is PASS when no critical or major finding in scope is open and nothing
   regressed. FAIL lists what to fix next, inside the item's claim. After
   `ux_audit.max_rounds` rounds the remaining findings are trade-offs for the user to
   accept (D# rows, `verdict: ACCEPTED`) or keep working on.
3. Commit audit.md (`<ID>: audit round <n>`).
