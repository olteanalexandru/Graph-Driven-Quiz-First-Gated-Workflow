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
