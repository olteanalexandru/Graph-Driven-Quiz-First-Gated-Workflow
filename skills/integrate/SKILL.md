---
name: integrate
description: Stage S7a integration gate - items that finished in parallel are merged together in a temporary worktree, tested and reviewed as one change before either ships.
argument-hint: "[ITEM-ID ...]"
disable-model-invocation: true
---

# Integrate $ARGUMENTS

Needed when two or more claimed items have `Verdict: PASS` (`/flow:review`,
`/flow:ship` and the S7a stage in `/flow:next` say so). Runs from any checkout.

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py run $ARGUMENTS` (no IDs: every
   claimed item with a passing review). It merges the items into a temporary worktree
   from the base branch and runs `integration_checks` there.
2. BLOCK (a merge conflict or a failing check): find the item that owns the cause, the
   one that changed the file, contract or behavior the other relies on. Tell the user,
   fix it in that item's worktree (`<ID>: integration fixes`), let its gate pass, review
   it again, then run step 1 again. Conflicts between claimed files mean the impact set
   missed something: add it to that item's impact.json.
3. Checks passed: run the `reviewer` subagent in integration mode with the worktree
   path, the items, the base commit and effort `max`, on the strongest reviewer model
   among the items' budgets. It reads the combined diff for problems no single review
   can see, and writes INTEGRATION-REVIEW.md in the worktree.
4. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py finish <run> --verdict PASS|BLOCK --notes <worktree>/INTEGRATION-REVIEW.md`
   records the result and removes the worktree. BLOCK goes back to step 2.
5. PASS: ship the items one at a time, critical path first (`/flow:ship <ID>`). Each
   ship checks that its branch head is the one that was integrated.
