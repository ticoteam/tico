A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme engineering summary, week of Mon 2026-09-21

Sample output for Acme, a fictional studio-software team. Every pull request and name is invented.
Nothing was changed on GitHub and nothing has been shared.

**Headline: 14 changes shipped, 1 release, no incidents; 3 pull requests are stuck in review, all in billing.**

## Needs you
- **Billing: 3 pull requests waiting for review more than 2 working days** (#412, #418, #421). Owner from
  areas.md: the billing owner. Only one reviewer is named for billing. Proposed: a second named reviewer.

## Shipped
- 14 pull requests merged across web-app and api (last week: 11). Release 2.14.0 on 2026-09-24. Source: gh, 2026-09-28.
- Biggest for users: waitlist reminders by text (#398), the calendar reset link (#405).

## Stuck or waiting
| PR | Age (working days) | Waiting on | Owner |
|---|---|---|---|
| #412 invoice rounding | 4 | first review | the billing owner |
| #418 refund webhook | 3 | first review | the billing owner |
| #421 plan upgrade copy | 2 | checks failing | the web-app owner |

## Measures (by repository, not by person)
- Review wait: median 0.8 days, longest 4 (billing).
- Lead time, first commit to release: median 3.1 days for the 14 changes.
- Failed deploys: 0 of 3. Time to restore: not measurable, no failures.

## Team reports
- QA Engineer: 22 new issues, 5 need a human (digest 2026-09-28). Release Manager: notes for 2.14.0 ready on
  2026-09-24, unreviewed. Technical Writer: 2 pages drifted. Senior Software Engineer and Site Reliability
  Engineer: not running.

## Hiring proposal
- Dependabot opened 11 security alerts in api since 2026-09-01 and 7 are older than 14 days; nobody owns
  them. Proposed: a Security Engineer (`security-engineer`), first routine "Weekly dependency and advisory
  report", Mondays 08:00, reporting to me. When the work asks for this hire, I will ask BotOps to set it up.

## Proposed routing (nothing assigned)
- "Add SMS opt-out to reminders" (task T-311, no owner): route to the reminders owner. Alternative: the API owner.

## Could not read
- The mobile repository: not in this bot's GitHub list, so its merges are missing.

## Sources
- gh pr list, web-app and api, 2026-09-28; knowledge/areas.md, knowledge/measures.md, 2026-09-28
```
