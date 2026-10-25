---
name: ship
description: Stage S8 for one reviewed item - integration check, rebase, full test suite, release security checks and gate, metrics, merge, refresh the graph, release the claim.
argument-hint: "<ITEM-ID>"
disable-model-invocation: true
---

# Ship $0

1. `docs/items/$0/review.md` must start with `Verdict: PASS`.
2. Integration, before the rebase changes the branch head:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py verify $0`. If it fails, another
   finished item has to be checked together with this one: ask the user to run
   `/flow:integrate` and stop.
3. Rebase on the latest base branch (`git fetch` then `git rebase origin/<base>`). Resolve
   conflicts only inside the claim; anything else goes back to the user.
4. Full checks: unit tests, the full Playwright suite (or let CI run it on the pull
   request), a production build without NEXT_PUBLIC_E2E,
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/trace.py check $0 --stage review`, then
   `uv run security/checks.py run --stage S8` and `uv run security/checks.py gate $0`.
5. Metrics: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py record $0` writes
   docs/items/$0/metrics.json (cost from the usage ledger, rework rounds, gate failures,
   escalations, items it ran beside). The scheduler uses them for future estimates.
6. Set `status: done` in the spec and commit it with metrics.json.
7. Merge: with gh available, open a pull request (`gh pr create --fill`) and let CI and
   the user merge it. Without gh, squash-merge locally into the base branch and ask the
   user to push. Never push the base branch yourself.
8. After the merge, from the main checkout: `graphify update .` (add `--force` when files
   were deleted), `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/schedule.py release $0`,
   `git worktree remove ../<repo>-$0`, `git branch -d $0`, then show `schedule.py next`.
