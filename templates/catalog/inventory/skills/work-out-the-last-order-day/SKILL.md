---
name: work-out-the-last-order-day
description: Work out the date an item runs out at the expected sales rate and the last day an order can be placed and still land before then, using observed lead time; say which items are already past that day and by how many days.
---
# Work out the last order day

When to use: for every item the weekly list flags as at risk; for every stock question ("can we run a promotion next
month?"); and for every "why did we run out" question.

Steps:
1. Stock on hand, from the latest stock export, with its date and time. A stock export older than seven days is said
   first on the page.
2. Expected daily sales for the period. Use the clean average from `skills/compute-a-reorder-point/SKILL.md` by
   default. For a promotion or a season, use the uplift the owner gave. If none was given, ask once; do not invent one.
3. Days of stock = stock on hand / expected daily sales. Date zero = stock date + days of stock. Work out the date at
   the normal rate too, so the gap between the two is visible.
4. Last order day = date zero - observed average lead time - two working days for receiving. If the item's last three
   deliveries ran late, use the longest of them instead, and say so.
5. If the last order day is today or past, the item is at risk. Say it on the first line. Days short, if an order is
   placed today = date zero - (today + observed lead time), counted in days. A negative number is the days short.
6. State the order quantity that reaches the reorder point, or the one that covers the gap to the next scheduled
   delivery if the owner has one.

Checks before you finish:
- Each date carries its stock count date and its sales window.
- The lead time is observed, and its source (the deliveries used) is named.
- A promotion or uplift that was not given is marked missing, not assumed.

Example (generic): "Travel mat: 42 in stock on 2026-09-28, selling 3.1 a day (90-day clean average), so zero on
2026-10-11. Observed lead time 18 days; the last order day was 2026-09-21, so the item is already at risk. An order
placed 2026-09-28 lands 2026-10-16, five days after stock runs out."
