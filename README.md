# Graph-Driven Quiz-First Gated Workflow

A Claude Code plugin for building and refactoring apps with several agents without
merge conflicts. It is used to develop [Note Noodle](https://note-noodle.com), a
browser-based music theory and practice app (Next.js, Supabase, Stripe, Vercel), and
works the same way for Rust projects.

What it gives you:

- **Quiz first, quick or deep.** Each change starts as multiple-choice questions to you.
  Quick mode asks only what matters and writes the rest down as assumptions you see;
  deep mode keeps asking until nothing high-impact is open and needs your explicit
  approval. The answers become numbered decisions that the design, tests and review
  all cite.
- **Editable design.** UML, messages, workload budgets and the feature baseline are
  drafts you can edit. Your hand edits win: Claude asks before changing them or an
  approved design, and `trace.py` keeps spec, design and tests in sync.
- **Graph checked.** Graphify finds the code an item touches; that impact set becomes
  the item's file claim. UML diagrams are checked against the code graph in review.
- **One writer per item.** One item = one branch = one worktree. Items run in parallel
  only when their claims do not overlap. On an item branch a hook checks each of
  Claude's file edits against the claim; files changed by shell commands are caught by
  the review's scope check.
- **Playwright gated.** Acceptance specs are written from the decisions before the
  code, and the item cannot finish while they fail.
- **Features preserved.** Before code changes, a baseline lists what it does today and
  the tests that guard it; changing or removing a feature needs your answer.
- **Component UX audit.** `/flow:ux-audit` measures one component's states, keyboard,
  mobile and accessibility behavior with a Playwright probe, rates findings, and
  re-audits after the fix until nothing critical is open.
- **One communication component.** Features talk through a typed message bus with one
  named handler per command; business rules live in pure helpers.
- **Security checks by stack.** 91 checks tagged by technology, environment and deploy
  target; each item gets only the ones its files trigger.
- **Adaptive token budget.** Each item starts on the smallest budget its size allows and
  moves up only when the work turns out harder or needs more context. Complex planning
  runs at the highest effort; complexity analysis is written only where the workload
  makes it matter.
- **Token-aware parallel work.** Items run side by side only when files, contracts and
  most of their context differ, and only as far as the WIP limit, the rate limit and a
  global cost budget allow. Items that finish together pass an integration gate before
  either ships, and each item's cost and rework are recorded for future estimates.
- **Refactor mode.** For an existing codebase: a coupling and security baseline, tests
  that lock current behavior, then small refactor items in conflict-free waves.

## Install

In Claude Code:

```
/plugin marketplace add olteanalexandru/Graph-Driven-Quiz-First-Gated-Workflow
/plugin install flow@graph-driven-quiz-first-gated-workflow
```

Then run `/flow:init` in each repository.

Requirements: Claude Code 2.1.222 or later, git, python3, [uv](https://docs.astral.sh/uv/),
[Graphify](https://github.com/Graphify-Labs/graphify), Node for web projects, and for the
security checks gitleaks, osv-scanner and zizmor (cargo-deny for Rust).

## Commands

| Command | Stage | What it does |
|---|---|---|
| `/flow:init` | setup | Config, CLAUDE.md rules, settings, security checks, Graphify, Playwright, bus template |
| `/flow:quiz "<idea>" [--quick\|--deep]` | S1 | Questions until the spec is clear; writes `docs/items/<ID>/spec.md` |
| `/flow:ux-audit <component>` | S1, S6a | Audit one component; with an item ID, audit again after a build |
| `/flow:plan <ID>` | S2-S3 | Editable design, workload and budgets, feature baseline; impact set, contracts and claim; security checklist |
| `/flow:build <ID>` | S4-S6a | Claim, worktree session, specs, implementation, test gate, audit |
| `/flow:review <ID>` | S7 | Read-only review against claim, acceptance criteria, baseline, UML, audit and security boxes |
| `/flow:integrate [ID ...]` | S7a | Merge items that finished together, run the checks and a combined review |
| `/flow:ship <ID>` | S8 | Integration check, rebase, full suite, release checks, metrics, merge, graph refresh, claim release |
| `/flow:next` | any | Stages, budgets, active claims, capacity, what can start now |
| `/flow:budget [ID] [level]` | any | Show or change an item's token budget |
| `/flow:handoff` | any | Save the item's state so you can `/clear` and continue |
| `/flow:refactor-scan` | existing code | Coupling, cycles, untested flows, security baseline, refactor backlog |
| `/flow:security-refresh` | weekly | New advisories, version floors, platform settings checks |

The stages and the rules behind them: [docs/flow.md](docs/flow.md).

## Terms

- **Item**: one change, from quiz to merge. It has an ID (`ITEM-042`), a folder
  `docs/items/ITEM-042/`, and a branch and worktree of the same name.
- **Seed node**: a function, component, route or table in the code graph that the spec
  names or the design adds. Impact analysis starts from the seeds.
- **Impact set**: the seeds plus the code the graph links them to (callers, callees,
  importers) and the planned new files, saved as `impact.json`. The budget sets how far
  it reaches.
- **Claim**: the files and folders of the impact set, which the item may change.
  `schedule.py claim` records it where every worktree sees it, and refuses when it
  overlaps another active item's claim.
- **Hot token**: a name for files that many items touch, such as `bus-registry` or
  `dependencies` (`hot_files` in `.flow/config.json`). One active item at a time holds
  a token, so two items never edit package.json or the message registry at once.
- **WIP limit**: how many items may hold a claim at once (`wip_limit`, default 2).
- **Wave**: items that can run side by side because their claims do not overlap and
  their dependencies are merged. `/flow:next` lists the next wave.
- **Gate**: the checks the Stop hook runs before Claude may finish a turn on a claimed
  item: the budget's tests, the baseline tests and the item's security checks. In
  enforce mode a failure sends Claude back to fix it; in warn mode it is reported.
- **Contract**: an API route, table, bus message, config key, store or shared UI
  component an item changes or uses (`api:`, `schema:`, `bus:`, `config:`, `state:`,
  `ui:` in impact.json). An item that changes one never runs beside another that changes
  or uses it; items that only use it can run together.
- **Baseline**: what the code an item touches does today (F#), with its tests. Kept
  features must keep working; changes need a decision.
- **Design lock**: `docs/items/<ID>/design.lock.json`, where the hooks note what Claude
  last wrote to each design file and what the user approved.

## Starting a new project

1. Create the repo, install the plugin, run `/flow:init`. New projects start in
   `enforce` mode: the hooks block edits outside the item's claim and destructive git
   commands, and Claude cannot finish while the item's checks fail.
2. First item: `/flow:quiz "Playwright foundation and bus skeleton"`. It runs alone, so
   it cannot conflict with anything.
3. From the second item on: `/flow:quiz` -> `/flow:plan` -> `/flow:build` ->
   `/flow:review` -> `/flow:ship`. Keep at most two items active until the flow feels
   routine. When two items pass review at the same time, `/flow:integrate` comes before
   either ships.
4. To polish one component: `/flow:ux-audit <component>`, then `/flow:quiz <ID>` to pick
   the findings, and the same flow from there.

## Adopting it in an existing project

1. Run `/flow:init` on a `workflow-setup` branch. It adds its own files and merges short
   blocks into CLAUDE.md, `.claude/settings.json`, `.gitignore`, package.json and
   next.config, showing each change; it never overwrites a file you wrote. It starts in
   `warn` mode: claim, spec and git-safety problems are reported, not blocked, so old
   code does not trip them on every edit. The design guard still asks before Claude
   changes a design file you edited or approved, and design.lock.json changes only
   through design.py.
2. Run `/flow:refactor-scan`. It measures imports that cross feature boundaries, import
   cycles, the most imported files, string-keyed messaging, flows without tests and
   security findings, and writes a backlog to `docs/refactor/`.
3. Work the backlog in order: tests that lock today's behavior first, then security
   blockers, then the bus skeleton with one ported interaction, then one item per
   coupling pair. Refactor items never change behavior, and the coupling numbers may
   only go down.
4. After about a week of warnings, set `"mode": "enforce"` in `.flow/config.json`
   yourself. Agents are not allowed to change that file in enforce mode.

## Token budget

| | lean | standard | deep |
|---|---|---|---|
| Used for | tier S fixes | tier M features and refactors | auth, payments, RLS, cross-domain, escalated |
| Session | sonnet, low effort | sonnet, medium effort | opus, high effort |
| Subagents | review on haiku; the rest in the main session | design on opus, the rest on sonnet | opus for design, build and review |
| Handoff point | 40% of context | 60% | 75% |
| Planning effort | medium | high | max (also for any item marked complex) |

There is no tier L budget: the quiz splits an item that big into M items.

`/flow:build` claims the item's files, creates its worktree next to the repository and
prints the session command for its level, for example
`cd ../note-noodle-ITEM-042 && claude --model sonnet --effort medium --autocompact 250000`.
An item moves up one level only on a logged trigger: a test that keeps failing after an
advisor question, two review blocks for the same reason, a large impact set, or
security-critical code. Other savings: most commands stay out of context until typed,
stack rules load only for matching files, subagents skip CLAUDE.md where they carry
their own rules, and `/flow:handoff` plus `/clear` replaces long conversations. The
status line shows the item, its budget, context use and cache hits. Details and the
Claude Code behavior each choice relies on: [docs/token-budget.md](docs/token-budget.md).

## Security checks

`security/checks.yaml` holds 91 checks for Next.js, React, Supabase, Stripe, Resend,
Vercel, GitHub Actions, Claude Code itself, Rust, Axum, Tauri, Android wrappers,
Cloudflare and Docker, plus version floors taken from vendor advisories. Each project
gets the checks its stack needs; each item gets the ones its files trigger. See
[security/README.md](security/README.md).

## What `/flow:init` adds to a repository

| Path | Purpose |
|---|---|
| `.flow/config.json` | Mode (warn or enforce), base branch, WIP limit, shared files, domains, budgets, clarification mode, design approval, baseline and integration checks, cost budget |
| `.flow/statusline.py` | Status line: item, budget, context, cache, cost; writes the usage ledger |
| `CLAUDE.md` block | Short workflow rules and compaction instructions |
| `.claude/rules/flow-*.md` | Stack rules that load only for matching files |
| `.claude/settings.json` | Secret-file deny rules, advisor model, auto-compact window, optional sandbox |
| `security/` | Check registry and runner, so CI can run them without the plugin |
| `docs/items/` | One folder per item: spec, UML, performance, baseline, impact, security checklist, audit, review, handoff, metrics |
| `src/bus/`, `scripts/bus-gen.mjs` | Message bus template and its generator (Next.js) |

It also adds `.flow/state/` to .gitignore, the `bus:gen` and `bus:check` scripts to
package.json and a `NEXT_PUBLIC_E2E` default to next.config, and sets up Graphify and
Playwright when they are missing.

## Repository layout

```
.claude-plugin/   plugin.json, marketplace.json
agents/           architect, auditor, e2e-author, implementer, reviewer, second-opinion
skills/           one folder per /flow: command
hooks/            hooks.json and flow_hook.py (design guard, claims, git safety, test gate, session restore)
scripts/          schedule.py (claims, waves, capacity, metrics), design.py (design lock and approval),
                  trace.py (spec-design-test sync), integrate.py (integration gate),
                  ux_probe.mjs (Playwright UX evidence), coupling.py (refactor metrics), flow_lib.py
security/         checks.yaml, checks.py
templates/        config, CLAUDE.md block, rules, settings, status line, spec, baseline, performance,
                  audit, UX states, Next.js bus
docs/             flow.md, token-budget.md, design/ (the design of each plugin change)
tests/            test_workflow.py
```

Run the tests with `python3 tests/test_workflow.py` (standard library only; uses Node
for the bus generator test, and Node with Playwright for the UX probe test, when they
are installed).

## License

MIT
