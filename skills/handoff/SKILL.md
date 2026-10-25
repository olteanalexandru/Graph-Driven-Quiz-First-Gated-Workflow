---
name: handoff
description: Write the active item's handoff file so the session can be cleared, compacted or continued elsewhere without losing anything.
effort: low
---

# Handoff

Write `docs/items/<ID>/handoff.md` for the item on the current branch (replace the old
one). At most 40 lines, facts only, file:line references instead of code:

```
# <ID> handoff - <date time>, stage <S#>, budget <level>
## Done
## In progress            (the exact step, file:line)
## Next                   (ordered, one action per line)
## Decided while building (not yet in spec.md; add D# rows there if they matter)
## Failing                (test name + last error line)
## Tried and dropped      (so the next session does not repeat it)
```

Then commit it on the item branch (`<ID>: handoff`) and tell the user, in one line, to
run `/clear`. The SessionStart hook loads this file and the item's state into the new
context, so nothing else needs to be pasted.

Use it:
- between S4 and S5, and before /flow:review;
- when context passes the budget's limit (the status line shows `-> /flow:handoff`);
- before a break longer than the prompt cache lifetime, because reloading a one-page
  handoff costs less than rebuilding a cache for a long conversation;
- before moving the item to another machine or session.
