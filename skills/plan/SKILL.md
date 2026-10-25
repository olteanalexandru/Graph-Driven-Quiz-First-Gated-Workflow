---
name: plan
description: Stages S2-S3 for an approved item - design (UML, data structures, messages), impact set and file claim, security checklist, and whether it can start now.
argument-hint: "<ITEM-ID> [--no-design]"
disable-model-invocation: true
---

# Plan $0

Run on the base branch; these are planning docs. To re-plan an item that is already
being built, run this in its worktree instead: the claim is refreshed in step 6.

1. Read docs/items/$0/spec.md. Stop if `status` is not `approved`. Note `tier` and `budget`.
2. Design (skip for tier S or `--no-design`): run the `architect` subagent with the item
   ID and the model from the budget's `agents.architect` (null: write the design in this
   session following `${CLAUDE_PLUGIN_ROOT}/agents/architect.md`). It writes
   docs/items/$0/uml/, data-structures.md and messages.md, and returns seed nodes.
3. Impact, in this session with Graphify:
   - graphify_impact on each seed node; it covers direct callers and callees only, so
     add one hop of importers with graphify_imports_exports (lean budget: skip this hop);
   - deep budget: also add a second hop of callers (graphify_callers on each direct
     caller), and run impact_and_risk on the seeds to note the least-tested code;
   - add the planned new files;
   - add hot tokens from .flow/config.json `hot_files` the item needs (for example
     `bus-registry` when it adds messages).
   Write docs/items/$0/impact.json:
   `{"item": "$0", "paths": [...], "new_files": [...], "hot": [...], "nodes": [...]}`.
   Paths may be folders ending in `/`.
4. Budget check: if the impact set spans more than 2 domains or about 25 nodes, or the
   security checklist has blocker items for auth, payments or RLS, raise the budget one
   level and add a line to the spec's Budget log.
5. Security: `uv run security/checks.py item $0 --write`.
6. Schedule: if $0 already has a claim (re-planning), refresh it with
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py claim $0`; otherwise run
   `schedule.py next` and report whether $0 can start now or what it waits for.
7. Commit only docs/items/$0/: `$0: design and impact`. Next step for the user:
   `/flow:build $0`.
