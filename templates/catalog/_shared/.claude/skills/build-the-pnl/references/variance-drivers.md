# Splitting a movement into drivers

A movement is actual minus the comparison (last month, the same month last year, or the budget). Split it into the
drivers below. The pieces must add up to the movement to the cent. What is left is the **unexplained remainder**, shown
with its amount and an owner.

## The fixed driver list

| Driver | Meaning | Usual label |
|---|---|---|
| Volume | More or fewer units at the old price or rate | run-rate |
| Price or rate | A different price per unit, or cost per unit | run-rate |
| Mix | The same total units, shared differently between higher and lower priced things | run-rate |
| New or discontinued | A product, customer, vendor or bot that exists in only one of the two periods | run-rate |
| One-time | Will not recur: a setup fee, a settlement, a migration, a refund batch | one-off |
| Timing | Belongs to another month and reverses: an annual bill expensed at once, a late invoice, an accrual | timing |
| Currency | The same foreign amount at a different exchange rate | run-rate or timing |

Use these words and no others, so twelve months of notes can be compared.

## Order of work

1. Take out **timing** and **one-time** first, each matched to its document. They are the easiest to prove and the most
   misleading if left in.
2. Take out **new or discontinued**: lines present in only one period.
3. Split what is left into **volume**, **price or rate** and **mix** with the formulas below.
4. **Currency** only if the company bills or pays in more than one.
5. Whatever remains is the unexplained remainder.

## One product or one cost: volume and rate

Units and rate for both periods (rate = amount / units).

- Volume = (actual units - prior units) x prior rate
- Rate = (actual rate - prior rate) x actual units
- Check: volume + rate = actual amount - prior amount

For a cost line, "units" is whatever drives it: customers for hosting, tasks or requests for AI, tickets for support,
payment volume for processor fees.

## Several products: volume, mix and price

For each product i: units and price in both periods. Totals: all units in each period. Share = a product's units / total
units. Prior average price = prior revenue / prior total units.

- Volume = (actual total units - prior total units) x prior average price
- Mix = sum over products of (actual share - prior share) x actual total units x prior price
- Price = sum over products of (actual price - prior price) x actual units
- Check: volume + mix + price = actual revenue - prior revenue

## Rules

- **Add it again.** Recompute the check line after writing the drivers. A split that is off by a rounding cent is
  fixed in the largest driver and noted.
- **A driver names a document and a date.** An invoice, a contract, an order, a usage export, a payroll run, a bank
  row. "Seasonality" and "growth" are not documents.
- **Three drivers usually carry most of a movement.** Stop splitting when the remainder is under the explanation
  threshold in `knowledge/pnl-design.md`; show it as "other, not split".
- **Never name a person as a cause.** Name the event.
- **You explain; you do not adjust.** A driver that is really an error becomes a proposed correction through
  `review-the-books-for-unusual-entries`, and the P&L shows the number as booked until it is corrected.
- **Against a budget**, first say whether the budget changed during the month. A changed budget explains part of the
  gap by itself.
- **Complete periods only.** Do not compare a part-month with a full month.

## What each label does next

- **Timing**: nothing changes in the forecast. Name the month it reverses in. If the cause is an annual bill expensed
  at once on an accrual book, propose moving the unexpired part to prepaid expenses (`close-the-month`).
- **One-off**: nothing changes in the forecast. It is listed, so it is not mistaken for a trend next month.
- **Run-rate**: the forecast changes. Send the driver, the new rate and the document to `forecast-cash-and-budget`.

## Worked example (invented)

Revenue 84,300 against 78,000 last month: +6,300.

| | Prior units | Prior price | Actual units | Actual price |
|---|---|---|---|---|
| Basic | 300 | 100 | 310 | 100 |
| Pro | 120 | 400 | 127 | 420 |

Prior revenue: 30,000 + 48,000 = 78,000. Actual: 31,000 + 53,340 = 84,340. A one-time credit of 40 was issued on
2026-10-12 (credit note CN-88), so booked revenue is 84,300.

- One-time: -40 (CN-88).
- Prior total units 420, actual 437. Prior average price 78,000 / 420 = 185.714.
- Volume: 17 x 185.714 = 3,157.14.
- Mix: Basic (310/437 - 300/420) x 437 x 100 = -214.29; Pro (127/437 - 120/420) x 437 x 400 = +857.14. Total 642.86.
- Price: Basic 0; Pro 20 x 127 = 2,540 (price change of 2026-10-01, pricing page and order forms).
- Check: -40 + 3,157.14 + 642.86 + 2,540 = 6,300.00. Remainder 0.
