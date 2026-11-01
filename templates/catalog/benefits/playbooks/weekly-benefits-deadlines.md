# Weekly benefits deadlines

Schedule: Tuesdays at 09:00 team time (routine `weekly-benefits-deadlines`), after setup. Budget 25 minutes. The outcome is one page for the HR owner: what closes soon, who starts
or ends coverage, what the calendar needs next, and what was handed on. Nothing is submitted or sent.

Done when every deadline in the next 60 days has a source and a named owner, the page is attached to the task, and nothing has been
submitted or sent.

---

## 1. Read where things stand

    hub task show <id>
    hub team show
    hub task list --status open --status doing --status waiting

Then `knowledge/change-log.md`, `knowledge/benefits-calendar.md`, `knowledge/eligibility.md` and last week's page.

## 2. New changes

Joiners since last week (from the roster and `people-hr`), leavers with a last day set (from `people-ops`
or the tasks), and life events reported on tasks. For each, add a line to the change log: reference,
event, event date, the window from `knowledge/eligibility.md`, what the provider needs.

## 3. Count down

For every open line, follow `skills/count-a-life-event-window/SKILL.md`: the close date, the rule and its page, and
days left. Seven or fewer days left is bold and goes to the top with the human who must submit it. A closed window with
nothing submitted is reported plainly, with the date.

## 4. Coverage starting and ending

Joiners whose coverage starts in the next 30 days (and whether their enrollment is in), leavers whose
coverage ends (and whether the notices they are owed are prepared). Tell payroll what changes and from when.

## 5. The calendar

Milestones in the next 60 days: renewal meetings, enrollment opening, communications to prepare.
Enrollment opening within 30 days means the comparison (`playbooks/compare-plans.md`) should be ready.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-benefits-deadlines.md` in the shape of `knowledge/examples/benefits-deadlines.md`,
then `hub file publish reports/YYYY-MM-DD-benefits-deadlines.md --scope task --task <id>`. Reminders to
employees go on the task for review. Commit, and `hub task update <id> --status done --note`.

## When a source fails

A roster or document you could not read is named; a window is never computed from a guessed date.
