---
name: measure-response-times-against-target
description: Measure first response and resolution time for a set of tickets against the team's target, with the start point, the median and the slowest tenth, and the count behind each, so a number can be checked later.
---
# Measure response times against target

When to use: before you state how fast customers were answered or how long tickets took to resolve, in a weekly
summary, a decision ask or a note to the owner.

Steps:
1. Name the start point before you measure. Use the customer's first message when the support mailbox was read, and
   the task's creation time when only tasks were read. Write which one on the same line as the figure.
2. Name the end point. First response ends at the first public reply to the customer, not at an internal note.
   Resolution ends at the close, or at the final reply if the ticket is reopened and closed again. A "we are
   checking" reply ends the first-response clock only.
3. Build a separate cohort for each measure. First response: each ticket with a public reply to the customer in the
   period. Resolution: each ticket that reached its final close in the period. Count the tickets you left out of each
   cohort and give the reason (for example, another team owned them until the close).
4. Compute the median and the slowest tenth for each measure from its completed cohort, and the count behind each. Under
   ten tickets, give the count and the longest time only. Report tickets still open at the period's end separately, as
   unresolved waits: how many, and the age of the oldest.
5. Compare with the target in `knowledge/targets.md`. Quote the target's date and who set it.
6. If the target is in business hours, measure in business hours. Say which calendar you used.
7. If a source could not be read, do not drop its tickets quietly. Name the source and mark the figure "unverified".

Checks before you finish:
- The start point, end point, period and count sit on the same line as each figure.
- The target is quoted with its date.
- No average stands alone. If one is shown, the median is beside it.
- Left-out tickets are counted and the reason is named.
- Open tickets are not in a resolution median. They appear only as unresolved waits.

Example (invented, for illustration only): 58 tickets had a first reply in the week of 2026-09-21. Measured from the
customer's first email, the median first reply is 2h10m and the slowest tenth is 7h40m. The target is 4 business
hours, set on 2026-09-01 by the support owner. Two tickets were left out because another team owned them until the
close, and that is named in the summary.
