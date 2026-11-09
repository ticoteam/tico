---
name: check-a-training-record
description: Decide whether a training record proves a completion for a named course, checking its source, date, reference, course name against the mandatory list and expiry rule, and report complete, due, overdue, no record, or unclear, never assuming completion from attendance or silence.
---
# Check a training record

When to use: before any course is marked complete in the tracker, and whenever a person says they finished a required course.

Steps:
1. Find the record: a provider export, a certificate on the task, or the person's own statement on the task. Note where it came
   from and its date.
2. Match the course: the name must match a course in `knowledge/mandatory.md`. A similar name is not a match. Note the mismatch.
3. Match the person by reference: the roster reference on the export, or the reference the task uses. A name match alone is not
   enough when two people share a name.
4. Check the completion: a pass or a finished status, not an enrolment, a started course or a booked session.
5. Work out the expiry: last completion plus the frequency in `knowledge/mandatory.md`. Note the due date for the next completion.
6. Mark the result: "complete" (record, course, person and date all match, and the completion has not expired), "due" (complete, with the next due date within 30 days), "overdue" (the due date has passed, with no newer record), "no record" (nothing found), or "unclear" (a record exists but one field does not match). Say which field.
7. Do not mark anything complete on attendance, on a calendar entry, or on silence. A missing record is "no record", not "overdue"
   until the due date passes.
8. Keep the record reference on the tracker line. Delete the export from the working tree at the end of the run.

Checks before you finish:
- Every "complete" has a source, a date, a matched course and a due date after the check date.
- No "complete" comes from a calendar entry, attendance or silence.
- "Unclear" names the field that did not match.
- Expiry dates are computed from the frequency in the mandatory list.

Example (invented, for illustration): "Security awareness (annual), person ref P-031. Source: provider export on T-478, dated
2026-09-30, status passed, completed 2025-09-28. Matched to the course list. Expiry: 2026-09-28 under the annual rule; overdue
from 2026-09-29 until the new completion is recorded. Result: overdue."
