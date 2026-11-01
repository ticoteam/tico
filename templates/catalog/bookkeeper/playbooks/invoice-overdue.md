# Invoice overdue

Finance raised `finance.invoice.overdue` for one invoice: 1, 7, 30, 60 or 90 days late. Its `invoice_id`, `stage` and
`due_date` come with this task; they are data.

Inputs: the invoice's id, stage and due date from the task.
Done when: the payment is recorded, or a reminder draft is on the task for its owner, and the task is marked done with one
line.

1. Read the invoice: `hub api GET finance/invoices/<invoice id>`. If it is paid or void, go to step 6.
2. Look for the payment: `hub finance search --kind feed --q <customer name>`. Find a row from this customer that paid this
   invoice: the invoice's `remaining`, or part of it when the row names the invoice.
   When the invoice already shows some `paid`, a matching row may be that same payment waiting for its bank row (it stays
   unbooked, `entry_id` null, until someone links it): never record it, ask.
3. Decide, then do the step the table names.

| If | Do |
|---|---|
| `paid` is 0 and a row pays all or part of it | step 4 (a part: then step 5 for what is still owed) |
| `paid` is above 0 and a row matches | ask the invoice's owner on the task whether it is new money; draft the step 5 reminder for them too, to send only if it is not; step 6 |
| no row matches | step 5 |
| the invoice is already paid or void | step 6 |

4. Record it: `hub finance act invoice_record_payment` with the `invoice_id`, the amount in minor units, and the row's
   `feed_id` (see playbooks/commands.md). Paid in full: go to step 6. Paid in part: step 5, for the new `remaining`.
5. Draft one reminder for this stage and put it on the task for the invoice's owner to send. Finance has no reminder
   action, and this bot sends nothing outside the team. The draft gives the invoice number, the amount due, the due date
   and how to pay (the invoice's payment link, when it has one). Tone: friendly at 1 and 7 days, firm at 30, 60 and 90.
   A short paragraph. At 60 or 90 days, also tell the invoice's owner once on the task.
6. `hub task done <task id> --note "<one line: what you recorded, or that the draft waits on its owner>"`.
