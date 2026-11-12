# Weekly success summary

Schedule: Mondays at 09:00 team time (routine `weekly-success-summary`), once the routine is switched on. Budget 40 minutes.

Inputs: the support owner's and the success owner's newest reports and open tasks (`tico update list --kind weekly`,
`tico task list`), the tickets and renewals they report, `knowledge/team.md` and last week's summary.

Outcome: one page for the owner: how support and success did against target, what is waiting and for how long, what keeps
coming back, what needs a decision, the routing and hiring proposals, and how your pipelines moved.

Done when: the summary is published with its source for every figure, the lowest measure is named against its target, every
blocked item has an owner, every task that stopped this week shows its decision, and the task is done with its headline.

1. Read where things stand: `tico task show <id>`, last week's summary, `knowledge/team.md`.
2. Support: response times against target (median and slowest tenth, with counts), the backlog by age, the oldest five and
   who holds each.
3. Success: renewals and at-risk accounts in the next 60 days, customer health changes, onboarding stalls, each with its
   owner.
4. Repeats: the same request three times or more in the period, and whether an owner has it.
5. Decisions: at most three, each one line with the figure, the deadline and the options, asked with `tico task ask`.
6. Proposals: routing for stuck work, and a hire only where repeat work has no owner (with the evidence).
7. Pipeline: read the open tasks (`tico task list --status open`; each row has its stage and its Turn line; check a
   doubtful one with `tico task show <id>`), the snoozed ones (`tico task list --snoozed only`) and the tasks you were
   woken about this week (`tico task list --member me`). Write the Pipeline section in this order: tasks per stage; each
   task that stopped this week with why and the decision made; tasks on ice; what waits on a person, and since when; the
   repeats worth a proposal; and what was not read. A task you could not read is "not read", not clean. No person is
   named as the cause of a stop.
8. Write `reports/YYYY-MM-DD-success-summary.md`, publish it with `tico file publish`, and finish the task with the headline,
   the path and what you could not read.

Write only what the reports and tasks say. A number you could not read is "not available", never a guess.
