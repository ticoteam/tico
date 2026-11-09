# Weekly marketing summary

Schedule: Fridays at 14:00 team time (routine `weekly-marketing-summary`), after setup. Also run by hand on request. Budget 30 minutes. The outcome is one page for
the marketing owner: how the week went, what is blocked, the next six weeks on the calendar and
proposed priorities. Nothing is shared and no task is created.

Inputs: `knowledge/workstreams.md`, `knowledge/calendar.md`, `knowledge/priorities.md`, last week's page in `reports/`, and each workstream owner's tasks, updates and published reports since last Friday.
Done when: `reports/YYYY-MM-DD-marketing-week.md` is written and published; every workstream has a line (or "no report"); the calendar covers six weeks; the task note gives the headline, the path and any source that could not be read.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/workstreams.md`, `knowledge/calendar.md`, `knowledge/priorities.md` and last week's
summary in `reports/`.

## 2. Gather the week, by workstream

For each workstream in `knowledge/workstreams.md`, read what its owner produced since last Friday:
`hub task list --status open --status doing --status waiting`, `hub update list --kind weekly --bot <slug>`,
the owner's newest published report, and imported meetings that mention it. A bot or human with
nothing this week is "no report", never "on track".

## 3. Give each workstream one line

Status (red, amber, green, or no report), what moved since last week, the evidence with its date,
the owner and the next step. Red means blocked or missed; amber means at risk with a named reason.

## 4. Blockers and asks

List each blocker once: what is blocked, who can clear it, what they must decide, by when. Waiting on
missing facts or Tools are named as blockers.

## 5. Calendar, six weeks ahead

Update `knowledge/calendar.md` from `hub calendar list` and the workstreams. Flag collisions (two
sends or two launches the same day), launches inside two weeks with no brief or owner, and gaps.

## 6. Propose next week's priorities

At most three, each with the workstream, the reason and the owner. Say what was dropped and why.
Routing and reassigning tasks are not part of this playbook; a request for them goes through `playbooks/route-a-request.md`.

## 7. Write the page and hand it over

`reports/YYYY-MM-DD-marketing-week.md` in the shape of `knowledge/examples/marketing-week.md`. Then:

    hub file publish reports/YYYY-MM-DD-marketing-week.md

Commit, then `hub task update <id> --status done --note`: the headline, the path, what you could not read.
Always finish it: an open scheduled task absorbs the next.

## When a source fails

Name the source and what is therefore unknown in the closing line. A summary built from two
workstreams out of five that reads like the whole picture is worse than none.
