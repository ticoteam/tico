# Weekly tier 2 queue report

Schedule: Thursdays at 09:00 team time (routine `weekly-tier2-report`), after setup. Budget 30 minutes.

Inputs: the open and waiting tier 2 tasks, the bug issues read-only, `knowledge/workarounds.md` and the case files in `knowledge/cases/`.

Outcome: one page on the technical queue: what is open, what engineering is holding, what was solved, and what keeps coming back.

Done when: every open tier 2 ticket is listed with its verdict and what it waits on, every bug has its engineering status with the date it was read, the report is published, the replies ready for review are on the Support Agent's tasks, and the task is done with the counts.

---

## 1. List the queue

Every open tier 2 task (`hub task list --owner technical-support --status open --status waiting`) with
its age, verdict so far and what it waits on: the customer, engineering, or you.

## 2. Check engineering status

For each bug reported, the issue's state and last movement (read only). A bug with no response for five
working days is flagged with its customer count. Word each status as `skills/word-a-fix-status/SKILL.md` says.

## 3. Count what came back

Problems seen more than once this week or this month, from `knowledge/workarounds.md` and the cases.
A problem seen three times with a workaround and no fix is the week's finding: it costs support time
every week until fixed.

## 4. Prepare replies

For tickets where the investigation finished, the customer reply is ready on the Support Agent's task
for review. In the report, list each of those tickets with its verdict and the status of its reply.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-tier2.md` in the shape of `knowledge/examples/tier2-report.md` and
`hub file publish` it. Commit, and finish the task with the counts and the path.
