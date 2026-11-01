---
name: read-the-backlog-by-age-and-owner
description: Read the open backlog by age bucket and owner, name the oldest tickets with what each waits on, and say why a ticket has stayed open for two weeks or more, or that the record does not show why.
---
# Read the backlog by age and owner

When to use: when a summary says whether the backlog is growing, names the oldest waiting tickets, or calls a
ticket stuck.

Steps:
1. Count the open tickets from the record (`hub task list --owner <support bot> --status open` and
   `--status waiting` for each support bot), not from memory. Write the time of the count.
2. Put each open ticket in one age bucket: 0 to 2 days, 3 to 7 days, 8 to 14 days, or 15 days and older. Age runs
   from the customer's first message when it is known, and from the task's creation when it is not. Say which.
3. For each ticket in the 8 to 14 and 15-plus buckets, write its owner (or "none") and what it waits on: a customer,
   a colleague, a fix, or nobody, which means no one has picked it up.
4. Name the oldest five with age, owner and waiting-on. Keep the list about the tickets, not a ranking of people.
5. For a ticket open two weeks or more, say which of these the record shows: it went to the wrong role; it is stalled
   on a customer (give the last touch and the nudge date); its owner has no capacity (give their open count); or a
   fix is still open (give its status). If none shows, write "cause not visible in the record".
6. Compare the bucket counts with last week's summary. Name a direction only when both counts are dated and read.
7. If a bucket cannot be counted, write "not counted" in it. Never write zero for a bucket you did not read.

Checks before you finish:
- The count's date and the age rule are on the summary.
- Every ticket named appears in a record you read.
- No bucket shows zero unless it was read.
- Each old ticket has an owner or "none", and a waiting-on.

Example (invented, for illustration only): on 2026-09-28, 31 tickets were open: 14 aged 0 to 2 days, 11 aged 3 to 7,
4 aged 8 to 14 and 2 aged 15 or more. The two oldest had no owner after their assigned bot was switched off on
2026-09-16. The summary names both, routes them through a decision ask, and does not call the backlog grown, because
last week's count is not in the record.
