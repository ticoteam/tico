# Weekly reorder list

Schedule: Mondays at 07:30 team time (routine `weekly-reorder-list`), after setup. Budget 30 minutes. The outcome is a reorder list and one purchase order per supplier ready
for review. Nothing is ordered.

Done when: every item is either on the list with its inputs or marked as not planned, every at-risk item has its days
short, every purchase order is on the task with its total, the report is published, and the task note carries the
headline and the export dates used.

---

## 1. Get this week's data

The sales and stock exports on the task. If they are missing or older than 7 days, ask once with
`hub task ask <id>` and build the list from the last exports, saying so in the first line.

## 2. Update the item plan

For every item, run `skills/compute-a-reorder-point/SKILL.md` over the 90-day window. Add observed lead times from any
deliveries recorded since last week (ordered date to arrival). Note any item whose reorder point moved by more than 20
percent, and why.

## 3. Decide what to order

Items at or below reorder point: quantity to reach the reorder point plus the agreed cover, rounded to minimum or case
size. For each item that could run out, run `skills/work-out-the-last-order-day/SKILL.md`. Items whose last order day
is today or past are at risk: first on the page, with the days short. Skip discontinued items. Flag seasonal ones whose
season is ending.

## 4. Overstock and slow movers

Run `skills/review-slow-stock/SKILL.md`: items above the agreed months of cover, and items with no sale in 60 days, with
the value tied up where the export has cost. A suggestion (pause reorders, a bundle, a markdown) is an option for a
human to choose. Nothing is changed here.

## 5. Build the orders

One purchase order per supplier: lines, quantities, unit price from the price list with its date, and the total.
Attach each order and its total to the task. An order is placed only when the task asks for it and a granted Tool
allows it. Otherwise name the missing access on the page.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-reorder.md` in the shape of `knowledge/examples/reorder-list.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note` with the headline.
