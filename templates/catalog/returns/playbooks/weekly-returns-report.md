# Weekly returns report

Schedule: Mondays at 09:00 team time (routine `weekly-returns-report`), after setup. Budget 30 minutes.

Inputs: the ledger for the last four weeks (`knowledge/ledger.md`), `knowledge/products.md`, the warehouse or tracking record of
returns received, the payment system's refund record if readable, and `knowledge/returns-practice.md`.

Outcome: one page in the shape of `knowledge/examples/returns-report.md`: what came back, what waits on a decision or a refund, which
products keep coming back, and the flags for the owner. The report itself refunds nothing and sends nothing.

Done when: every open request has a decision or a human's call; every refund past the promised time is named with who issues it;
every product pattern is counted per units shipped (or shown as a count below about 30 units); each flag states facts only; the report
is published; and the task note gives the counts and the path.

---

## 1. Work the open requests

Run `playbooks/decide-a-return.md` for every request without a decision. List them with the recommended decision and the reply ready
to act on.

## 2. Age the refunds

Every return received (the warehouse record, or the customer's tracking showing delivered back) without a refund issued, with the days
since receipt. Anything past the promised refund time goes first, with who issues it.

## 3. Count the week

Requests, approved, refused and exchanged; the refund total; the median days from receipt to refund. Compare with the four-week average
from `knowledge/ledger.md`.

## 4. Find the product patterns

Return counts and reasons by product and variant, from `knowledge/products.md`, with a rate only where the units shipped are about 30 or
more (`knowledge/returns-practice.md`). A variant returned for the same reason three times in a month ("runs small", "arrived damaged")
is a finding, with its orders listed.

## 5. List the flags

Follow `skills/flag-a-return-pattern/SKILL.md` for each pattern. The flags go to the owner only.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-returns.md` in the shape of `knowledge/examples/returns-report.md`, then
`hub file publish reports/YYYY-MM-DD-returns.md`. Commit, then `hub task update <id> --status done --note "..."` with the counts and the path.
