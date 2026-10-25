# Adaptive token budget

Each item gets a budget level from its size, and moves up only when the work proves
harder than planned. The levels live in `.flow/config.json` under `budgets`, so you can
tune them per project. `/flow:budget` shows or changes an item's level.

| | lean | standard | deep |
|---|---|---|---|
| Used for | tier S: small fixes, copy, config | tier M: features, refactor items | auth, payments, RLS, cross-domain, escalated items |
| Session | `--model sonnet --effort low --autocompact 150000` | `--model sonnet --effort medium --autocompact 250000` | `--model opus --effort high --autocompact 400000` |
| Design (architect) | none | opus | opus |
| Acceptance specs | main session | sonnet | sonnet |
| Implementation | main session | sonnet | opus |
| Review | haiku | sonnet | opus |
| Reading | graph, then at most 3 files | graph, then up to 8 files | graph, then what the design needs |
| Impact | direct callers and callees | plus one hop of importers | plus a second hop of callers, ranked by test links |
| Gate tests | unit + the item's specs | plus `@smoke` | full Playwright suite |
| Handoff point | 40% of context | 60% | 75% |
| Planning effort | medium | high | max |
| Audit (auditor) | main session | sonnet | opus |
| Starting estimate | $2 | $8 | $25 |

There is no tier L budget: the quiz splits an item that big into M items first.
`/flow:build` claims the item's files, creates its worktree next to the repository and
prints the session command with the Session flags, for example
`cd ../<repo>-ITEM-042 && claude --model sonnet --effort medium --autocompact 250000`.

The advisor (`advisorModel: opus` in project settings) is available at every level:
the working model asks the stronger one only when it decides it needs to, and those
calls are billed at the advisor model's rates.

## Moving up

One level up, logged in the spec's Budget log, on any of these:

1. a test still fails after one fix and one advisor question;
2. review BLOCKs twice for the same reason;
3. the impact set spans more than 2 domains or about 25 nodes;
4. the security checklist has blocker items for auth, payments or RLS;
5. you ask.

Budgets do not carry over between items.

## Effort for planning

Reasoning effort is spent where a wrong answer costs most: the design, the impact set,
re-planning and the integration review. An item is complex when its spec says
`complex: true` (auth, payments, RLS, an API or schema contract change, more than two
domains), its budget is deep, or planning finds it so; complex planning runs at effort
`max`, which Claude Code lowers to the highest level the model supports. Other items
plan at the budget's `planning_effort`. Implementation keeps the session's effort.

Workload decides how much analysis a design gets: `performance.md` records how often
each path runs, how much data it sees, and its latency and memory limits. Complexity
analysis is written only for paths over a threshold, and critical paths get budgets
that tests measure.

## Running items in parallel

More agents are not more progress: two items that read the same code load the same
context twice, and two items that touch the same contract cost a rework when they
meet. `schedule.py next` starts an item beside the active ones only when:

1. hard checks pass: no overlapping files, folders or hot tokens, and no contract one
   item changes that the other changes or uses (`impact.json` `contracts`; the user of
   a contract goes after the item that changes it);
2. soft checks pass: they share less than half of the smaller item's graph nodes
   (`shared_context_limit`), and they are not both high rework risk in one domain;
3. capacity allows it: the WIP limit, the 5-hour rate limit floor
   (`rate_limit_floor`, default: keep 20% free) and the global budget
   (`orchestration.cost_budget_usd` per `budget_window`).

Capacity comes from a usage ledger: the status line writes each session's item, cost
and rate-limit use to `<git-common-dir>/flow-usage/` (the status line receives the
session's cost, not a running token count, so cost is the consumption measure).
Without it, every active item
counts at its estimate times `unknown_usage_factor` (1.5). Estimates start at
`estimate_usd` per level and become the median cost of shipped items once a level has
three (`docs/items/<ID>/metrics.json`, written by `/flow:ship`). The claim refuses past
any hard limit; only the user can pass `--force`. `schedule.py usage` shows the numbers.

Items that finish together go through `/flow:integrate` before either ships: their
branches are merged in a temporary worktree, `integration_checks` run on the result, and
a combined review looks for duplicated work and contract mismatches. Metrics record
rework rounds (review, audit and integration fixes), gate failures, escalations and the
items each one ran beside, so later scheduling can see which pairings cost more.

## How each lever works (Claude Code facts this design relies on)

- **Prompt caching.** Automatic. Each model has its own cache, so switching `/model`
  mid-session starts over. Changing effort keeps the cache on the 5.5-generation models.
  CLAUDE.md edits, plugin skills, hooks and plan mode do not invalidate it. Default
  lifetime: 1 hour for the main conversation on a Claude subscription within plan
  usage, 5 minutes with an API key or usage credits, 5 minutes for subagents
  (`promptCacheTtl`, `subagentPromptCacheTtl`). Hence: never switch the session model
  mid-item; escalate through subagent models, or hand off and start a new session.
- **Compaction.** Auto-compaction runs near the auto-compact window (about 967K tokens
  by default on 1M-context models). Every turn re-reads the whole context, so a smaller
  window (`--autocompact`, setting `autoCompactWindow`) keeps each turn cheaper. The
  `# Compact instructions` section in CLAUDE.md says what to keep. After compaction the
  SessionStart hook (source `compact`) puts back the item's stage, budget, claim, last
  check results and handoff.
- **Clearing.** `/clear` starts an empty conversation; CLAUDE.md reloads. There is no
  built-in handoff, so `/flow:handoff` writes `docs/items/<ID>/handoff.md` and the
  SessionStart hook loads it plus the item's stage, budget and last check results.
- **Subagents.** Each has its own context and returns only a summary. `omitClaudeMd`
  keeps CLAUDE.md out of the architect, reviewer and second-opinion agents, which carry
  their own rules. Small items skip subagents entirely: one context, one cache.
- **Always-loaded text.** Most flow skills set `disable-model-invocation: true`, so their
  descriptions stay out of context until you type the command; `budget` and `handoff`
  stay invocable because the build and review steps call them. The CLAUDE.md block is
  about 25 lines; stack rules live in `.claude/rules/` with `paths:` and load only when
  matching files are read. MCP tool search (on by default) loads tool schemas on demand.
- **Output.** Test commands use line reporters and `--max-failures`; the Stop hook
  passes back only the last 25 lines of each failing check; Graphify answers before
  files are opened.
- **Visibility.** The status line shows the item, its budget, context use with a
  handoff prompt at the budget's limit, prompt cache hit ratio, cost and the 5-hour
  plan usage. `/context` and `/usage` give the detail.

Sources: Claude Code docs pages
[prompt-caching](https://code.claude.com/docs/en/prompt-caching),
[model-config](https://code.claude.com/docs/en/model-config),
[costs](https://code.claude.com/docs/en/costs),
[context-window](https://code.claude.com/docs/en/context-window),
[advisor](https://code.claude.com/docs/en/advisor),
[sub-agents](https://code.claude.com/docs/en/sub-agents),
[skills](https://code.claude.com/docs/en/skills),
[hooks](https://code.claude.com/docs/en/hooks),
[statusline](https://code.claude.com/docs/en/statusline).
