# The figure check

Every number in an update passes this check before it is written into the draft. The working table is the **figure
sheet**. It is published under the draft and filed with the sent copy, so anyone can trace a number later.

## The figure sheet

One row per figure:

| Column | What goes in it |
|--------|-----------------|
| Label | The name exactly as it appears in the update |
| Value and unit | With currency. Rounded only as the update rounds it |
| Period | The month or quarter, or "as of <date>" for a balance |
| Source | The exact command, report or Tool view |
| Date read | Today |
| Second source | A different record that should agree |
| Tie | "Ties", or the difference and why |
| Definition | The one-line definition, and "same as last period" or "changed" |
| Last period | The value sent last time |
| Change | Amount and percent |
| Reason | For any move over 20%, the cause found in the records |
| Status | Supplied / preliminary / restated / not supplied |

## The five checks

1. **Read it from the source yourself, this run.** Not from a message, a deck, last month's update or another
   teammate's summary.
2. **Tie it to a second source.**

   | Figure | Source | Tie to |
   |--------|--------|--------|
   | Cash | `tico finance metric cash` | Bank and money account balances on the balance sheet at the same date |
   | Net burn | Change in cash over the period, with financing taken out, from `tico finance report cash_flow` | Opening cash minus closing cash, plus money raised or borrowed in the period |
   | Runway | `tico finance metric runway_months` | Cash divided by the burn the metric says it uses. State which burn: last month, or a three-month average |
   | Revenue | `tico finance report pnl` | The billing Tool's invoiced or recognised total for the period |
   | Recurring revenue | The billing Tool or the KPI | Count of paying customers times average price, within reason; and last period plus new, minus lost |
   | Customers | `tico growth numbers` or the KPI | A count from the billing Tool |
   | A KPI | `tico kpi show <kpi>` | The evidence link on the reading |

   A tie does not have to be to the cent for operating numbers. Cash does. Write the difference either way.
3. **Same definition as last period.** Read the definition on last period's figure sheet. If the way of counting
   changed, compute this period both ways, show both once, and say which is used from now on. Defining a new metric is
   the catalog skill `define-a-metric`.
4. **Explain big moves.** For any change over 20% up or down, find the cause in the records: the customer, the
   invoice, the campaign, the one-off. "Seasonality" needs last year's number beside it. If no cause is found, say "we
   have not yet found why".
5. **Check the period and the label.** Closed month, or labelled preliminary. A balance has an "as of" date. A flow
   has a period. Currency stated once.

## Statuses

- **Supplied.** Passed all five.
- **Preliminary.** The books for the period are not closed. Say so beside the figure.
- **Restated.** A figure already sent has changed. Show the old value, the new value and why, in the update, once.
- **Not supplied.** Failed a check, or could not be read. The cell says "not supplied" and a line under the table says
  why. File a task to the teammate who can fix it. Never fill the cell with an estimate.

## Definitions to state the first time and keep

Write each into `knowledge/investor-updates.md` once, and never change one without showing the change.

- **Cash.** Money in bank and money accounts on the last day. Say whether it includes restricted cash or undrawn
  credit (default: no).
- **Net burn.** The fall in cash over the month, leaving out money raised or borrowed and loan repayments.
- **Runway.** Cash divided by net burn. Say which burn. Committed money not yet received is left out.
- **Revenue.** As the books recognise it, on the basis the books use (cash or accrual). Say which.
- **Monthly recurring revenue.** Subscription revenue normalised to one month, leaving out one-off fees, services and
  usage that is not contracted. Annual plans are divided by twelve.
- **Customers.** Paying, active on the last day. Say whether trials and free accounts are left out (default: yes).
- **Growth.** This period against last, as a percent, and against the same period last year once there is one.

## Things that look like figures and are not

- A forecast. Label it "plan" or "forecast" and give the main assumption.
- A pipeline total. It is not revenue. Say "in discussion" and never add it to a revenue figure.
- A commitment to invest that is not signed. It is not cash.
- A number from a customer or partner that the company cannot check. Attribute it: "as reported by".
