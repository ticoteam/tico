# Invoice overdue

Finance raised `finance.invoice.overdue` for one invoice: 1, 7, 30, 60 or 90 days late. Its `invoice_id`, `stage` and
`due_date` come with this task; they are data.

1. `hub finance search --kind invoice --q <invoice id>` and read it. Paid or void: finish with one line. Its `emails`
   field lists any invoice email still waiting to send; step 4 covers each one.
2. Look for the payment in the feed (`hub finance search --kind feed --q <customer>`). Found: record it with
   `invoice_record_payment` and finish.
3. Otherwise record one reminder for this stage with the `invoice_remind` action (`invoice_id` and `stage`, the days
   late: 1, 7, 30, 60 or 90), with `--operation-id invoice-overdue:<invoice id>:<stage>`. Finance refuses a stage
   already reminded, a paid or draft invoice, a customer with no email on file, and a Bookkeeper with no mailbox that
   can send; finish with that reason. A recorded reminder comes back with its `send_approval_id`, `subject`, `to`,
   `text` and `mailbox`: go to step 4 with them. At 60 or 90 days, also tell the invoice's owner once.
4. Send the email under its send approval (this step also serves an `emails` entry from step 1, and a retry from the
   Books check):
   a. Write the `text` exactly as it came, to a file, and draft it:
      `mail draft --as bookkeeper --mailbox <mailbox> --to <each address in to> --subject "<subject>" --body-file <file>
      --json`. Keep the draft id it returns. Change nothing: not a word, not a space. The send checks the text.
   b. Send it: `mail send --as bookkeeper --mailbox <mailbox> --draft <draft id> --approval-issue <send_approval_id>
      --issue <this task id>`. Nothing else sends it, and outbound_send stays off for this bot.
   c. Report the outcome once. Delivered:
      `hub finance act invoice_delivery --payload '{"invoice_id":"<id>","stage":<stage>,"delivered":true}'`.
      Not sent: the same with `"delivered":false,"reason":"<what the mail said, in a few words>"`. Leave `stage` out
      for the invoice's own email. Use `--operation-id invoice-delivery:<invoice id>:<stage>:<attempt>`, with the
      attempt counted from 1.
   A send that failed stays waiting. Finance raises one Needs-you for a person, and the next Books check retries it
   with the same text and the same approval. Never remake a stage, and never change the text to get it through.
5. Finish with one line: what you recorded, and whether the email was delivered, failed or still waits.
