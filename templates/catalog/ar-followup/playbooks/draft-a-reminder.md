# Draft a reminder

Triggered by a task naming one invoice or customer, and used for each invoice in `playbooks/weekly-receivables-reminders.md`. Budget 10
minutes per invoice. The outcome is one draft on the task, to send with Tools when a person has turned mail sending on in Tico and the task asks
for it. You never send it yourself.

The cadence and tone follow the collections practice in `knowledge/collections-practice.md`: polite first, firmer later, short, one clear action,
and a human takes over at the end.

---

## 1. Run the checks

Run `skills/check-before-you-chase/SKILL.md`: paid, disputed or promised, do-not-chase, recent thread, personal-touch amount. Any hit means no
draft, and the reason goes in the pack. Confirm the terms in `knowledge/customers.md`. If none are recorded, the draft leaves a marked gap.

## 2. Find the step

Days past due, against the ladder in `knowledge/ladder.md`. The default, until the team sets its own:
- **Before due (3 to 7 days)**: optional, a courteous heads-up with the invoice and the payment route.
- **1 to 7 days late**: friendly, assumes an oversight, asks whether something is holding it up.
- **15 days**: direct, restates the invoice and the due date, asks for a payment date.
- **30 days**: firm and calm, cites the earlier reminders by date, asks for payment or a call.
- **45 days and beyond**: no email from you. A human calls or writes; you supply the history.

## 3. Write it

Run `skills/write-a-payment-reminder/SKILL.md`. The draft is plain text, 50 to 125 words, in the voice in `knowledge/voice.md`. It has the
invoice number, the amount and the due date from the row, how to pay as the invoice says, one ask, and a line inviting a reply if it crossed with
a payment. If a term or a payment-plan answer is needed, write "[for you to decide]" and ask on the task.

## 4. Finish

Attach the draft with recipient, subject, body and its source row. Send that exact text with your Tools only when a person has turned mail sending
on in Tico and the task asks for the send. Otherwise keep the draft. Then `hub task update <id> --status done --note`: the invoice, its step, and
what needs a human.
