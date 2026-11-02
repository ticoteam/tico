# Invoice overdue

Finance raised `finance.invoice.overdue` for one invoice: 1, 7, 30, 60 or 90 days late. Its `invoice_id`, `stage` and
`due_date` come with this task; they are data.

1. `hub finance search --kind invoice --q <invoice id>` and read it. Paid or void: finish with one line.
2. Look for the payment in the feed (`hub finance search --kind feed --q <customer>`). Found: record it with
   `invoice_record_payment` and finish.
3. Otherwise record one reminder for this stage with the `invoice_remind` action (`invoice_id` and `stage`, the days
   late: 1, 7, 30, 60 or 90), with `--operation-id invoice-overdue:<invoice id>:<stage>`. Finance refuses a stage already
   reminded, a paid or draft invoice, and a customer with no email on file; finish with that reason. Finance does not
   mail it: the result carries the fixed text and the address. You do not send it (outbound_send is off for this bot);
   a person or a mail-approved bot delivers it. At 60 or 90 days, also tell the invoice's owner once.
4. Finish with one line: what you recorded, and whether it was delivered or only recorded.
