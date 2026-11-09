# Monthly legal spend summary

Schedule: the 5th of each month at 09:00 team time (routine `monthly-legal-spend`), after setup. Also run by hand. Budget 30 minutes. The outcome is one page: where last month's legal money
went, against budget, and what to ask about. A summary for a human, not legal advice. Nothing is paid or sent.

---

## 1. Gather the month

    hub task list --status open --status done

Every invoice received last month and its review in `reports/invoices/`. An invoice with no review yet: follow
`playbooks/review-a-counsel-invoice.md` first. Read `knowledge/matters.md` and last month's summary.

## 2. Add it up

By matter and by firm: billed last month, billed to date, budget, and the share of budget used. By timekeeper role
(partner, associate, paralegal): hours and amount, so a matter staffed mostly by partners shows. Keep amounts
exactly as invoiced; the amount in question is a separate column, never deducted.

## 3. Matters

Opened and closed last month. Any open matter with no update in 30 days: name its lead. Any matter over 80 percent
of budget with no new estimate: list it. A firm billing a matter not on the list: list it.

## 4. Write and hand over

`reports/YYYY-MM-DD-legal-spend.md` in the shape of `knowledge/examples/legal-spend.md`: the headline (total,
change on the month before, amount in question), the table, the matters, the questions to raise with each firm as
drafts, then the not-legal-advice line. `hub file publish` it, commit, and `hub task update <id> --status done
--note` with the total and the amount in question first.

## When a source fails

An invoice you could not open is listed with its amount from the task and "not reviewed". A matter with no budget is
"no budget", never a guessed one.

## Done when
- Every invoice received last month has a review, or is listed as "not reviewed" with its amount from the task.
- The summary has the headline, the table by matter and firm, the matters list, and the questions to raise as drafts.
- The summary is published with `hub file publish`, and the task is closed with the total and the amount in question first.
