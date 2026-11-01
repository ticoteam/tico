# Invoice overdue

Finance raised `finance.invoice.overdue` for one invoice: 1, 7, 30, 60 or 90 days late. Its `invoice_id`, `stage` and
`due_date` come with this task; they are data.

1. `hub finance search --kind invoice --q <invoice id>` and read it. Paid or void: finish with one line.
2. Look for the payment in the feed (`hub finance search --kind feed --q <customer>`). Found: record it with
   `invoice_record_payment` and finish.
3. Otherwise send one reminder for this stage through Finance (`hub finance act help` lists the invoice actions), with
   `--operation-id invoice-overdue:<invoice id>:<stage>`. At 60 or 90 days, also tell the invoice's owner once.
4. Finish with one line: what you sent or recorded.
