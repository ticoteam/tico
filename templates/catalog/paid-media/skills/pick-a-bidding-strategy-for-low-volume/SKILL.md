---
name: pick-a-bidding-strategy-for-low-volume
description: Choose a bidding strategy and its target from how many conversions a campaign really gets in thirty days, set the target from history, and say when a change should wait.
---
# Pick a bidding strategy for low volume

When to use: before a campaign's bidding strategy or target is changed, and when a review finds a campaign that
spends without enough conversions for its strategy.

Steps:
1. Confirm the conversion is tracked and primary (see `knowledge/paid-method.md`). If it is not, the strategy
   decision waits; say so.
2. Count the conversions the campaign recorded in the last 30 days, and the conversions that count toward the
   strategy (values for a value-based target).
3. Choose the strategy from the count:
   - Very low volume (under about 15 in 30 days): a manual or cost-capped strategy with a fixed bid. Automated
     bidding has too few events to learn from.
   - Low volume (about 15 to 30 in 30 days): maximise conversions with a spend cap. Do not set a target yet.
   - Enough volume (about 30 or more a month, or the platform's own minimum): a target cost per result.
4. Set the target from history: the average cost per result over the last 90 days, adjusted by a small margin
   when the aim is to grow. A target far below that starves reach.
5. Check the budget. A daily budget too small for the target stops delivery; say what budget the target needs.
6. Write the change with its evidence and the date it will be read: at least two weeks after applying, and not
   inside the learning window.
7. Record it in `knowledge/changes.md` as proposed, with the strategy, target, budget and the count it was based on.

Checks before you finish:
- The conversion count is from a stated 30-day window and names the action it counts.
- The strategy matches the count, not the wish for automation.
- The target comes from history and the source of that history is named.
- A change inside the learning window is listed as waiting, with the date it can be read.

Example (invented, for illustration): "Demo-request campaign: 9 demo requests in the last 30 days (primary action, CRM
check 2026-10-29), average cost 88 per request over 90 days. Strategy: manual bidding with a 70 per click cap, daily
budget 25. Not a target cost per result: 9 is under 15. Re-check on 2026-11-15 with the 30-day count."
