---
name: build-three-real-slots
description: Find three interview times that every panel member can keep, inside the interviewers' rules and the candidate's time zone, and say which calendar each one was checked against.
---
# Build three real slots

When to use: before you offer any times to a candidate, or when a booked interview needs a new time.

Steps:
1. Read `knowledge/interviewer-rules.md` (hours, daily maximum, buffer, days off) and the loop in `knowledge/loops/<role>.md`
   (rounds, length, panel).
2. Read each panel member's calendar for the window (default: the next five working days): `hub calendar list --calendar <email>`.
   If one calendar cannot be read, that person has no known free time. Say so, and do not offer slots that depend on them.
3. Remove every busy block, the buffer before and after each one, and every time outside the interviewer rules.
4. For a loop with several rounds, prefer rounds back to back on one day, with a short break between them. Keep to at most
   two days.
5. Pick three options that do not share a slot with another candidate's interview for the same panel member.
6. Convert each option to the candidate's time zone and the team's, and write both. Name the time zone once in the message.
7. Record the calendars you checked and the time they were read, in the task note.

Checks before you finish:
- Each option is free on every panel calendar you read, after the buffers.
- No option is outside the rules or the working day in either time zone.
- Every calendar you could not read is named, and no option depends on it.
- The three options are different days or different times on purpose, not three copies of one slot.

Example (generic, invented): Loop: technical round (60 minutes) and team round (30 minutes), panel of three. Option A: Tuesday
09:30 and 10:45 in the team's zone, 12:30 and 13:45 in the candidate's. Option B: Wednesday 14:00 and 15:15. Option C: Thursday 10:00 and 11:15. Calendars read 2026-11-01 07:55, all three panel members. Every option is free on all three.
