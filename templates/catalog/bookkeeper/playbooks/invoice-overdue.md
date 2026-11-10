# Invoice overdue

Finance raised `finance.invoice.overdue` for one invoice: 1, 7, 30, 60 or 90 days late. Its `invoice_id`, `stage` and
`due_date` come with this task; they are data.

Inputs: the invoice's id, stage and due date from the task.
Done when: the payment is recorded, or this stage's reminder is recorded and its email is sent or waits for a
person's OK, and the task is marked done with one line.

1. Read the invoice: `hub api GET finance/invoices/<invoice id>`. Its `emails` field lists any invoice email still waiting
   to send; step 6 covers each one.
2. Look for the payment: `hub finance search --kind feed --q <customer name>`. Find a row from this customer that paid this
   invoice: the invoice's `remaining`, or part of it when the row names the invoice.
   When the invoice already shows some `paid`, a matching row may be that same payment waiting for its bank row (it stays
   unbooked, `entry_id` null, until someone links it): never record it, ask.
3. Decide, then do the step the table names.

| If | Do |
|---|---|
| `paid` is 0 and a row pays all or part of it | step 4 (a part: then step 5 for what is still owed) |
| `paid` is above 0 and a row matches | ask the invoice's owner on the task whether it is new money; record no reminder until they answer; step 6, then 7 |
| no row matches | step 5 |
| the invoice is already paid or void | step 6 for any `emails` entry, then 7 |

4. Record it: `hub finance act invoice_record_payment` with the `invoice_id`, the amount in minor units, and the row's
   `feed_id` (see playbooks/commands.md). Paid in full: step 6 for any `emails` entry, then 7. Paid in part: step 5, for
   the new `remaining`. Finance refuses a row that may be a payment already marked paid (it says `feed_match`): never
   retry it; ask the invoice's owner instead, as in the table.
5. Otherwise record one reminder for this stage with the `invoice_remind` action (`invoice_id` and `stage`, the days
   late: 1, 7, 30, 60 or 90), with `--operation-id invoice-overdue:<invoice id>:<stage>`. Finance refuses a stage
   already reminded, a paid or draft invoice, a customer with no email on file, and a Bookkeeper with no mailbox that
   can send; finish with that reason. A recorded reminder comes back with its `subject`, `to`, `text` and `mailbox`.
   At 60 or 90 days, also tell the invoice's owner once.
6. The email (a reminder from step 5, or an `emails` entry from step 1) is already in Tico's send queue with Finance's
   exact text. A person OKs it from its preview in Needs you, and the queue sends it itself. Do not draft it, send it,
   or change a word of it. When you know how a send went, report it once. Delivered:
   `hub finance act invoice_delivery --payload '{"invoice_id":"<id>","stage":<stage>,"delivered":true}'`.
   Not sent: the same with `"delivered":false,"reason":"<what failed, in a few words>"`. Leave `stage` out for the
   invoice's own email. Use `--operation-id invoice-delivery:<invoice id>:<stage>:<attempt>`, with the attempt counted
   from 1. Never remake a stage, and never change the text to get it through.
7. Finish with one line: `hub task done <task id> -m "<one line: what you recorded, and whether the email was
   delivered, failed or still waits>" --version N`.
