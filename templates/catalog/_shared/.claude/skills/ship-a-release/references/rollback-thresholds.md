# Rollout thresholds

Fill this table in before the first stage of every rollout and put it on the release task. The point of writing the
numbers first is that the decision is then a lookup, not a debate.

## The table

Starting values. A team replaces them with its own in `knowledge/engineering.md` (or `knowledge/thresholds.md` if the
role already keeps one) once it has a few releases of history.

| Signal | Baseline (measured) | Advance | Hold and investigate | Roll back |
| --- | --- | --- | --- | --- |
| Error rate (failed requests out of all requests) | fill in | within 10% of baseline | 10% to 100% above baseline | more than 2 times baseline |
| p95 response time | fill in | within 20% of baseline | 20% to 50% above baseline | more than 50% above baseline |
| New kinds of error (in server logs or the browser console) | list of known ones | none new | a new kind, seen at low volume, not on a key flow | a new kind on a key flow |
| Key pages and flows | all load and work | all load and work | one is slower than twice its baseline load time | one fails to load or a key flow cannot be finished |
| Install health | healthy, old version | healthy, new version | still updating past the usual time | failed, rolled back by the updater, or uncertain |
| Data checks (row counts, sums, queue depth that should not move) | fill in | unchanged | moved, cause not yet known | wrong data written |
| Customer reports | usual volume | usual volume | one report that matches the change | several reports that match the change |

"p95 response time" means 95 out of 100 requests were at least this fast.

A signal counts only when it shows in two checks in a row. One bad reading is noise until the next check agrees.
Any data being written wrongly, or a security problem, is a roll back at the first sighting.

Source of the starting values for error rate and p95: the rollout threshold table in addyosmani agent-skills,
`shipping-and-launch`, as read on 2026-11-18. They are a reasonable first setting, not a law.

## Also write down

```
Release:                 vX.Y.Z at <full SHA>
Can roll back:           yes | no, because ... | unknown, because ...
Roll back to:            vX.Y.W at <full SHA>
How:                     <the exact steps or command>
How long it takes:       <measured last time, or "not yet measured">
Stages:                  <audience 1> -> <audience 2> -> ...
Soak per stage:          <minutes>, one check every <seconds>
Where the numbers come from:  <monitoring Tool | logs | key-page checks>
Who decides a hold that stays unclear:   <the Release Owner by default>
```

## Measuring the baseline

1. Take the "before" readings in the minutes before the release, and screenshot each key page.
2. Compare with the same hours on the same weekdays over the last four weeks, so a normal Monday peak is not read
   as a regression.
3. Check the request rate first. If traffic is more than 10% different from the comparison period, the comparison
   is weak: say so, lean on the key-page checks and extend the soak.
4. With very low traffic a rate is meaningless (one failure out of four requests is "25%"). Use counts: "no more
   than <n> failed requests in the soak" and rely on walking the key flows yourself.

## Watching for change, not for perfection

- The baseline is allowed to be imperfect. Three known errors before and three after is green.
- Compare each stage with its own "before", not with an ideal.
- Every signal gets a screenshot or a log line saved to the release task, with the time.
- Longer-running effects (nightly jobs, billing runs, emails that go out hours later) need a second look after they
  have run. Put a follow-up on the release task with `tico task snooze <id> --until <date> -m "check <job> ran clean
  on vX.Y.Z" --version N` if the check cannot happen now.

## A hold

A hold has a time limit: one more soak period by default. In that time, find out whether the change caused the
signal. Explained and harmless: advance and note why. Explained and harmful, or still unexplained when the time is
up: roll back. A hold never turns into "keep an eye on it" at the next stage.

## After a rollout

Add to the release log: the baseline, the worst reading at each stage and the decision. If a threshold fired when
nothing was wrong, or stayed quiet when something was, change the table for next time and record why.
