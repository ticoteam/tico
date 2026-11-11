---
name: diagnose-a-rising-cost-per-result
description: Split a rise in a paid social campaign's cost per result into its three factors (auction price, link click-through and conversion rate), name the most likely cause, and say which check would confirm it before any change is proposed.
---
# Diagnose a rising cost per result

When to use: a campaign's cost per primary result is over its target for two weeks running, or has risen by more than the budget step in `knowledge/targets.md` in one week.

The identity behind it: cost per result = CPM ÷ (1,000 × link click-through rate × conversion rate), where conversion rate = primary conversions ÷ link clicks. Cost per result only rises when one of the three factors worsens, or several do.

Steps:
1. Take the campaign's weekly rows for the last eight weeks: spend, impressions, CPM, link clicks, link click-through rate, primary conversions and cost per primary conversion.
2. Check the arithmetic before you diagnose. Recompute each week's cost per result from the three factors. If it does not reproduce the platform's figure within rounding, the export mixes bases (all clicks with link clicks, or secondary with primary conversions). Fix the base first.
3. For this week, compare each factor with its own eight-week average. The factor that moved furthest in ratio terms is the first suspect.
4. Name the likely cause from the factor that moved:
   - CPM up, click-through and conversion rate steady: the auction got dearer or the audience saturated. Check the same week's CPM for the account's other campaigns. If they rose too, the cause is the market, not this campaign.
   - Click-through down, CPM and conversion rate steady: creative fatigue or an offer that has stopped landing. Read `skills/spot-creative-fatigue/SKILL.md`.
   - Conversion rate down, CPM and click-through steady: the landing page, the offer, the form or the tracking. Check the primary event still fires once, on the same page, for the same action. A changed page goes to the conversion channel's owner with the evidence.
   - Two factors moved: name both, and rank them by how far each moved.
5. Check the tracking before you accept the conclusion. If the primary event count is off against the CRM for the same week and source (once that is read), the rise may be the tracking, not the performance. Route it to the tracking owner.
6. Check the changes in the last 14 days in `knowledge/changes.md`. A budget or audience change in that window is a candidate cause. If one exists, the answer may be "wait until the date it can be read", not a new change.
7. Write the diagnosis on the task: the three factors with their eight-week averages and this week's values, the cause named as likely, the check that would confirm it, and either the fix (upkeep your channel playbook lists, or an idea filed) or "wait until <date>".

Checks before you finish:
- The three factors are on one base, and they reproduce the cost per result.
- One cause is named as likely, with the check that would confirm it. It is not called certain unless the check was done.
- A rise shared by the account's other campaigns is named as market, not as this campaign's fault.
- A change in the last 14 days is named, or ruled out, before any new change is proposed.

Example (invented, for illustration): "Week 1: CPM 12.00, link click-through 1.0 per cent, conversion rate 2.0 per cent. Cost per result = 0.012 ÷ (0.010 × 0.020) = 60.00. Week 2: CPM 12.00, link click-through 0.7 per cent, conversion rate 2.0 per cent. Cost per result = 0.012 ÷ (0.007 × 0.020) = 85.71, up 43 per cent. Only click-through moved, by 30 per cent. The cause is in the creative or in how the audience responds, not in the auction or the page. Next check: the frequency and the other ads in the ad set (`spot-creative-fatigue`)."
