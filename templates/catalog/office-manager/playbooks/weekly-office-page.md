# Weekly office page

Schedule: Mondays at 08:00 team time (routine `weekly-office-page`), after setup. Budget 20 minutes. The outcome is one page and, where stock is low, one combined order
ready to use. Nothing is ordered or booked.

Inputs: `knowledge/requests.md`, new tasks since last Monday, `knowledge/supplies.md`, `knowledge/visitors.md`,
`knowledge/office.md`, and `hub calendar list` for the next 7 days.

Done when: every section below is written, or says "none"; anything unsafe is first; each order has its total on the task;
the page is published; and the task note carries the headline.

---

## 1. Requests

Read `knowledge/requests.md` and new tasks since last Monday. For each open request: age in days,
fixer, promised date, and whether the promise has passed. A request past its promise gets a chase
prepared for the fixer (internal: `hub message send --fyi`; external: a message for review). Anything unsafe goes
first, whatever its age.

## 2. Supplies

Ask the human who stocks the kitchen or supply cupboard for counts if nobody logged them (one
`hub task create` a week at most), or use the last counts and usage. List
every item at or below par with the quantity to reach par plus two weeks' use. Group by supplier into
one order each, with the price last seen and its date. Run `skills/reorder-supplies-at-par/SKILL.md`.

## 3. Visitors and events

`hub calendar list` for the next 7 days. For each visitor or office event: host, time, room,
whether the host checklist in `knowledge/visitors.md` is done (reception told, room booked, Wi-Fi
guest details ready, catering if any). Missing items are named with the host. Run `skills/check-a-visitor-before-arrival/SKILL.md` for each visitor or event.

## 4. Office dates

From `knowledge/office.md`: cleaning, maintenance visits, fire equipment checks, inspections, and
lease dates inside 60 days.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-office.md` in the shape of `knowledge/examples/office-page.md` and
`hub file publish` it. Put each order and its total on the task; place an order only when the task asks for it and a granted Tool allows it; otherwise name the missing access on the page. Commit,
then `hub task update <id> --status done --note` with the headline.
