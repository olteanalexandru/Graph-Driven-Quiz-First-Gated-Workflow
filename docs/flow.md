# The flow

Every change is an item. An item moves through fixed stages; each stage leaves a file,
so a new session (or a different agent) can continue from the files alone.

| Stage | Command | Runs in | Writes |
|---|---|---|---|
| S1 Clarify | `/flow:quiz "<idea>" [--quick\|--deep]` | main session, plan mode, base branch | `docs/items/<ID>/spec.md` (decisions D1.., assumptions A1.., acceptance criteria AC1..) |
| S1 UX audit | `/flow:ux-audit <component>`, then `/flow:quiz <ID>` | main session + `auditor`, base branch | `audit.md` round 1 (findings U1..), `baseline.md` (features F1..), `ux-states.json` |
| S2 Design | `/flow:plan <ID>` | `architect` subagent | `uml/*.mmd`, `data-structures.md`, `messages.md`, `performance.md`; approval in `design.lock.json` |
| S3 Impact | `/flow:plan <ID>` | `architect` + main session, Graphify | `impact.json` (claim, contracts, risk), `baseline.md`, `security.md` |
| S4 Specs | `/flow:build <ID>` | `e2e-author` subagent, item worktree | `tests/e2e/<ID>-*.spec.ts`: AC specs failing, F# baseline specs passing, PB# budgets |
| S5 Build | `/flow:build <ID>` | `implementer` subagent | helpers, messages, handlers, UI |
| S6 Gate | Stop hook | hook | tests + baseline tests + `security/checks.py run --stage S6` |
| S6a Audit | `/flow:build <ID>` (or `/flow:ux-audit <ID>`) | `auditor` subagent | `audit.md` round n (PASS, FAIL or ACCEPTED) |
| S7 Review | `/flow:review <ID>` | `reviewer` subagent, read-only | `review.md` (PASS or BLOCK) |
| S7a Integration | `/flow:integrate` | `integrate.py` + `reviewer` | a run in `.git/flow-integration/` (PASS or BLOCK) |
| S8 Ship | `/flow:ship <ID>` | main session | `metrics.json`, merge, `graphify update`, claim released |

S6a runs only for items with a baseline or a UX audit, and S7a only when another
claimed item has also passed review. The stage IDs S1-S8 stay as they were because the
security registry keys its checklists on them.

`/flow:next` shows every item's stage, the active claims, and what can start now.

`/flow:build` starts in the planning session: it claims the item's files, then creates
the item's worktree beside the repository from the base-branch commit that holds the
planning docs (`git worktree add ../<repo>-<ID> -b <ID>`). You open a new session in
that folder with the budget's model and settings; S4-S8 run there.

## Rules that prevent merge conflicts

1. One item = one branch = one worktree = one writer at a time. Subagents inside an
   item take turns; nothing runs in parallel inside one item.
2. Items run in parallel only when their claims do not overlap. A claim is the item's
   impact set from Graphify: files and folders, plus tokens for shared "hot" files
   (bus registry, dependencies, app shell, test fixtures).
3. On an item branch the pre-edit hook checks Claude's edit tools (Edit, Write,
   MultiEdit, NotebookEdit). It blocks edits outside the claim, edits to files another
   item claimed, and hot files the item did not claim; in warn mode it reports them
   instead. Files whose name contains the item ID are always inside the claim. Shell
   commands can still write files, so the review's scope check compares every changed
   file with the claim.
4. `schedule.py` treats scheduling as a graph problem: items with dependencies form a
   DAG; overlapping claims form a conflict graph. `next` picks a conflict-free set of
   ready items, critical path first, up to the WIP limit; `waves` lays out the whole
   plan.
5. Merge one item at a time: rebase on the base branch, run the full suite, merge.
6. Contracts count like files. An item that changes an API route, a table, a bus
   message, a config key, a store or a shared UI component (`impact.json`
   `contracts.changes`) never runs beside an item that changes or uses the same one;
   the user goes after the changer.
7. Items that would read mostly the same code, or are both high rework risk in one
   domain, run one after the other: waiting is cheaper than reading twice and redoing.
8. The scheduler starts no more than the WIP limit, the 5-hour rate-limit floor and
   the global cost budget allow. See [token-budget.md](token-budget.md).

## Clarification: quick or deep

`deep_clarification` in `.flow/config.json` (default `false`) picks the quiz mode;
`--quick` and `--deep` override it per item. Quick asks only the high-impact questions
and writes everything else down as assumptions (A#) that the user sees before
approving. Deep keeps a ledger of open questions, asks the highest-impact ones first,
never asks a settled slot again, and ends only when nothing high-impact is open and
every acceptance criterion is testable; the spec and then the design each need an
explicit approval before any code.

## Editable design

The design is a draft the user can edit before and during the build:

- The hooks record a hash each time Claude writes a design file
  (`docs/items/<ID>/design.lock.json`). A file whose content no longer matches was
  edited by hand and is authoritative: Claude's next edit to it triggers a permission
  prompt with the reason, and re-planning reads it instead of regenerating it.
- `design.py approve <ID>` snapshots the approved design. Any later edit by Claude
  prompts first; a change outside Claude shows as "changed since approval". For deep
  items (or with `design_approval: "always"`) code edits wait until the design is
  approved again.
- `trace.py check <ID>` keeps the pieces in sync: every AC cites a decision, every
  design file cites decisions that exist, every kept feature and budget has a test, no
  test points at nothing, and diagram participants that the code lacks are listed.

## Feature preservation

Before an item changes existing code, `baseline.md` lists what that code does today
(F#) and which tests guard it. S4 adds characterization specs for features without
tests; they pass before any change. The Stop hook runs the baseline tests on every
finish (`baseline_check`), S6a checks the features and flows again, and review blocks
a feature changed or removed without the user's decision (a D# row) or a baseline test
weakened.

## Component UX audit

`/flow:ux-audit <component>` audits one component, not the app. The `auditor` builds
the feature inventory, an interaction map and a state matrix (default, loading, empty,
error, disabled, long content, signed out, reduced motion; at 375 px, 1280 px and by
keyboard), runs `scripts/ux_probe.mjs` for evidence, and rates findings 0-4 across
intuitiveness, accessibility (WCAG 2.2 A/AA), responsive behavior, consistency, visual
hierarchy and market appeal. The quiz picks which findings to fix; the build re-audits
at S6a with the same probe and states until no critical or major finding in scope is
open, or, after `ux_audit.max_rounds`, the user accepts the rest as trade-offs.

## Why planning docs live on the base branch

S1-S3 write only `docs/items/<ID>/` and commit them on the base branch. That lets the
scheduler see every item's claim before any of them starts, and item folders never
conflict with each other. S4-S8 happen on the item branch.

## Communication through one component

Features never import each other's internals. They send typed commands through the bus
(`src/bus`), each handled by exactly one named handler, and react to typed events.
`scripts/bus-gen.mjs` generates the dispatcher as a `switch` over named handlers, so
the compiler checks that every command is handled and Graphify can trace every edge.
Business rules live in pure helpers. See `templates/nextjs/bus/`.
