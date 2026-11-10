# Weekly segment growth review

Schedule: Tuesdays at 09:00 team time (routine `weekly-segment-growth-review`), after the Monday numbers and the Monday
growth meeting. Also run by hand. Budget 40 minutes. The outcome is one page: the segment's new customers and cost per
customer against goal and ceiling, its tests, its asks and its message. Nothing is started, spent, changed or sent.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --label segment:<slug>

Then `knowledge/segment.md`, `knowledge/message.md`, `knowledge/asks.md` and last week's review. Note the date of the
last review: steps 3 and 4 read from it.

## 2. Read the number

    hub growth numbers
    hub growth cohort list --month <last full month, YYYY-MM>
    hub goal list --all
    hub kpi show <id>

Use `skills/read-cost-per-customer-for-a-segment/SKILL.md` for the segment's new customers and cost per customer. Write
each figure with its split methods and its month. For each KPI you own, log the reading:

    hub kpi log <id> <value> --period-start <YYYY-MM-01> --period-end <YYYY-MM-DD>

Log nothing for a KPI you do not own. If the goal names no KPI, say so in the review and ask the Growth Team Manager (`growth-team-manager`).

## 3. Read the tests

    hub task list --label growth:running --label segment:<slug>
    hub task list --label growth:measuring --label segment:<slug>

For each running or measuring test, note its channel, its update since the last review and its numbers. Read the close-out
comment of each test that finished since the last review. A running test with no update for two weeks is named in the
review; the growth meeting nudges its owner.

## 4. Read the asks

For each ask in `knowledge/asks.md`, note whether it is answered, open, or open for seven days or more. Record each
answer with its date and the number it gave. Add a reminder comment to any ask open seven days, with
`hub task comment <id> "<reminder>"`. Ask the Growth Team Manager (`growth-team-manager`) on the task for any ask open 14 days.

## 5. Check the message

If the copy changed since the last review, or a claim is in use that is not in `hub brand show --section claims`, run
`skills/test-a-segment-message/SKILL.md` on it. If nothing changed, say so in one line.

## 6. Plan the next moves

Run `playbooks/plan-a-growth-test.md`: up to three ideas for the Monday growth meeting. An idea already filed or running for this
segment is not filed again.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-segment-growth-review.md` in the shape of `knowledge/examples/segment-growth-review.md`, then
`hub file publish reports/YYYY-MM-DD-segment-growth-review.md`. Update `knowledge/asks.md` and `knowledge/message.md`,
commit this repository, and finish with `hub task done <id> -m "..." --version N`: the number against goal and
ceiling, the tests, the asks, the ideas filed and what you could not read.

Done when: the number is written with its split methods and month; every running test has a line; every open ask has its
task id and age; the reconciliation is written, or its gap is named; and the review is published.
