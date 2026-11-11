# Weekly pipeline report (example, invented numbers)

Line one: 9 tasks stopped this week and each has a decision; the one repeat is Review 2 going Late on large pull requests
(3 times), proposed to the Engineering Team Manager.

## Tasks per stage (read 2026-11-09 09:00 PT)
| Stage | Open | Late | Waiting on a person |
|---|---|---|---|
| On deck | 4 | 0 | 0 |
| Build | 3/3 | 1 | 0 |
| Review 2 | 2 | 1 | 0 |
| Fixes 2 | 1 | 0 | 1 (the Fixes 2 OK, since 2026-11-07) |

## Stopped this week
- "Refund flow", Build, Late at 1 day. The Builder was still committing: left with it, snoozed to 2026-11-10, the date it named.
- "Calibration export retries", Review 2, the owner couldn't move it three times: the reviewer could not read the pull
  request. Filed the access fix for BotOps.
- "Docs for saved carts", Fixes 2, pushed past with "docs only" by a person: the reason holds, no action.

## On ice
- "Carrier contract", snoozed with no end date by a bot in Ideas. Snoozed again to 2026-12-01, the vendor's renewal date.

## Repeats worth a proposal
- Review 2 Late three times this week, each on a pull request over 800 lines. Proposed to the Engineering Team Manager
  on a task: a longer expected time for Review 2, or a size limit in Build.

## Not read
- One pull request in a repository this bot cannot read.

## Sources
- tico task list --status open, read 2026-11-09 09:00 PT
- tico task list --member me, read 2026-11-09 09:00 PT
