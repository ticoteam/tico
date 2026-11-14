A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme engineering summary, week of 2026-09-28

Six tasks reached Released and three stopped, each decided. One decision waits on a person: the QA OK on "Saved carts".

## Pipeline (read 2026-10-05 09:00 PT)
| Stage | Open | Late | Waiting on a person |
|---|---|---|---|
| On deck | 4 | 0 | 0 |
| Build | 3/3 | 1 | 0 |
| Review 2 | 2 | 1 | 0 |
| QA | 1 | 0 | 1 (the QA OK, since 2026-09-29) |

- Stopped: "Refund flow", Build, Late at 1 day. Its feature owner was still committing: left with it, snoozed to 2026-10-02,
  the date it named.
- Stopped: "Calibration export retries", Review 2, the owner couldn't move it three times: the reviewer could not read
  the pull request. Filed the access fix for BotOps.
- Stopped: "Docs for saved carts", QA, pushed past with "docs only" by a person: the reason holds, no action.
- On ice: none.
- Open longest: "Calibration export retries" pull request, 6 days, review requested; "Refund flow", 4 days, checks failing.
- Repeats worth a proposal: Review 2 Late three times this week, each on a pull request over 800 lines. Proposed on a
  task to the person above: a longer expected time for Review 2, or a size limit in Build.
- Hire proposal: none.
- Not read: one pull request in a repository this bot cannot read.
```
