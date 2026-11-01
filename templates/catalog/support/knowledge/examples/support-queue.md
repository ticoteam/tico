A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme support queue, Tue 2026-09-29, 09:00

Sample output for Acme, a fictional team. Every ticket, name and address below is invented.

**Headline: 11 arrived since yesterday, 8 drafted from the docs, 2 need a person today, 3 follow-ups due, 1 doc gap sent to the Librarian.**

## Needs a person today
- **T-2041, refund request on the studio plan** (customer says they were charged twice). Task created for the
  billing owner: "Decide on a refund for a double charge". The draft has a marked gap where the refund would go.
  Not promised.
- **T-2046, possible outage**: three studios cannot open the booking page since 08:10. Task created for the
  on-call engineer. The draft is a holding reply with no time promised.

## Drafts ready to use (8)
| Ticket | Bucket | Draft says | Rests on |
|---|---|---|---|
| T-2038 | Answered by the docs | How to reset a studio's calendar link | Librarian, doc "Calendar settings", asked 2026-09-29 |
| T-2039 | Answered by the docs | Invoices are emailed on the first of the month | Librarian, doc "Billing FAQ", asked 2026-09-29 |
| T-2040 | Known issue | Calendar sync can lag up to 10 minutes; a fix is being worked on | known-issues.md, "Sync lag", count now 4 |
| T-2042 | New | Asks which plan includes SMS reminders. Draft says we are checking and will confirm | Librarian: "Not in the docs" |

The other four are the same shape and are on the task.

Draft for T-2038, for the owner to review (nothing has been sent):

> Hi, thanks for asking about resetting the calendar link. Open Settings, then Calendar, and choose
> "Reset link". Your old link stops working straight away, so share the new one with your studio. If the
> option is missing, tell us your plan and we will take a look.

## Follow-ups due (3)
- **T-2031**, waiting on the customer since 2026-09-24 (5 days, rule is 3): first nudge drafted on the task. It
  restates the question and adds where the setting is.
- **T-2027**, waiting on the sync fix owner since 2026-09-22: asked again on the task, with the one question that is
  due today.
- **T-2019**, second nudge sent 2026-09-22 with no reply since: marked closed quiet today, with both dates and the
  reason.

## Patterns and doc gaps
- **Calendar sync lag** reached 4 tickets in 10 days. One task for the sync fix owner with the ticket references
  and the count. Not escalated again.
- **SMS reminders by plan** asked twice and the docs do not say. Task created for the Librarian with both ticket
  references and the question. No answer was written by the agent.
- **Repeat check**: T-2044 has the same title as T-2040 ("Calendar not updating") but a different symptom (a
  missing booking, not a late one). Not counted as a repeat; new line in known-issues.md, count 1.

## Could not read
The Slack support channel refused access (not connected). Only the support mailbox was read.

## Sources
- Support mailbox, 11 messages 2026-09-28 09:00 to 2026-09-29 08:55
- Librarian answers to 6 questions, asked 2026-09-29; `knowledge/known-issues.md`, `knowledge/follow-ups.md`, read 2026-09-29
```
