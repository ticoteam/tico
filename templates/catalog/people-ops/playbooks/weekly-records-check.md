# Weekly people records check

Schedule: Mondays at 10:00 team time (routine `weekly-records-check`), after setup. Budget 30 minutes.

Inputs: `knowledge/leavers.md`, `knowledge/systems.md`, last week's check, the open tasks, any HR and payroll exports attached to
this week's tasks, and `hub team show`.

Outcome: one page for the HR owner: leavers and what is still open, records that disagree, letters waiting for signature, and
policy acknowledgements outstanding. Nothing is changed or sent.

Done when: the page has a line for each leaver in progress, each mismatch shows both values with their dates, each unread source
is named, and the page is published on the task for the HR owner only.

---

## 1. Read where things stand

    hub task show <id>
    hub team show
    hub task list --status open --status doing --status waiting

Then `knowledge/leavers.md`, `knowledge/systems.md` and last week's check.

## 2. Leavers

For each leaver in progress: last day, checklist items done (with the dated confirmation), open, and late. Any privileged
access not confirmed removed 24 hours after the last day is the first line of the page. New leavers from the tasks get a
checklist (`playbooks/offboard-a-leaver.md`).

## 3. Records audit

If this week's tasks carry an HR export and a payroll export, compare them with each other and with `hub team show`, following
`skills/audit-hr-records-against-payroll/SKILL.md`. Missing documents (a signed contract, a right-to-work check where required)
are listed by reference and type.

## 4. Letters and acknowledgements

List letters prepared and waiting for signature, with the days each has waited (`skills/build-a-letter-from-records/SKILL.md`).
List each policy version and how many people have not acknowledged it.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-records-check.md` in the shape of `knowledge/examples/records-check.md`, then publish it with
`hub file publish reports/YYYY-MM-DD-records-check.md --scope task --task <id>`. Delete the exports from the working tree.
Commit, and run `hub task update <id> --status done --note "..."`.

## When a source fails

No export this week means no audit this week. Say so, and never repeat last week's findings as current.
