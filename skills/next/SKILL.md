---
name: next
description: Show every item's stage and budget, active claims and conflicts, the capacity left (concurrency, rate limit, cost budget), and which items can start now without overlapping.
disable-model-invocation: true
effort: low
---

Run and show the output as is, without commentary:

`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py status; python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py next`

If the user asks for the whole plan, also run `schedule.py waves`; for spend and
estimates, `schedule.py usage`.
