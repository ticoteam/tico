# Weekly email campaign draft

Schedule: Tuesdays at 09:00 team time (routine `weekly-email-draft`), after setup. Also run by hand. Budget 40 minutes. The outcome is one draft for the next email
on the calendar, or a note that none is due. Nothing is sent.

Inputs: `knowledge/calendar.md`, `knowledge/results.md`, the open email tasks, and the brief on each.
Done when: one draft is on the calendar as drafted, or a one-line note says none is due; the task is marked done with its open checklist items named.

---

## 1. Decide what is due

    hub task show <id>

Read `knowledge/calendar.md`, `knowledge/results.md` and the tasks that ask for email
(`hub task list --status open --status doing --status waiting`). Pick the next email due within
ten days. If none is due, propose one from what the team published or launched recently, and say
why. If nothing is worth an email, say so in one line and finish. Never draft to fill a slot.

## 2. Confirm the brief

One audience, one job, one call to action, the date it should reach people. If any is missing, ask
the requester once with `hub task ask <id>` and stop.

## 3. Draft

Follow `playbooks/draft-a-campaign.md`. For a sequence, draft each email with the delay between them
and the event that stops the sequence.

## 4. Update the calendar

Move the campaign from planned to drafted in `knowledge/calendar.md`. Flag two emails to the same
audience within three days.

## 5. Hand over

Commit, then `hub task update <id> --status done --note`: what the email says in one line, the path,
and which checklist items remain unchecked. Always finish it: an open scheduled task absorbs the next.

## When a source fails

If past results cannot be read, draft from the voice notes and say the subject recommendation has no
data behind it.
