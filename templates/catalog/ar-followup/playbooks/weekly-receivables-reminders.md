# Weekly receivables reminders

Schedule: Mondays at 09:00 team time (routine `weekly-receivables-reminders`), after setup. Also run by hand on request. Budget 35 minutes. The outcome is one pack for the
sender: the aging summary, a draft reminder per invoice at its step, and what was held back. Nothing is
sent and no record changes.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/ladder.md`, `knowledge/customers.md`, `knowledge/voice.md` and last week's pack. Find the
newest aging export and its as-of date. If it is older than 3 days, say so in the first line.

## 2. Summarise the aging

Buckets: not yet due, 1 to 30, 31 to 60, 61 and over. For each: count, amount, and the change on last
week's pack. Name the three largest overdue invoices. Amounts are copied from rows.

## 3. Choose the invoices, with the checks first

For each overdue invoice, and each due within the "before due" window in the ladder, check in this order:
1. **Paid in this export?** Then skip and note it.
2. **Disputed or promised?** Read `knowledge/customers.md`; skip and note it, with the date.
3. **On the do-not-chase list?** Skip and name it as skipped.
4. **Recent thread?** Where the sender's mailbox is connected, `mail.sh search "<customer email>"` and read
   the last exchange. A reply that a human has not handled goes to "Needs you now".
5. **Above the personal-touch amount, or past the last step?** Put it under "Needs you now" with no draft.

## 4. Draft

Follow `playbooks/draft-a-reminder.md` for each remaining invoice at its step, at most fifteen a week; the
rest are listed with their step for next week. Each draft carries the recipient, subject, body and the
export row it rests on.

## 5. Write the pack and hand it over

Write `reports/YYYY-MM-DD-ar-followup.md` in the shape of `knowledge/examples/ar-pack.md`: headline,
aging, needs you now, drafts, held back, could not read, sources. Then:

    hub file publish reports/YYYY-MM-DD-ar-followup.md

Attach the drafts to the task. Send requested reminders with their exact text and recipients when a person has turned mail sending on in Tico; otherwise keep drafts.

## 6. Finish

Update `knowledge/customers.md` with what you learned. Commit, then `hub task update <id> --status done
--note`: overdue amount, drafts ready, who needs a human, and which sources you could not read. A mailbox
or export that failed is named; an unread source is not an empty one. Always finish the task: an open
routine task absorbs the next.
