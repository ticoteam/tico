# Weekly onboarding board

Schedule: Mondays at 10:00 team time (routine `weekly-onboarding-board`), after setup. Budget 30 minutes.

Inputs: every plan in `knowledge/customers/`, the deals closed since last week (a CRM read, or tasks naming a signed order),
`knowledge/milestones.md`, the stuck rule, and the meetings, tasks, tickets and usage readings you can read.

Outcome: one page in the shape of `knowledge/examples/onboarding-board.md`: how many customers are on track, behind, stuck, waiting
or blocked; each stalled customer in one stall state with its reason; the go-live blockers; the new customers needing a plan; and the
customer messages ready to use. Nothing is sent.

Done when: every customer in onboarding is on the board with one status; every stuck, waiting or booked customer has a stall state
and a date; every new customer has a plan or a dated step to make one; the board is published; and the task note gives the counts and the path.

---

## 1. Find who is in onboarding

Every plan in `knowledge/customers/` without a go-live date, plus deals closed since last week. A new customer with no plan goes to
the top of the board and gets one today (`playbooks/plan-a-new-customer.md`).

## 2. Read the evidence of progress

For each customer: `hub meeting search "<customer>"`, `hub task list` for tasks naming them, support tickets routed to Tico, and a
usage reading if one is in your access. Log each dated fact in the plan's progress log. A milestone is done only with evidence.

## 3. Sort

- **On track**: the current milestone on or before its target date.
- **Behind**: past the target date but moving.
- **Stuck, waiting or booked**: no progress past the stuck rule. Classify each with `skills/classify-a-stalled-customer/SKILL.md`.
- **Blocked**: a go-live date at risk. Check it with `skills/check-go-live-blockers/SKILL.md`.

## 4. Prepare the next step

For each stuck, behind or blocked customer, one next step with an owner and a date. Where the step is a message to the customer,
write the exact text (under 120 words, one clear ask, their goal in the first line) and the recipient, ready to use. Nothing is sent.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-onboarding-board.md` in the shape of `knowledge/examples/onboarding-board.md`, then
`hub file publish reports/YYYY-MM-DD-onboarding-board.md`. Put the messages on the task as separate items so each can be checked on
its own. A customer who reached go-live gets a handover task to `customer-success`. Commit, then
`hub task update <id> --status done --note "..."` with the counts and the path.
