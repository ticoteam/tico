# Weekday outbound run

Schedule: weekdays at 08:00 team time (routine `outbound-run`), after setup. Also run by hand. Budget 45 minutes.
The outcome is one run note: every reply since the last run read and sorted, stops applied, the day's touches and first
lines ready for review, the day's new contacts checked, and the meetings handed to the Account Executive. Nothing is sent
unless a task asks for it.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/outbound-method.md`, `knowledge/do-not-contact.md`, `knowledge/sequences.md` (who is at which step), and
yesterday's run note.

## 2. Replies first

Read every reply since the last run, from the sequence mailbox (or the CRM, read only) and from the task. Sort each one:

- **Stop or unsubscribe**: add the person to `knowledge/do-not-contact.md` now, in this run. Nothing else is sent to them,
  in any sequence.
- **Bounce**: a hard bounce removes the address. A soft bounce gets one retry two business days later, then removal.
- **Out of office**: pause until the return date they give.
- **Not now**: pause until the date they name, or 90 days if they name none. Record the pause.
- **Wrong person**: reroute the account to the right role with `skills/build-a-target-account-list/SKILL.md`. Do not write
  to the wrong person again.
- **Interested, or asking a question**: a meeting if they asked for a time; otherwise a draft answer on the task. A question
  about price or terms goes to a person.

A positive reply is handed over in step 6 the same run. The Account Executive should hear the same business day.

## 3. Apply the stop rules

Read the stop rules in `knowledge/outbound-method.md`. For each sequence, check whether a stop trigger has fired since the last
run: a reply of any kind, a customer or open-deal match, a complaint. A contact with a trigger leaves the sequence in this run,
and `knowledge/sequences.md` records why.

## 4. Draft the due touches

For each contact whose next step is due today, follow `skills/write-a-personal-first-line/SKILL.md`, add this touch's new reason
(a fact, an answer or a smaller ask), and keep the touch under 100 words. Check it against `knowledge/voice.md`, and run
`hub brand check -` on it. Put on the task: the recipient, the subject, the body, the source and date of the first line, and the
step number. Respect the day's cap in `knowledge/outbound-method.md`.

## 5. Check and record today's new contacts

Take up to ten accounts from `knowledge/target-accounts.md` that already passed their checks, and only those. Before a contact is
recorded, its suppression, customer and open-deal checks are complete and dated. Record it in `knowledge/sequences.md` as "drafted"
with its first touch. Enrolling it in a live sequence is a send act: it waits for a task that asks for it and a granted Tool.

## 6. Hand over meetings

For each meeting booked: write a short note on the account (who, the role, the first line that worked, what they asked, the open
questions), then `hub task create --owner sales --title "<account> meeting booked" --body-file <note>`. The calendar invitation is a
person's act.

## 7. Finish

Write the run note on the routine's task, commit, and run `hub task update <id> --status done --note`. A quiet day finishes with the
count of contacts still waiting and the oldest reply waiting on a person.
