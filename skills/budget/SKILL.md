---
name: budget
description: Show or change an item's token budget (lean, standard, deep) - session model and effort, subagent models, reading limit, gate tests, handoff point - and the command that applies it.
argument-hint: "[ITEM-ID] [lean|standard|deep] [reason]"
effort: low
---

# Token budget

Item: `$0` when it looks like an item ID, otherwise the item on the current branch.
Settings for each level are in `.flow/config.json` under `budgets`.

Show, in under 12 lines:
- level and what it is for;
- the session command, run in the item's worktree:
  `claude --model <model> --effort <effort> --autocompact <tokens>`;
- subagent models per stage (`null` = the main session does that stage itself, with
  the rules in `${CLAUDE_PLUGIN_ROOT}/agents/<name>.md`);
- reading limit, impact depth, gate tests (`stop_checks`), handoff point (% context).

To change it (a level was given): set `budget:` in docs/items/<ID>/spec.md and append to
its Budget log `<date> <old> -> <new>: <reason>`. Then apply it the cheap way:
- subagent models: take effect at the next subagent call;
- effort only: `/effort <level>` (on the current 5.5-generation models this keeps the
  prompt cache);
- session model: write a handoff (`handoff` skill), ask the user to `/clear` and start
  the new session command. A model switch starts a new prompt cache anyway, and a fresh
  session with a one-page handoff costs less than continuing a long conversation.

Raise the budget one level only on these triggers, and log which one:
1. a test still fails after one fix and one advisor question;
2. review BLOCKs twice for the same reason;
3. the impact set spans more than 2 domains or about 25 nodes (checked in /flow:plan);
4. the item's security checklist has blocker items for auth, payments or RLS;
5. the user asks.
Budgets do not carry over: every new item starts at its own level (tier S lean, tier M
standard, deep when it touches auth, payments or RLS).
