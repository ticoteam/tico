---
name: estimate-a-job-length-from-history
description: Set the planned length of a job type from completed jobs in past exports, not the booking default, so the plan's times match what crews actually take and the buffers before fixed-time jobs are real.
---
# Estimate a job length from history

When to use: when a job type's length is first set, when the planned length has been wrong twice in a month, or when a
clash came from a job that ran over.

Steps:
1. Take the completed jobs of one type from the last 90 days of exports. Use actual start and finish times, not the
   booked time.
2. Drop jobs that were not a normal visit: a second visit for a part, a job cancelled on site, a multi-day scope. Count
   what you dropped and why.
3. Use the median of the rest as the planned length. Use the 80th percentile to set the buffer before a fixed-time job.
   With fewer than eight clean jobs, write "estimate, few jobs" beside the length and keep the booking default visible.
4. Split by crew only when the gap is large and steady: at least eight jobs each, and the same direction across the
   period. A split is a fact about how a crew works on that type. Write it in `knowledge/crews.md` with its evidence. It
   is not a rating of a person.
5. Write the result in `knowledge/job-types.md`: median, 80th percentile, job count, date range, date computed, and the
   booking default beside them.
6. Plan with the planned length. Do not use a length to judge a crew's performance on a single job.

Checks before you finish:
- The length has a count, a date range and a computed date next to it.
- Every dropped job is counted, with its reason.
- No judgement about a named person appears in the file.

Example (generic): "Electrical test, median 58 minutes, 80th percentile 74 minutes (34 jobs, 2026-07-01 to 2026-09-27;
booking default 45). Plan uses 60 minutes and leaves 75 minutes before the next fixed-time job. Dropped: 4 second visits
for a part, 1 job cancelled on site."
