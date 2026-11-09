---
name: build-the-13-week-cash-forecast
description: Build or roll a 13-week cash forecast by week and line from bank balances and dated sources, using the direct method. Then compare last week's forecast with actual cash, and log each miss with its cause and owner.
---
# Build the 13-week cash forecast

When to use: when you build the first forecast, roll it forward each Monday, or a human asks what cash will look like in
the coming weeks.

Steps:
1. Fix the starting cash. Use the bank balances supplied for the week, by account, stated as "bank available" (what can be
   spent today). Books cash after the cut-off is a separate line, never mixed in. A balance feed is a snapshot, not a
   reconciliation; say which one you used.
2. Set the lines: collections, payroll, payables, rent, debt service, tax and other. Use 8 to 12 lines. Each names its
   source: the aging report, the payment run, the payroll register, the loan schedule, the tax calendar.
3. Fill the near weeks (1 to 4) from documents: the invoices due, the bills scheduled, the payroll date. Fill later weeks
   (5 to 13) as estimates from collection history and known commitments, and label them as estimates.
4. Time collections from history, not from invoice terms. A customer who usually pays 10 days late moves the receipt into a
   later week. A payment takes about one business day to post on both sides, so a Friday payment may show on Monday.
5. Roll each week. Drop last week's column, add week 13, and put last week's actuals beside the forecast for each line. A
   line that missed by more than 10 percent gets a cause from a source, or is re-based and marked as re-based.
6. Log each miss in a variance log: line, week, forecast, actual, cause, owner and date. A miss with no cause stays open, with an owner.
7. Find the lowest week, its closing cash, and the gap to the minimum cash line in `knowledge/thresholds.md`. Runway is cash
   divided by the average net monthly burn of the last three closed months, with its inputs shown.

Checks before you finish:
- Every row names its source and date; an unsourced figure is left out.
- A missing export is marked "not supplied", never zero.
- Near and later weeks are labelled differently.
- Last week's forecast was compared with actuals before the new one was written.

Example (invented, for illustration): "Opening bank available 1.42M (balances supplied 2026-10-12). Week of 2026-10-12:
collections 58k (aging report; two customers usually pay 10 days late), payroll 219k (payroll register, pay date 10-15),
payables 41k (payment run of 10-14, one bill held). Closing 1.42M + 58k - 219k - 41k = 1.218M, the lowest week in the
outlook. Last week's collections line missed by 14k because one customer paid early; logged with its cause."
