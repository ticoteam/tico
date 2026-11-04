# Weekly receivables reminders

Schedule: Mondays at 09:00 team time (routine `weekly-receivables-reminders`), after setup. Also run by hand on request. Budget 35 minutes. The
outcome is one pack for the sender: the aging summary, a draft reminder for each invoice at its step, and what was held back. Nothing is sent and
no record changes.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/ladder.md`, `knowledge/customers.md`, `knowledge/voice.md` and last week's pack. Find the newest aging export and its as-of date. If it
is older than 3 days, say so in the first line.

## 2. Read and tie out the aging

Run `skills/read-an-aging-report/SKILL.md`: tie the total, net unapplied cash and credit notes, find duplicates, bucket the rest, and compute DSO with
its formula.

## 3. Choose the invoices, with the checks first

For each overdue invoice, and each due within the "before due" window in the ladder, run `skills/check-before-you-chase/SKILL.md` in its order. An
invoice that passes goes to the draft step. Work the largest overdue balances first when the list is longer than fifteen.

## 4. Draft

Follow `playbooks/draft-a-reminder.md` for each invoice that passed, at most fifteen a week. The rest are listed with their step for next week. Each
draft carries the recipient, subject, body and the export row it rests on.

## 5. Write the pack and hand it over

Write `reports/YYYY-MM-DD-ar-followup.md` in the shape of `knowledge/examples/ar-pack.md`: headline, aging with DSO, needs you now, drafts, held back,
could not read, sources. Publish it with `hub file publish reports/YYYY-MM-DD-ar-followup.md`. Attach the drafts to the task. Send a reminder only when
the task asks for it, the text is exactly the approved draft, and mail sending is on in Tico. Otherwise the drafts stay on the task.

## 6. Finish

Update `knowledge/customers.md` with what you learned. Commit, then `hub task update <id> --status done --note`: overdue amount, drafts ready, who
needs a human, and which sources you could not read. A mailbox or export that failed is named; an unread source is not an empty one. Always finish the
task: an open routine task absorbs the next.
