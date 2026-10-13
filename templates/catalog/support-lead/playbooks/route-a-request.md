# Route a request

Triggered by a task that says a request is stuck, misrouted or has no owner, or by step 4 of the weekly
summary. Budget 10 minutes. The outcome is a routed task naming who should take it, and why.

---

## 1. Read the request and its history

    hub task show <id>

Note who has touched it, how long it has waited and what it is waiting on (a customer, a human, a fix).
Read `knowledge/team.md` for who owns what and who is covering today.

## 2. Pick the owner by what the work is

- Answering a customer: the Support Agent (`support`) prepares the reply, send within the requested work when a person has turned mail sending on in Tico.
- A repeated question the docs do not answer: the Librarian (`librarian`), as a task naming the question and the tickets.
- A reply that already went out and may be wrong: the Support Quality Analyst (`support-qa`).
- A technical problem that needs reproducing: the Technical Support Engineer (`technical-support`).
- A key account or VIP waiting too long: the Escalations Manager (`escalations`).
- A cancellation or downgrade: the Retention Specialist (`retention`); a return or refund of an order:
  the Returns and Refunds Specialist (`returns`).
- A new customer stuck in setup: the Customer Onboarding Specialist (`onboarding-specialist`).
- Account health or adoption: the Customer Success Manager (`customer-success`); price or renewal terms:
  the Account Manager (`account-manager`) or a seller.
- Tickets landing in the wrong queue, or a macro quoting old policy: `support-ops`.
- A theme or feature request: the Customer Insights Analyst (`feedback-analyst`), or a human in product.
- A refund decision beyond policy, legal, security or an outage: a named human in `knowledge/team.md`, now.
- A defect: the head of engineering or the product-issue owner, with the ticket count.

If two owners fit, say which you would choose and why. If the human in `team.md` is away, name their cover.

## 3. Route the requested work

On the task: one line naming the owner and the reason, the age and what it waits on.
Create the task with `hub task create --owner <owner> --parent <id>`, link the ticket, and note it in
`knowledge/decisions-needed.md`. If the routing rules do not identify an owner, ask the support owner
with `hub task ask <id>` for that missing information. Record useful routing lessons in
`memory/learnings.md`.
