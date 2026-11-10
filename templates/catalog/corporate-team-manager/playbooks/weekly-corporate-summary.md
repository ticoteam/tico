# Weekly corporate summary

Schedule: Mondays at 09:00 team time (routine `weekly-corporate-summary`), once the routine is switched on. Budget 40 minutes.

Inputs: `knowledge/deadlines.md` and `knowledge/team.md`; the legal, finance and partnership owners' newest reports and open
tasks (`hub update list --kind weekly`, `hub task list`); the Finance Owner's latest headline from its report.

Outcome: one page for the owner: the deadlines in the next 30 days with their owners, the open legal requests and how long
they have waited, the books' headline, the partner work waiting, the decisions needed, and the routing proposals.

Done when: the summary is published with its source for every date and figure, every deadline inside 30 days has an owner,
and the task is done with its headline.

1. Read where things stand: `hub task show <id>`, last week's summary, `knowledge/deadlines.md`.
2. Deadlines: every date in the next 30 days, its owner, and whether its inputs are ready. A date with no owner goes first.
3. Legal: open requests, the oldest five, and who holds each.
4. Books: the headline from the Finance Owner's latest report, as reported. Name any figure that was not available.
5. Partners: deals and registrations waiting on the company, each with its owner.
6. Decisions: at most three, each one line with the date, the deadline and the options, asked with `hub task question`.
7. Write `reports/YYYY-MM-DD-corporate-summary.md`, publish it with `hub file publish`, and finish the task with the headline,
   the path and what you could not read.

Write only what the reports and tasks say. A date or figure you could not read is "not known", never a guess.
