# Draft a reminder

Triggered by a task naming one invoice or customer, and used for each invoice in `playbooks/weekly-receivables-reminders.md`. Budget 10
minutes per invoice. The outcome is one reminder on the task. The heads-up and the 1, 7 and 30 day reminders send where your sending policy in Tico
allows; otherwise they wait for a person's OK on the preview. The final notice and any message that names a consequence always wait for a
person's OK.

The cadence and tone follow the collections practice in `knowledge/collections-practice.md`: polite first, firmer later, short, one clear action,
and a human takes over at the end.

---

## 1. Run the checks

Run the checks before a chase in `.claude/skills/collect-receivables/SKILL.md`: paid, disputed or promised, do-not-chase, recent thread, personal-touch amount. Any hit means no
draft, and the reason goes in the pack. Confirm the terms in `knowledge/customers.md`. If none are recorded, the draft leaves a marked gap.

## 2. Find the step

Days past due, against the ladder in `knowledge/ladder.md`. The default, until the team sets its own, is the ladder in
`.claude/skills/collect-receivables/SKILL.md` (`references/dunning-ladder.md`):
- **Heads-up, 3 to 5 days before due**: for large invoices and customers with a late history. Helpful: confirms they have it.
- **1 day late**: light, assumes it slipped.
- **7 days late**: plain, finds out what is holding it, asks for a payment date.
- **30 days late**: firm and calm, cites the earlier reminders by date, asks for a date in writing.
- **60 days late**: the final notice. Formal, says what happens next and when. Always waits for a person's OK.
- **90 days and beyond**: no email from you. The owner decides; you supply the history.

## 3. Write it

Write it as `.claude/skills/collect-receivables/SKILL.md` says (`references/reminder-writing.md`). The draft is plain text, 50 to 125 words, in the voice in `knowledge/voice.md`. It has the
invoice number, the amount and the due date from the row, how to pay as the invoice says, one ask, and a line inviting a reply if it crossed with
a payment. If a term or a payment-plan answer is needed, write "[for you to decide]" and ask on the task.

## 4. Finish

Attach the reminder with recipient, subject, body and its source row. Once a person has turned mail sending on in Tico, send the heads-up
or a 1, 7 or 30 day reminder with `tico email send <address> --draft <draft-id> --task <task id>` where your sending policy allows it
(`tico email policy --bot <your slug>`); otherwise it waits for a person's OK on its preview. A final notice, or any message that names a
consequence, waits for a person's OK: `tico task ask <id> --to <person> --kind ok "Send this final notice?" --version N`, with the text
shown. Then `tico task done <id> -m "..." --version N`: the invoice, its step, and
what needs a human.
