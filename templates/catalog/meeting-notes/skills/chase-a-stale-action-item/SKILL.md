---
name: chase-a-stale-action-item
description: Handle a late action item or an ask that has gone quiet, with one re-check on its task, at most one chase per owner a week, an escalation to the person who ran the meeting after a second late week, and no second task filed for the same ask.
---
# Chase a stale action item

When to use: an action item is past its stated date, or a question to a person has been open for more than a day with no
reply.

Steps:
1. Re-check the task with `hub task show <id>`. If it has moved, update the row in `knowledge/actions.md` and stop.
2. Classify the item: late (past its stated date), stale (an ask open more than a day with no reply), or no date (never
   stated, so it cannot be late). A no-date item is not chased; it is asked about once in the status page.
3. For a late item, draft one chase to the owner: the item, the meeting with the short quote and its timestamp, the date it
   was due, and one question, "new date?" or "blocked on whom?". Put the chase on the task for a human's yes, and send it only after that yes, as `playbooks/chase-open-actions.md` step 5 says. For a stale ask, draft the one re-ask on the same task and send it with
   `hub task ask <id>` only after the same yes.
4. Chase the same owner no more than once a week. A second late week goes to the person who ran the meeting, with the
   number of weeks it has been late.
5. When the owner says that someone else must act, do not file a new ask. Leave the original task with its owner,
   `hub task update <id> --status waiting --on <owner>`, and say in one line what it waits for.
6. An ask still unanswered after the re-ask is closed on its task with a note that says so and the date:
   "closed: no reply after one re-ask on <date>". The row stays in `knowledge/actions.md` with its last chased date.

Checks before you finish:
- Each chase names the item, the meeting, the short quote with its timestamp, the due date and one question.
- No owner received more than one chase in the same week.
- No new task was filed for an ask that already has one.
- Every closed ask says why, with the date.

Example (invented, for illustration): "Action: pull last quarter's trial-to-paid numbers. Owner unclear at the 2026-09-23
meeting (quote 'Someone should get the numbers', 00:44:02). Not chased: no owner and no date. Written as an open question
and asked once of the person who ran the meeting. The row stays in actions.md with the date asked."
