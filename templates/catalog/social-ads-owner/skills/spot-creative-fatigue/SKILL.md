---
name: spot-creative-fatigue
description: Find a paid social ad whose frequency is rising while its link click-through falls against its own first-week baseline, separate creative fatigue from an auction or audience change, and queue a replacement before its cost per result breaks target.
---
# Spot creative fatigue

When to use: each weekly review, for every live ad with spend in the last seven days; and when a campaign's cost per result rises with no change to its audience or budget.

Steps:
1. Pull the ad-level export for the last eight weeks, or since launch if the ad is newer: week, impressions, frequency, link clicks, link click-through rate and spend. Note the export's date range and any ad paused inside it.
2. Set the baseline: the ad's first full week at a steady budget, with enough impressions to read. An ad with no full week has no baseline. Its status is "too early", not "fresh".
3. For each later week, compare link click-through with the baseline, and read frequency beside it. Fatigue is two weeks running with frequency rising and link click-through below 80 per cent of baseline (a team default in `knowledge/targets.md`).
4. Rule out the auction. Compare CPM over the same weeks with the other ads in the same ad set. A CPM rise shared by every ad is an auction or audience change. Name it, and do not rotate on that alone.
5. Rule out the audience. If the audience shrank (a budget cut, a narrower targeting change, a new exclusion), a rising frequency may come from the audience. Check `knowledge/changes.md` for a change in the same weeks.
6. Log each fatigued ad in `knowledge/creative.md`: its baseline week, the first week of the fall, its replacement status (none, in brief, in production with the named person and date, or in test).
7. Prepare the replacement before the old ad goes. Each replacement tests one idea (the offer, the pain or the proof) and carries its claim check from `knowledge/paid-social-method.md`. Do not pause the only live ad in an ad set until a replacement is live.
8. Set the check: the replacement's link click-through against the old ad's baseline, read after its own count, on the date written in `knowledge/creative.md`.

Checks before you finish:
- Each fatigued ad names its baseline week and the dated trend, not one bad day.
- A CPM rise that every ad in the ad set shares is named as an auction or audience change.
- No ad is paused without its replacement live, and the ad set still has live ads.
- An ad that is too early to call is listed as waiting, with the date it can be read.

Example (invented, for illustration): "Ad 'unboxing v3', prospecting ad set, live 58 days. Baseline week (days 1 to 7): link click-through 1.3 per cent, frequency 1.4. Weeks 6 and 7: frequency 2.8 and 2.9, link click-through 1.0 and 0.9 per cent (77 and 69 per cent of baseline). CPM between 11.60 and 11.80 across all four ads in the ad set over the same weeks, so the auction did not move. Fatigue. Two replacements in brief on 2026-10-28; the first goes live before the old ad is paused."
