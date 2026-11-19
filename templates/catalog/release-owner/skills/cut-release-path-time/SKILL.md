---
name: cut-release-path-time
description: Make a release pipeline faster without weakening it. Lay out one real release as a timeline (validated, published, canary, stable), find the largest segment, and cut waits before cutting work - waiting for the next event, slow planning scans, steps that no longer earn their time, hand-offs that could overlap, promotion that could run in the same step, and fail-fast where it is safe. Then re-check the reads and failure reports that the old waits used to hide, and measure the first real releases against the baseline. Use it when asked to speed up releases, shorten merge to production time, or find out why a release takes so long. Not for measuring medians for a report - use time-the-merge-to-release-path for that.
metadata:
  function: engineering
---

# Cut release path time

Make a release reach users sooner by removing waits, not by removing gates. Most of the time lost on a release path is waiting, not testing. Measure first, cut one thing at a time, then measure again.

## When to use

- Someone asks to make releases faster, or asks why a release takes so long.
- A release path has steps such as: validate a candidate, publish, update a canary, promote to stable or production.
- You are about to add machines or spend to speed up releases. Do this first.

Do not use it to report a delivery time. For medians over a window, use the skill `time-the-merge-to-release-path`.

## Words used here

- Segment: the time between two recorded events on one release, such as "validation end to published".
- Critical path: the chain of steps that decides the total time. Speeding up anything off it changes nothing.
- Canary: the first install that gets a release, before everyone else.
- Stable: the install or channel everyone else uses. Some call it production.
- Step: one run of the automation that moves a release forward. Many pipelines run one step per event (a push, a finished run, a timer).
- Incremental validation: a run of only the tests the change selects, measured from an anchor.
- Anchor: the last validated commit that the next incremental run compares with.
- Fail-fast: cancel the remaining test jobs as soon as one fails.
- Single-shot read: a request made once, with no retry.

## Steps

### 1. Lay out one real release as a timeline

Take one recent, ordinary release. Read the timestamps from the records, not from memory. Write one line per event:

- validation started, validation ended
- tagged, published
- canary updated, canary healthy
- marked latest
- next validation started
- stable updated

Work out each segment. Name the largest. Note what each segment was doing: running work, or waiting for something to start.

This is your baseline. Keep the method, because you will repeat it at the end.

### 2. Look for waits before you look at work

For each segment, ask: was the pipeline working, or waiting for its next event?

A step that runs only when the next event arrives can sit idle for minutes. If promotion waits for the next push or the next timer, that wait is the cost, and no faster test will remove it.

In one case: the largest segment was not the tests. Promotion to stable ran only on the next event, which took 3 to 16 minutes to arrive.

### 3. Profile the planner and other glue

Time the small steps that decide what to do next. Run them read-only against live data and time each part.

Look for a check that reads a lot and applies to only some of what it reads. Filter first, then read.

In one case: one scan took 31.6 of 36.6 seconds of planning. It read the jobs and logs of 12 runs to enforce a daily limit that applied only to full runs. Skipping incremental runs in that scan saved about 30 seconds per plan, and two jobs each planned, so 60 to 90 seconds per hand-off.

### 4. Remove work that no longer earns its time

List each step on the critical path with its cost. For each, ask what it protects against, and whether something else already protects against it.

If another check already covers it, delete the step. Do not make it conditional. A conditional step is still code to keep.

Check again after other speed-ups. A step that was harmless can become the critical path once the work around it gets faster.

In one case: a "free disk space" step cost 47 to 143 seconds per validation. The machines had about 85 GB free, and a later check already cleaned up below 35 GB. The step was deleted.

### 5. Overlap the hand-offs

For each thing that starts late, ask what it truly needs before it can start. Start it as soon as that exists.

In one case: the next validation needed only the published release to compare with. Starting it right after publish, before the canary update, the health wait and the latest mark, saved about a minute per release.

### 6. Promote in the same step

When the canary runs the release healthy and the release is approved, promote to stable in that same step. Do not wait for the next event.

Keep these as they are:

- The approval rules. A release that needs a person still waits for a person.
- The rollback rule. A canary that rolled back must never reach stable in that step. Write a test for this.

Soak time (how long a release sits on the canary before going wider) is the owner's decision. Ask. Record the answer in the change. Do not shorten it or remove it silently.

### 7. Fail fast only where it is safe

Turn fail-fast on for incremental runs. Leave full runs to finish, since their job is to give the whole picture.

A red run that fail-fast cut short needs three rules:

- It still names the parts that really failed. Treat those as you treat any red.
- It must never become an anchor. Its cancelled parts never ran, so it proves nothing about them.
- Tell a fail-fast cancel from a hang. Some CI systems report a job that hit its own time limit as "cancelled" too. Use duration: a job cancelled well under the limit was cut short, and a job cancelled at the limit hung. A hang is a failure with no name. Report it as one.

Estimate the saving before you build it: on past red runs, measure from the first failure to the end of the run.

Write down side effects you accept. In one case: a reclaimed spot machine now cancelled its sibling jobs too. Manual reruns were rare (2 in 100 runs), so this was accepted and documented.

### 8. Re-check what the old waits used to hide

A faster loop makes more requests, sooner after each change. Find every single-shot read the pipeline cannot continue without.

- Retry those reads a few times with a short pause.
- Make the failure say why: the status code, or "no answer" when there is none, and the number of tries.
- Leave polling during an update alone if a missing answer there already means "restarting".
- Check what one failed step triggers. A single missed read should not reopen an incident or page someone when the next step would succeed.

In one case: after continuous promotion, 4 of 11 steps in an hour failed on one unanswered status read while the service was up. Three tries, 20 seconds apart, fixed it.

### 9. Ship each cut as its own change

One cut per change, so each can be reviewed and reverted alone. Each change carries a test that pins the new behaviour. Examples:

- "The next validation starts before the canary update."
- "A cut-short red names the failed parts, not the cancelled ones."
- "The limit scan never reads an incremental run's jobs."
- "A canary rollback never reaches stable in the same step."

Run test suites under a time limit. A test that replaces a hook a polling loop depends on can hang forever once sleep is stubbed. Chain the original hook instead of replacing it.

Get one independent review of the whole set. In one case the review caught the time limit reported as "cancelled".

### 10. Measure the first real releases

After merge, lay out the first releases on the new flow with the same timeline method as step 1. Compare segment by segment with the baseline.

Watch the next several releases for new failures caused by the faster loop. Then name the largest remaining segment, so the next person knows where to start.

## Do not

- Do not add machines or spend without numbers that show machines are the limit.
- Do not remove or loosen an approval rule to gain time.
- Do not change soak time or risk rules without the owner saying so.
- Do not quote a saving you did not measure. Say "estimated" when it is.

## Checks before you finish

- A baseline timeline from one real release exists, with each segment timed.
- Each cut names the segment it shortens and the measured or estimated saving.
- Approval rules are unchanged, and the change says so.
- Any choice about soak time or accepted side effects names who decided.
- A cut-short red cannot become an anchor, and a hang is not reported as a cancel.
- Reads the pipeline cannot continue without are retried, and failures state the cause and the number of tries.
- Each cut is its own change with a test that pins the new order or rule.
- Suites ran under a time limit.
- An after timeline from a real release on the new flow is compared with the baseline, and the largest remaining segment is named.

## Example

Task: "Releases take too long. Make them faster."

Baseline, one release, from the records:

| Segment | Time |
| --- | --- |
| Validation start to end | 14 min |
| Validation end to published | 4.5 min |
| Published to stable | 16.4 min |
| Total | about 35 min |

Finding: published to stable is the largest segment, and most of it is waiting for the next event.

Cuts, each its own change with a test:

1. Promote to stable in the same step once the canary is healthy and the release is approved. Owner chose no soak; recorded in the change.
2. Start the next validation right after publish.
3. Filter the daily-limit scan to full runs before reading jobs.
4. Delete the disk clean-up step that a later check already covers.
5. Fail-fast on incremental runs, with the three rules in step 7.
6. Retry the status reads a step cannot continue without.

After, first release fully on the new flow:

| Segment | Before | After |
| --- | --- | --- |
| Validation start to end | 14 min | 11.5 min |
| Validation end to published | 4.5 min | 3.4 min |
| Published to stable | 16.4 min | 3.7 min |
| Total | about 35 min | 18.6 min |

Report: "Validation start to stable went from about 35 min to 18.6 min on one release each. Approval rules unchanged. No canary soak, by the owner's choice. Largest remaining segment: validation end to published, 3.4 min."

These are one release each, not medians. For a figure to put in a report, measure a window with `time-the-merge-to-release-path`.
