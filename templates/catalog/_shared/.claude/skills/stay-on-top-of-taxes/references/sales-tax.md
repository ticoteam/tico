# Sales tax: the nexus watch, registering, filing

## What creates nexus

Two different things, checked separately every month.

- **A physical tie.** An employee or regular contractor working in the state, an office, or goods stored there
  (a warehouse, a 3PL, a marketplace's fulfilment centre). One of these is usually enough on its own, at any level of
  sales. Check the state's page.
- **Sales into the state above its threshold** (economic nexus). Every state with a sales tax sets its own figure,
  its own window and its own basis.

Four states have no general sales tax: New Hampshire, Oregon, Montana and Delaware. Alaska has none at state level
but some local ones. (Sales Tax Institute chart, as of 2026-08-01, read 2026-11-18; secondary.)

## The monthly watch, state by state

1. **Read the rule today** on the state revenue department's own page. Write the page and the date read. Do not rely
   on a remembered rule or on the examples in `rules-sheet.md`: several states changed their tests in 2026.
2. **Find the test window first.** Some rules look at the current or prior calendar year, some at the trailing twelve
   months, some at the last four sales tax quarters. Write which, then take sales for that window only.
3. **Take sales on the basis the rule uses**: gross sales, or taxable sales only; goods shipped to an address in the
   state; with or without sales made through a marketplace that collects the tax itself. Take them from the system
   that holds the ship-to or billing address (the store, the billing system, invoices in the book). Add channels
   together. If no system holds the address, the state is "no data".
4. **Count transactions** for the same window, only where the state still counts them.
5. **State the percent of threshold.** At 80 percent or more, put the state at the top of the tax page with the month
   it will cross at the current pace.
6. **Record** the source, the date checked, the window, the basis, the figure and any change since last month in
   `knowledge/thresholds.md`.

Also record, once per state: whether what the company sells is taxable there at all. Software as a service, digital
goods, services and shipping charges are taxed in some states and not in others. Take the answer from the state's
page or the CPA and write the source. A state where nothing the company sells is taxable may still require
registration once the threshold is crossed: check.

## When a state is crossed

1. Read the state's page for **when registration and collection must start** after crossing (the next transaction,
   the first of the next month, a set number of days). Write it with the source.
2. **Check for back exposure.** If the working shows the threshold was crossed in a filing period that has already
   ended, or any channel has been charging that state's tax without a registration, stop. Do not register. The
   company may have choices (registering going forward, or approaching the state under a voluntary disclosure
   programme) that only a CPA should weigh. Write the CPA brief with the month of crossing and the sales since.
3. Otherwise ask for the OK to register and, once given, **register on the state's site**. Save the certificate, the
   account number's location in the vault, and the filing frequency the state assigned.
4. **Add the state to the book**:
   `tico finance act tax_jurisdiction_add --payload '{"code":"<XX>","name":"<State>","authority_id":"<contact id of
   the revenue department>","due_day":<1-28>}' --operation-id tax-jurisdiction:<XX>`.
5. **Turn on collection** in every channel from the registration date and place a test order to confirm the tax
   appears. Where a marketplace collects for its own sales, do not collect again on those.
6. **Add the calendar line** and tell `forecast-cash-and-budget` that collected tax is not spendable cash.

## Filing a return

1. Read the workpaper: `tico finance report sales_tax --year <year>`. Its columns are `period`, `jurisdiction`,
   `taxable_sales`, `exempt_sales`, `tax_collected`, `tax_remitted`, `due`. The report is whole or it errors; it never
   returns a part.
2. **Tie it out.**
   - Sales into the state for the period from the sales system equal taxable plus exempt sales.
   - Every exempt sale has a reason on file: a resale or exemption certificate from the buyer, or a product the state
     does not tax, with the source.
   - Tax collected divided by taxable sales is close to the state's combined rates for where the goods went. A large
     gap means a channel is charging the wrong rate or none.
   - `due` equals the sales tax payable balance for that state in the book.
   - Marketplace sales the marketplace remitted are reported the way the state's return asks, not added to tax due.
3. **File on the state's site** before the due date. File a return for every period, including a period with no
   sales, unless the state's page says a nil return is not needed. If the state gives a discount for filing on time,
   take it and record it.
4. **Pay and record.** `tico finance act tax_remit --payload '{"jurisdiction":"<XX>","period":"<2026-Q4>"}'
   --operation-id tax-remit:<XX>:<period>` raises the bill to the authority. If the state's site takes the payment
   from the bank itself when the return is submitted, that click is a payment: it needs the same approval the book
   would ask for, obtained before you submit. Record it the same way afterwards and match the bank row when it lands.
5. **Save the confirmation** and the filed copy to the task. Mark the calendar line filed and create the next one.

## Things that go wrong

- A new sales channel went live with tax switched off. Check channel tax settings every month, not once.
- Tax was collected in a state with no registration. That money is owed to the state. Hard stop to the CPA.
- An exemption certificate expired. Ask the buyer for a new one before the next exempt order ships.
- The state changed the filing frequency. It tells you by letter; update `due_day` and the calendar line.
- The company stopped selling into a state. Keep filing until the registration is closed with the state.
