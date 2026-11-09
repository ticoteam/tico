# Daily interview logistics

Schedule: weekdays at 08:00 team time (routine `daily-interview-logistics`), after setup. Budget 20 minutes.

Inputs: `knowledge/schedule.md`, `knowledge/interviewer-rules.md`, yesterday's sheet, the open loop tasks
(`hub task list --status open --status doing --status waiting`), and each interviewer's calendar for today and tomorrow.

Outcome: one sheet in the shape of `knowledge/examples/interview-logistics.md`: today and tomorrow, candidates waiting for
times with the offers made, scorecards chased, and debriefs ready to book. Messages to outsiders stay drafts until a person
turns mail sending on in Tico.

Done when every interview in the two days has a line with kit status, every late scorecard is named, the sheet is published,
and the task note carries the headline.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --status open --status doing --status waiting

Then read `knowledge/schedule.md`, `knowledge/interviewer-rules.md` and yesterday's sheet.

## 2. Today and tomorrow

For each interview: the time in both time zones, the panel, the join link or room, and kit status for each interviewer (sent
or not sent). Check each interviewer's calendar still shows the event (`hub calendar list`). A conflict or a declined event goes
to the top of the sheet with a proposed fix.

## 3. Candidates waiting for times

A candidate moved to interview with no time yet: run `playbooks/schedule-an-interview-loop.md` and put the message up for review.
A candidate waiting more than one working day for times is flagged.

## 4. Scorecards

Run `skills/chase-scorecards-and-build-a-debrief-pack/SKILL.md` for each interview that ended before today. The sheet shows who
has submitted and who has not, and what has been nudged.

## 5. Debriefs

A candidate whose loop is complete and whose scorecards are all in is ready for a debrief. Propose a 30-minute slot for the
panel, and build the pack as the skill describes. Book a requested debrief only when the task asks for it and the calendar tools
allow it.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-interview-logistics.md` in the shape of `knowledge/examples/interview-logistics.md`, then
`hub file publish reports/YYYY-MM-DD-interview-logistics.md --scope task --task <id>`. Update `knowledge/schedule.md`, commit,
and close the task with `hub task update <id> --status done --note "..."`.

## When a source fails

A calendar you cannot read means no known free time for that interviewer today. Say so on the sheet. Never guess a free time.
