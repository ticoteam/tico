---
name: check-a-repeat-before-you-escalate
description: Decide whether a new ticket repeats a known issue, a known question or an open task, so each problem gets one count and one owner instead of a new escalation each time.
---
# Check a repeat before you escalate

When to use: before you write a reply that calls something a known issue, before you create a task for a person or
bot, and whenever a new ticket's title looks like one you have already seen.

Steps:
1. Compare ticket IDs, not titles. Automatic and copied titles repeat across unrelated tickets. An identical title is
   a lead, not a match.
2. Write the symptom in one line: what the customer did and what they saw. Then note the product version and the setup.
3. Search `knowledge/known-issues.md` and the open tasks for that symptom. Search the product's own report list too,
   where one is connected.
4. If the symptom and the version both match, add this ticket to the existing count and link the existing task. Do not
   open a second one.
5. If the symptom matches but the version or setup differs, it is not yet a repeat. Say which part differs and treat the
   ticket as new until a person says otherwise.
6. Count only what you read. A search that failed or timed out is "not checked", never "no repeat".
7. At the third matching ticket, write one line in `known-issues.md` (symptom, count, first and last date, owner of
   the fix) and send one task to that owner. Later repeats only raise the count.
8. A repeat is not proof of the same cause. In the reply, say "the same symptom as an earlier report" and never
   "the same cause" until someone has confirmed it.

Checks before you finish:
- The ticket ID and the existing entry or task ID are both on the task.
- Each match names the symptom and the version it matched on.
- Nothing already named by an open task was escalated a second time.
- Every search that failed is written as unread.

Example (invented, for illustration only): two tickets share the title "Export failed". One is a CSV export on version
4.2 that drops an empty column. The other is a PDF export on version 4.1 that times out. The symptoms differ, so the
second is not a repeat of the first. It gets its own line in `known-issues.md` with a count of one.
