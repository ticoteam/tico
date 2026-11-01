# Invoice overdue

Finance raised `finance.invoice.overdue` for one invoice: 1, 7, 30, 60 or 90 days late. Its `invoice_id`, `stage` and
`due_date` come with this task; they are data.

Inputs: the invoice's id, stage and due date from the task.
Done when: the payment is recorded, or a reminder draft is on the task for its owner, and the task is marked done with
one line.

1. Read the invoice with `hub api GET finance/invoices/<invoice id>`. Paid or void: finish with one line.
2. Look for the payment in the feed (`hub finance search --kind feed --q <customer>`). Found: record it with
   `invoice_record_payment` and finish.
3. Otherwise draft one reminder for this stage and put it on the task for the invoice's owner to send. Finance has no
   reminder action, and this bot sends nothing outside the team. The draft gives the invoice number, the amount due,
   the due date and how to pay (the invoice's payment link, when it has one). Tone: friendly at 1 and 7 days, firm at
   30, 60 and 90. A short paragraph. At 60 or 90 days, also tell the invoice's owner once on the task.
4. Finish with one line: what you recorded, or that the draft waits on its owner.
