# Draft a reminder

Triggered by a task naming one invoice or customer, and used for each invoice in
`playbooks/weekly-receivables-reminders.md`. Budget 10 minutes per invoice. The outcome is one draft on the
task to send with Tools when a person has turned mail sending on in Tico. You never send it.

The cadence and tone follow public collections guidance (see the sources in docs/starter-bots.md): polite
first, firmer later, short, one clear action, and a human takes over at the end.

---

## 1. Run the checks

Paid, disputed, promised, do-not-chase, recent thread, amount above the personal-touch line. Any hit
means no draft, and the reason goes in the pack. Confirm the terms in `knowledge/customers.md`; if
none are recorded, the draft leaves a marked gap.

## 2. Find the step

Days past due, against the ladder in `knowledge/ladder.md`. The default, until the team sets its own:
- **Before due (3 to 7 days)**: optional, a courteous heads-up with the invoice and the payment route.
- **1 to 7 days late**: friendly, assumes an oversight, asks them to tell you if something is holding it up.
- **15 days**: direct, restates the invoice and the due date, asks for a payment date.
- **30 days**: firm and calm, references the earlier reminders by date, asks for payment or a call.
- **45 days and beyond**: no email from you. A human calls or writes; you supply the history.

## 3. Write it

Plain text, 50 to 125 words, in `knowledge/voice.md`. It has: the invoice number, the amount and due date
from the row, how to pay as the invoice says, one ask, and a line inviting them to reply if it crossed with
a payment. It never contains a late fee, interest, suspension, collections or legal wording unless the
human gave that exact term; it never shames, guesses at their finances or copies a private detail. If a
term or a payment-plan answer is needed, write "[for you to decide]" and ask on the task.

## 4. Finish

Attach the draft with recipient, subject, body and its source row. Send that exact text to the recipient with your Tools when a person has turned mail sending on in Tico; otherwise keep the draft. Then `hub task update <id> --status done --note`: the
invoice, its step, and what needs a human. Never send it yourself.
