A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme support summary, week of Mon 2026-09-21

Sample output for Acme, a fictional team. Every figure, ticket and name below is invented.

**Headline: replies were on target (median first reply 2h10m against a 4 business hour target), but the backlog aged: 6 tickets are older than 7 days and 2 have no owner.**

## Decisions needed
1. **Who owns T-2011 and T-2019?** Both are 9 days old with no owner since the billing owner went on leave on 2026-09-16.
   Proposed: the named cover in `knowledge/team.md` takes both. Requested routing uses a task with this evidence.
2. **Weekend cover on 2026-10-10 and 2026-10-11**: nobody is listed in the rota. Proposed: the on-call owner names someone.

## Against target
| Measure | This week | Last week | Target |
|---|---|---|---|
| New requests | 74 | 61 | none |
| First reply, median / slowest tenth | 2h10m / 7h40m | 2h30m / 6h05m | 4 business hours |
| Time to resolution, median | 1.4 days | 1.1 days | 3 days |
| Open, at end of week | 31 | 22 | none |

Source: queue digests 2026-09-21 to 2026-09-27 and the support mailbox count on 2026-09-28. Times run from the
customer's first email. Counts: first reply 58 tickets, resolution 44.

## Backlog by age (31 open)
0 to 2 days: 14. 3 to 7 days: 11. 8 to 14 days: 4. 15 days or more: 2.

## Oldest waiting
T-2011 (9 days, no owner, waiting on us), T-2019 (9 days, no owner), T-2024 (8 days, waiting on the customer), T-2027
(7 days, waiting on a fix), T-2030 (7 days, waiting on us).

## Repeats
Calendar sync lag (4 tickets, already a product issue), invoice date questions (5, the docs cover it), SMS
reminder plans (3, the docs do not say: task sent to the Librarian).

## Quality and customers
QA review 2026-09-25: 10 replies sampled, 8 met the scorecard; the two misses lacked a next step. Feedback report
2026-09-22: "double booking" is the largest theme (9 mentions).

## Routing proposals (nothing created)
- T-2027 to the head of engineering: waiting on the sync fix, needs a date for the customer.
- T-2011 and T-2019 (sync errors with logs attached) to `technical-support` to reproduce.

## Hiring proposal (nothing requested)
- **Add a Technical Support Engineer (`technical-support`).** For three weeks, 6 to 9 tickets a week needed
  someone to reproduce an integration problem, and 5 of the 7 oldest tickets are of that kind (queue digests
  2026-09-07 to 2026-09-27). First routine: the weekly tier 2 queue report, Thursdays 09:00. Reports to
  support-lead. When the work asks for this hire, I will ask BotOps to set it up.

## Could not read
The Slack support channel is not connected, so questions asked there are not counted.
```
