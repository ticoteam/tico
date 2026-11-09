# Weekly success summary

Schedule: Mondays at 09:00 team time (routine `weekly-success-summary`), once the routine is switched on. Budget 40 minutes.

Inputs: the support owner's and the success owner's newest reports and open tasks (`hub update list --kind weekly`,
`hub task list --status open`), the tickets and renewals they report, `knowledge/team.md` and last week's summary.

Outcome: one page for the owner: how support and success did against target, what is waiting and for how long, what keeps
coming back, what needs a decision, and the routing and hiring proposals.

Done when: the summary is published with its source for every figure, the lowest measure is named against its target, every
blocked item has an owner, and the task is done with its headline.

1. Read where things stand: `hub task show <id>`, last week's summary, `knowledge/team.md`.
2. Support: response times against target (median and slowest tenth, with counts), the backlog by age, the oldest five and
   who holds each.
3. Success: renewals and at-risk accounts in the next 60 days, customer health changes, onboarding stalls, each with its
   owner.
4. Repeats: the same request three times or more in the period, and whether an owner has it.
5. Decisions: at most three, each one line with the figure, the deadline and the options, asked with `hub task question`.
6. Proposals: routing for stuck work, and a hire only where repeat work has no owner (with the evidence).
7. Write `reports/YYYY-MM-DD-success-summary.md`, publish it with `hub file publish`, and finish the task with the headline,
   the path and what you could not read.

Write only what the reports and tasks say. A number you could not read is "not available", never a guess.
