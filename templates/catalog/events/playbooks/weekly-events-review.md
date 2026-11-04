# Weekly events review

Schedule: Thursdays at 10:00 team time (routine `weekly-events-review`), after setup. Also run by hand. Budget 30 minutes. The outcome is one page: what is
coming, what is late, which follow-ups are due and what finished events returned. Nothing is booked
or sent.

Inputs: the task, `hub calendar list`, `knowledge/calendar.md`, `knowledge/rules.md`, `knowledge/results.md` and last week's review.
Done when: the page is at `reports/YYYY-MM-DD-events.md`, every event in the next 90 days has a status, late items have owners, and the task note names what could not be read.

---

## 1. Read where things stand

    hub task show <id>
    hub calendar list

Then `knowledge/calendar.md`, `knowledge/rules.md`, `knowledge/results.md` and last week's review.

## 2. Walk the next 90 days

For each event: days to go, goal, budget and spend so far, brief status, and the checklist items due
in the next two weeks (registration or early-rate deadline, booth and shipping, speakers, promotion
emails, staffing, lead capture set up). Anything past due is "late" with its owner.

## 3. Check follow-up

For each event that ended in the last 14 days: is the lead list on a task, were leads handed to sales,
and how many were contacted inside the deadline in `knowledge/rules.md`? Missing follow-up is the
first item under "Needs a human".

## 4. Count results

For events that ended 30 or more days ago, read the CRM (read only) or the attached exports: cost,
conversations, meetings, pipeline, and cost per meeting. Write one line per event into
`knowledge/results.md` with the lesson. Cost per meeting is cost divided by meetings held, with the arithmetic shown. Say "not yet measurable" rather than guess; `skills/score-an-event-after-it-ends/SKILL.md` sets out the full score.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-events.md` in the shape of `knowledge/examples/events-review.md`, then
`hub file publish reports/YYYY-MM-DD-events.md`. Commit, and `hub task update <id> --status done
--note`: the headline, the path, what you could not read.
