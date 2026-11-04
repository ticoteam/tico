# Weekly milestones and engagement actions

Schedule: Thursdays at 10:00 team time (routine `weekly-milestones-and-actions`), after setup. Budget 20 minutes. The outcome is one page:
the next two weeks' milestones with a note ready for each manager, survey actions and their status, and what is coming. Nothing is posted.

Done when every milestone in the window has a line and, where the manager asked, a note; every open action has an owner and a status;
the page is published; and each manager note is on its task for review.

---

## 1. Read where things stand

    hub task show <id>
    hub team show
    hub calendar list

Then `knowledge/milestones.md`, `knowledge/actions.md`, `knowledge/survey.md` and last week's page.

## 2. Milestones

From the roster's start dates: work anniversaries and first years in the next 14 days; birthdays only for
people in `knowledge/milestones.md` who opted in. For each, one line and a two-sentence note the manager
can send or adapt, specific to the milestone (a first year names what the person's team shipped since
they joined, if the manager has said). Add new joiners to the milestones file with their start date.

## 3. Survey actions

Each open action: owner, due date, status (done with its record, on track, late). A late action gets one
line to its owner on its task. An action done but not yet told to employees is flagged: telling people
is what makes the next survey worth answering.

## 4. What is coming

Next pulse date and what must be ready two weeks before (questions confirmed, the launch message for review); events in the next 30 days with their budget status.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-milestones-and-actions.md` in the shape of
`knowledge/examples/milestones-and-actions.md`, then `hub file publish
reports/YYYY-MM-DD-milestones-and-actions.md --scope task --task <id>`. Manager notes go on the task for
each manager. Commit, and `hub task update <id> --status done --note`.

## When a source fails

A person with no start date in the roster is left off, never guessed; say how many.
