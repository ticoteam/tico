A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme shop reorder list, Mon 2026-09-28

Sample output for Acme's online shop, the fictional team's store of branded studio gear. Every
item, supplier and number is invented. Nothing has been ordered. First draft, not yet reviewed.

**Headline: 3 items to order this week, in 2 purchase orders totalling $6,337.50, drafted for review. The travel mat runs
out 5 days before an order placed today can land.**

## At risk
- **Travel mat, grey (TM-GRY).** 42 in stock, selling 3.1 a day (90 days to 2026-09-27), so zero on
  2026-10-11. Lead time 18 days observed (supplier states 14). An order today lands 2026-10-16: 5 days
  short. Options for a human: order today and accept the gap, or ask Fernhill for part-shipment.

## Order this week
| Item | Stock | Daily sales | Lead time | Safety stock | Reorder point | Order |
|---|---|---|---|---|---|---|
| Travel mat, grey | 42 | 3.1 | 18 d | 25 | 81 | 200 (case 50) |
| Cork block | 60 | 2.4 | 21 d | 22 | 72 | 150 |
| Strap, 2.5 m | 95 | 4.0 | 12 d | 18 | 66 | not yet: 29 above point |
| Mat towel | 30 | 1.2 | 21 d | 14 | 39 | 100 (minimum) |

Method: safety stock by the variance method (z 1.65) on 90 days of clean sales, out-of-stock days removed, and the last
six to eight observed deliveries. Each item's inputs are in `items.md`. Reorder point = daily sales x lead time + safety
stock, rounded up: travel mat 3.1 x 18 + 25 = 80.8, so 81.

## Purchase orders (on the task for review)
- Fernhill Goods: travel mat x200 at $18.40, mat towel x100 at $9.10: $4,590 (price list 2026-08-01).
- Oakline Supply: cork block x150 at $11.65: $1,747.50 (price list 2026-07-15).

## Overstock
- Studio tote: 14 months of cover (210 in stock), $3,118.50 tied up at cost (price list 2026-09-01). Options for the
  owner: pause reorders until cover is under 4 months (cheapest, reversible); bundle with the travel mat for two weeks.

## Supplier delays
- Fernhill: last 3 deliveries took 17, 18 and 19 days against 14 stated.

## Sources
- Sales export 2026-06-30 to 2026-09-27 and stock export 2026-09-28 07:00 (task T-512); items.md
```
