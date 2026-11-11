---
name: count-impact-from-sources
description: Count the users, requests, minutes or money an incident affected from its raw sources, reading full lists rather than the first page, keeping the app's own guards apart from backend errors, grouping by build version where cause matters, and recording the window, the source and any cap.
---
# Count impact from sources

When to use: before you write any number of affected users, failed requests, minutes of impact or money lost into a
timeline, a postmortem or an update.

Steps:
1. Name the question in one line: what is counted, over what window (UTC), and for which surface or customer group.
2. Name the source and its limits. Note any cap or page size. A count endpoint may stop at 100 results, and an export
   may hold only its first N rows. If the source is capped, page through to the end of the list before you count.
3. Read every event before you name a pattern. Count by status across all events, not the first few. Recount from
   the full list before you write the total.
4. Keep the cause types apart. An error raised by the app's own guard, such as a rate limit, a validation message or
   a deliberate refusal, is not a backend error. Count them separately and say which one the incident is about.
5. Do not count an action that seemed to do nothing as evidence of impact until you know it reached the system.
   A tap that did nothing may never have fired.
6. Group by build or release version when the question is which change caused it. A closed issue is not evidence
   that the errors stopped, because a resolved issue can regress. Count again on the version that is live.
7. Write the number with its window, source, cap and method: "312 failed bookings, 14:02 to 14:47 UTC, from the
   bookings export (all rows read, no cap)." If a figure is an estimate, write "approx." and say how it was made.
8. If no source gives the number, write it as a gap. Do not round a guess into a figure.

Checks before you finish:
- Every number has a window, a source and a method.
- Any cap or page limit was noticed and handled.
- Errors from the app's own guards are not counted as backend failures.
- Versions are named when the question is cause.
- A missing number is written as a gap, not a figure.

Example (invented, for illustration): An export shows 1,140 failed requests, but its count endpoint stops at 100 per
page, so the list was read to its end. That gives 1,046 backend failures and 94 rate-limit refusals from the app's
own guard. Written as: "1,046 backend failures, 14:02 to 14:47 UTC, from the request log (all pages read). A further
94 rate-limit refusals are not counted as impact."
