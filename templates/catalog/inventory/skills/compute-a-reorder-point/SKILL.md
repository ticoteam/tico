---
name: compute-a-reorder-point
description: Compute one item's safety stock and reorder point from its own clean sales and its observed lead times, showing every input, using the variance method when the data allows and the max-minus-average rule when it does not.
---
# Compute a reorder point

When to use: when an item is first planned; each week for items whose sales or lead time moved by more than 20 percent;
and whenever a stock-out or a late delivery is being explained.

Steps:
1. Daily sales. Take the item's daily units sold over the last 90 days, or the season's window for a seasonal item (name
   the window). Remove days the item was out of stock: a day at zero stock understates demand. Count those days and say
   so. Average the rest: d.
2. Demand spread. Take the standard deviation of daily sales over the same clean days: sigma d.
3. Lead time. Use the last six to ten deliveries: days from order to goods received. Average them: L. Take their
   standard deviation: sigma L. Use the supplier's stated lead time only when there are fewer than three recorded
   deliveries, and mark the item.
4. Safety stock. With at least 60 clean days and three or more deliveries, use the variance method:
   SS = z x sqrt(L x sigma d squared + d squared x sigma L squared), with z = 1.65 for a 95 percent cycle service level.
   Otherwise use the fallback, SS = maximum daily sales x maximum lead time - d x L, and say which method was used.
5. Reorder point. ROP = d x L + SS, rounded up to a whole unit.
6. Show the inputs with the result: d, sigma d, L, sigma L, z, SS, ROP, the sales window, the delivery count and the
   method. A reorder point handed over without its inputs is not finished.
7. Write the item's line in `knowledge/items.md` with the date computed. If the reorder point moved by more than 20
   percent since last time, say why on the weekly page.

Checks before you finish:
- Each item names its method, its sales window and its delivery count.
- Out-of-stock days are out of the demand average, and their number is recorded.
- No item uses the stated lead time when three or more deliveries were observed.

Example (generic): "Cork block: 88 clean days, d 2.4, sigma d 0.9. L 21 days from eight deliveries, sigma L 3.5 days.
Variance method, z 1.65: SS = 1.65 x sqrt(21 x 0.81 + 5.76 x 12.25) = 1.65 x 9.36 = 15.4. ROP = 2.4 x 21 + 15.4 = 65.8,
so 66."
