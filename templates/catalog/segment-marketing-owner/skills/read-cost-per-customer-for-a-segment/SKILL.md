---
name: read-cost-per-customer-for-a-segment
description: Read one customer segment's new customers, direct and loaded cost per customer, retention and observed LTV for a month from Growth, which computes them by segment, and write the reading with its counts and health.
metadata:
  function: marketing
---
# Read a segment's cost per customer

When to use: each weekly segment growth review, and before any cost per customer, or a change in it, goes into a review, an ask or a message.

Growth computes these by segment. Never split spend or customers by hand.

Steps:
1. Run `tico growth segment show <slug>`. Write the month, new customers, direct CAC, loaded CAC, kept at 30 and 90 days and the observed LTV it shows, with the counts behind each.
2. Name the two costs. **Direct** is the spend of the channels set to this segment. **Loaded** adds company-wide spend in proportion to this segment's share of new customers. Use direct for payback, and loaded when you compare segments.
3. Write the health line: customers with **no segment**, and the **mismatch** count (customers whose channel's spend is for another segment). If either is above 20% of new customers, ask the Growth Manager (`growth-manager`) before you read the cost as true.
4. If the read says spend is not split by segment, the team has no segments yet. Ask the Growth Manager (`growth-manager`) to set them up in Growth, and write the cost per customer as blank.
5. A figure that says not yet, too few or no spend is written that way, never as 0. Retention waits for the segment's churn days, so a recent month fills later than its channel's.
6. To compare with other segments or weeks, use `tico growth cohort list --by segment --month YYYY-MM`. Compare with the goal and ceiling in `knowledge/segment.md`, read from `tico kpi show <id>`.

Example (invented, for illustration): "Rental, September 2026: 20 new customers. Direct CAC $20 (partners), loaded $31.67 with brand's company-wide spend. Day 30 is not yet, it fills on 29 November (30 churn days). Health: 10 with no segment, 2 mismatched. Against a $40 ceiling."
