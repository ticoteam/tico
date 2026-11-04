# Weekly people summary

Schedule: Mondays at 08:30 team time (routine `weekly-people-summary`), after setup. Also run by hand. Budget 35 minutes.

Inputs: `knowledge/headcount-plan.md`, `knowledge/people-calendar.md`, `knowledge/routing.md`, last week's summary, `hub team show`,
`hub calendar list`, the open people tasks, and each people bot's newest report.

Outcome: one confidential page for the named readers: hires against plan, who starts and leaves, what is due in the next 30
days, what is blocked, and who should take what. Nothing is assigned, published or sent.

Done when: every section has its source and date, every unread source is named, the headline is line one, and the page is
published on the task for its readers only. Follow `skills/write-a-people-summary-with-counts-only/SKILL.md` throughout.

---

## 1. Read where things stand

    hub task show <id>
    hub team show
    hub calendar list

Then `knowledge/headcount-plan.md`, `knowledge/people-calendar.md`, `knowledge/routing.md` and last week's summary. If last
week named an action, check whether it happened and say so.

## 2. Hires against plan

For each planned role: target start date, status (not opened, open, interviewing, offer, filled), and days past target. Read
the status from `recruiting`'s latest pipeline report or the hiring tasks. A role with no source is "status unknown", never
"on track". Headline number: filled against planned this quarter.

## 3. Starters and leavers

Starters in the next 30 days (from `people-hr`'s tracker) and leavers with a last day in the next 30 days (from `people-ops`).
First name or reference, role, date. Flag any leaver with no offboarding checklist.

## 4. Deadlines

Every item in `knowledge/people-calendar.md` falling in the next 30 days, with owner and status. Anything due in 7 days and
not done is bold and goes into "Needs a human".

## 5. What the people bots produced

For each people bot in `hub team show`: its newest report and anything waiting on a human more than 5 days. One line each.
A bot you could not read is named.

## 6. Routing and hiring proposals

Give each unowned people request one routing line, following `skills/route-a-people-request/SKILL.md`. If the same unowned
work appeared three or more times this month, add one hiring proposal following `playbooks/propose-a-new-bot.md`. Create
nothing.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-people-summary.md` in the shape of `knowledge/examples/people-summary.md`, then
`hub file publish reports/YYYY-MM-DD-people-summary.md --scope task --task <id>`. Commit, and run
`hub task update <id> --status done --note "..."`: the headline, the path, and what you could not read.

## When a source fails

Name it and what is therefore unknown, and keep going. A count built from tasks alone says so.
