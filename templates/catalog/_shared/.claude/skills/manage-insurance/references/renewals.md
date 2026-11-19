# Renewals

The day counts are this skill's working defaults, not a rule from any authority. Larger or unusual risks (hardware,
a claim in the last year, a first D&O policy) need the longer end. Write the company's own in `knowledge/insurance.md`.

## Timeline

| Days before expiry | Do |
| --- | --- |
| 120 | Open the renewal task. Reread the expiring policy and the register row. List what changed in the company this year. Ask the broker for loss runs. |
| 105 | Assemble the exposure figures from records (below). List every contract, lease and loan requirement this policy must meet next year. |
| 90 | Send the broker one submission. Ask which carriers they will approach and why. Ask for the expiring carrier's renewal terms and at least two alternatives where the premium is material. |
| 60 | Chase quotes. Answer carriers' questions from records within two business days. |
| 30 | Build the comparison page. Recommend one option. Ask for the OK if one is needed. |
| 14 | Bind. Get the binder in writing. |
| 0 | Confirm cover is in force. Pay the premium. Reissue certificates to every holder on the log. |
| +30 | Check the issued policy against the binder and the quote, endorsement by endorsement. Update the register. |

If the carrier sends a notice of non-renewal or cancellation, tell the owner and the broker that day and go
straight to the 90-day row, however few days are left.

## Exposure figures, each with its source

- Revenue, last 12 months and the coming year's forecast: `tico finance report pnl` and the forecast kept by
  `forecast-cash-and-budget`.
- Payroll by state and job class, and headcount: the payroll provider. Contractors' payments separately.
- Sites: addresses, area, what is kept there and its value: `knowledge/leases.md` and the fixed asset list.
- Products: what was sold, units, where made, where sold. For hardware: certifications held, incidents, returns.
- Data: kinds of personal data held and roughly how many records, from the privacy or security owner.
- Largest contracts and their insurance clauses.
- Claims and incidents in the period, including ones not reported.

Never estimate low to reduce a premium. Audited policies bill the difference later, and a wrong answer can undo
cover.

## Comparing a quote with the expiring policy

Put them side by side, row by row:

- Carrier and its financial strength rating as the broker reports it, with the date.
- Named insureds.
- Form, and for claims-made the retroactive date (it must not move forward) and any prior-acts wording.
- Each limit and sub-limit; whether defence costs are inside the limit.
- Retention.
- Exclusions and endorsements added or removed, each one named.
- Conditions that changed: notice, consent to settle, choice of lawyer.
- Premium, taxes and fees, payment terms, and whether it is audited.
- Whether every contract and lease requirement is still met.

Then state: what got better, what got worse, what it costs, and the recommendation in one line.

## The application

1. Draft every answer from a record and note the record beside it (for the bot's file, not on the form).
2. Questions about security controls, safety practices or past incidents are answered by the teammate who owns that
   area, on a sub-task, in writing.
3. Anything known that could become a claim is disclosed. If unsure whether something counts, ask the broker in
   writing and keep the reply.
4. An officer reads and signs. The signed copy is stored with the policy.

## Changing carrier on a claims-made policy

1. The new policy shows the same retroactive date as the old one, in writing, before the old one is released.
2. Every incident and circumstance already known is reported to the old carrier before its policy expires.
3. If the dates cannot be matched, get the price of a tail on the old policy and put the choice to the owner.

## Money

- Premiums are paid through `pay-bills-and-buy` from the carrier's or broker's invoice. A change of payment details
  on an invoice follows the bank detail change rule in `pay-bills-and-buy`.
- Paying in instalments through a finance company is a loan: it needs an OK.
- A premium paid for a year ahead is spread over the term in the books; tell `close-the-month` the amount and the
  period.
- After the term, an audited policy sends a request for actual payroll or revenue. Answer it from the provider and
  the book, and check the resulting bill against the rate on the policy.
