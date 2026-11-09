---
name: "check-an-all-clear"
description: "Before an incident is called over or resolved, confirm the impact has ended from fresh evidence: the source is online, the error rate is at baseline for a stated window, the queue is empty and the held work ran, and no open child task says otherwise. Record each check and any check you could not read."
---
# Check an all-clear

When to use: before you write that an incident is over, resolved or "all clear", in a timeline, a review, or a task note.

Steps:
1. Name the claim exactly: what is declared over, and from what time (UTC). "The booking errors ended at 14:47 UTC" is
   a claim. "It is fixed" is not.
2. Read the health signal now, not the last reading in the record. Confirm the source is still online and that its
   timestamp is recent. A dashboard that stopped updating shows its last value, not the current one.
3. Check the error rate against baseline over a stated window, such as the last 15 minutes. Name the baseline and its source.
4. Check the queue or backlog. An empty queue is not enough on its own. Confirm that the work held during the incident
   ran, using its own record: a job ID, a sent message, a row written. Until that record is read, the all-clear stays unverified.
5. Check the incident's open child tasks and action items. A task still open that says the problem continues blocks the all-clear.
6. Check that the fix reached where it runs. A closed ticket or a merged change does not show that a deploy reached
   production. Name the version or build that shows it.
7. Write the result with its time, source and window: "all clear at 15:25 UTC; error rate at baseline for 15 minutes
   from the request log; checks 4 to 6 read." If a check could not be read, do not write all clear. Write "resolution
   unverified" and name the missing read, for example: "resolution unverified: the retry log could not be read, so the
   held payments are not confirmed as retried."

Checks before you finish:
- The all-clear names a time, a source and a window.
- The health signal was read at the time of the claim, not copied from an earlier note.
- The held work was confirmed by its own record, not by an empty queue alone.
- Any unread check is named, and an unread check means "resolution unverified", not all clear.

Example (invented, for illustration): A payment retry queue shows 0 items at 15:10 UTC. The retry job last logged at
14:31 UTC, and the record does not show the held payments were retried. Result: not an all-clear. The entry reads
"queue empty; held payments not confirmed as retried; check the job log." At 15:25 UTC the job log shows 212 retries
at 15:12 UTC, the error rate is at baseline for 15 minutes, and no child task is open. Result: all clear at 15:25 UTC.
