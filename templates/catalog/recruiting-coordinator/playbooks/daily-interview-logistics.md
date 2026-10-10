# Daily interview logistics

Schedule: weekdays at 08:00 team time (routine `daily-interview-logistics`), after setup. Budget 20 minutes. The outcome is one sheet: today and tomorrow ready, waiting candidates
offered times, scorecards chased, debriefs ready to book. Messages to outsiders stay drafts until a person turns mail sending on in Tico.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --status open --status doing --status waiting

Then `knowledge/schedule.md`, `knowledge/interviewer-rules.md` and yesterday's sheet.

## 2. Today and tomorrow

For each interview: the time in both time zones, the panel, the join link or room, and kit status per
interviewer (sent, not sent). Check each interviewer's calendar still shows the event
(`hub calendar list`); a conflict or a declined event goes to the top of the sheet with a fix.

## 3. Candidates waiting for times

Anyone moved to interview with no time yet: follow `playbooks/schedule-an-interview-loop.md` and put the
message up for review. A candidate waiting more than one working day for times is flagged.

## 4. Scorecards

For interviews that ended before today: who has submitted and who has not. Nudge each late interviewer
once with `hub message send <human> "Scorecard for <reference>, <role>, due <time>"`. Two working days late goes
to the hiring manager.

## 5. Debriefs

A candidate whose loop is complete and whose scorecards are all in is ready for a debrief: propose a
30 minute slot for the panel and build the pack (each interviewer's scores and notes side by side, in the
order they were submitted). Book requested debriefs within the scheduling rules and your Tools.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-interview-logistics.md` in the shape of `knowledge/examples/interview-logistics.md`,
then `hub file publish reports/YYYY-MM-DD-interview-logistics.md --scope task --task <id>`. Update
`knowledge/schedule.md`, commit, and `hub task update <id> --status done --note`.

## When a source fails

A calendar you cannot read means no slots for that interviewer today: say so; never guess free time.
