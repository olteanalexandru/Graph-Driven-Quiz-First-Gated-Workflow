# The flow

Every change is an item. An item moves through fixed stages; each stage leaves a file,
so a new session (or a different agent) can continue from the files alone.

| Stage | Command | Runs in | Writes |
|---|---|---|---|
| S1 Quiz | `/flow:quiz "<idea>"` | main session, plan mode, base branch | `docs/items/<ID>/spec.md` (decisions D1.., acceptance criteria AC1..) |
| S2 Design | `/flow:plan <ID>` | `architect` subagent | `uml/*.mmd`, `data-structures.md`, `messages.md` |
| S3 Impact | `/flow:plan <ID>` | main session + Graphify | `impact.json` (the file claim), `security.md` |
| S4 Specs | `/flow:build <ID>` | `e2e-author` subagent, item worktree | `tests/e2e/<ID>-*.spec.ts`, failing |
| S5 Build | `/flow:build <ID>` | `implementer` subagent | helpers, messages, handlers, UI |
| S6 Gate | Stop hook | hook | tests + `security/checks.py run --stage S6` |
| S7 Review | `/flow:review <ID>` | `reviewer` subagent, read-only | `review.md` (PASS or BLOCK) |
| S8 Ship | `/flow:ship <ID>` | main session | merge, `graphify update`, claim released |

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
