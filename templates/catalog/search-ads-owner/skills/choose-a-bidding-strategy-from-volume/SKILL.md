---
name: choose-a-bidding-strategy-from-volume
description: Count a campaign's primary conversions over the last 30 days, compare the count with the minimum its bidding strategy needs, and set or keep the strategy and target the volume can support, with the decision and the date it is checked again.
---
# Choose a bidding strategy from volume

When to use: before a campaign's bidding strategy or target is set or changed, when Watch or a bet finds a target far from history, and when a campaign has just gained or lost conversions.

Steps:
1. Count the primary conversions for the campaign over the last 30 days, from the platform's own count for that conversion action. Do not add counts from another platform, and do not count secondary actions.
2. Find the minimum for the strategy. Target ROAS: 15 conversions with values in the last 30 days (Google's guidance, carried from the search-ads-owner method). Target CPA: a workable volume is about 30 or more a month (third-party guidance, carried). Use the platform's own figure when the account shows one.
3. Under the minimum: keep a manual bid, or an enhanced one, with a cap on the cost per click. No target is set. Judge the campaign on its cost per result over 30 days against its target in `knowledge/targets.md`, and say that the target is a goal, not a bid.
4. At or over the minimum: set the target at the 90-day average cost per result, not below it. A target below history starves delivery, and the volume falls.
5. Check the conversion action the strategy learns from. It must be the primary one, counted once. If a second action also feeds the strategy, remove it before deciding, and route the cause to the tracking owner.
6. Record the decision in `knowledge/structure.md`: the strategy, the target if any, the 30-day count, the minimum, the date it will be checked again, and the count that would change the decision.
7. Prepare any change with `playbooks/prepare-a-search-ads-change.md`. A strategy change restarts learning, so it is not made inside a learning period unless something unusual shows. A change of strategy or target is a bet: it goes live only after a person OKs its Go-live page.

Checks before you finish:
- The count is the primary conversion's, for this campaign, over 30 days.
- The decision names the minimum and says whether the count meets it.
- No target is set below the 90-day average cost per result.
- The decision has a date, and the count that would change it is written down.

Example (invented, for illustration): "Fleet quotes: 3 quotes a week, about 13 in the last 30 days. That is under the 15-conversion minimum for Target ROAS and under a workable Target CPA of about 30 a month. The campaign stays on a manual bid with a cost-per-click cap of $6. Its 90-day average cost per quote is $204, and the $180 target in knowledge/targets.md stays as the goal, not a bid target. Next check on 2026-12-01, at the 30-day count."
