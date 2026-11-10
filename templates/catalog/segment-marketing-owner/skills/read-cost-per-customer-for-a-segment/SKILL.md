---
name: read-cost-per-customer-for-a-segment
description: Compute one customer segment's new customers and cost per customer for a month from Tico's per-channel numbers, splitting each channel's spend and customers by a named method, labelling every estimate, and reconciling the segments back to the channel totals.
---
# Read a segment's cost per customer

When to use: each weekly segment growth review, and before any cost per customer, split or change in either goes into a review, an ask or a message.

Steps:
1. Pull the channel rows for the month with `tico growth cohort list --month YYYY-MM`: one row per channel, plus Unknown. Write each channel's spend, bot cost and new customers. A month with no inputs is blank.
2. Count the segment's new customers for the month by the counting rule in `knowledge/segment.md`, from the sign-up export on the task or the CRM segment field where it is connected. Write the source and its date.
3. For each channel that spends on the segment, pick one split method, in this order of preference, and write which one:
   - **Direct, actual.** The channel's spend and customers are for this segment alone. Use the figure from the source `tico growth source list` names for it.
   - **Audience split, estimate.** The channel serves more than one segment, and its own targeting report gives a share. Multiply the channel's spend and bot cost by that share.
   - **Share of new customers, estimate.** No targeting split exists. Multiply by the segment's share of that channel's new customers. This assumes the segments cost the same to win, so say so.
4. Add up the segment's spend, bot cost and new customers across its channels. Keep a row for customers with no segment.
5. Compute cost per customer = (spend + bot cost) ÷ new customers, rounded to whole dollars. Leave it blank when there are no new customers.
6. Reconcile. The segments' new customers, plus the no-segment row, equal each channel's total for the month in step 1. Spend and bot cost reconcile the same way. Write any gap with its size and do not force it.
7. Compare with the goal, floor and ceiling in `knowledge/segment.md`, read from `tico kpi show <id>`. Write the reading: the value, the counts, the split methods, the no-segment share, the month and the source dates.

Checks before you finish:
- Every estimate says "estimate" beside its number.
- Blank is written as blank, not as 0.
- The reconciliation is written, with any gap.
- The no-segment share is named, with the range it implies if it is above zero.

Example (invented, for illustration): "Creators, October 2026. Search Ads: spend $3,100, direct actual, 58 creators. Social Ads: spend $2,000, audience split estimate at 60%, so $1,200, and 26 creators. Bot cost $380, split by new customers, estimate. Cost per creator ($4,300 + $380) ÷ 84 = $56, against a $60 ceiling. Reconciled: 84 creators + 57 other segments + 9 with no segment = 150 channel new customers."
