---
name: triage-a-flaky-test
description: Decide whether a failing automated check is a product bug, a test defect or environment noise, using reruns, the failure's history and the test's own assumptions, and record the flake with an owner instead of skipping it quietly.
---
# Triage a flaky test

When to use: when a check fails on one run and passes on a rerun, or when the same check fails on some commits and not others
without a code reason. Use it before a release is blamed, and before anyone marks a test as skipped.

Steps:
1. Record the failure: the test name, the run link, the commit, the error text and the time. Do not rerun before you record it.
2. Look at the history of that test over the last 30 runs or the last two weeks. Count failures, and note whether they
   cluster by branch, runner, time of day or a shared resource. A test that fails about one run in twenty is still a
   defect to fix, not a fact of life.
3. Rerun the same commit a small fixed number of times, for example three, to see a pattern, but only when the task asks for
   reruns and a granted Tool allows them. Otherwise propose the reruns on the task and mark the pattern as not yet tested.
   Record each result. A pass on rerun is not proof that the product is fine; it is proof that the failure is not stable.
4. Find the assumption the test makes that the product does not guarantee: a fixed sleep or timeout, an order of results
   that is not sorted, shared data between tests, the current time or time zone, a real network call, or a port or file
   name another test uses.
5. Decide the kind:
   - product bug: the same failure reproduces by hand with the same steps and data; file it as a bug (see
     `skills/check-a-bug-report-for-repro/SKILL.md`);
   - test defect: the assumption in step 4 is broken; propose the fix to the test's owner;
   - environment: the runner, a dependency or a service was down; name it and check it again.
6. If the cause is not known within the pass, propose a quarantine of the test. Quarantine it only when the task asks for it,
   a granted Tool allows it and the team's own rule allows it; the quarantine has an owner, an issue and a date to fix or
   delete. A test never goes quiet without that record.
7. Write the outcome in `knowledge/test-notes.md` (create it on first use): the test, the kind, the evidence, the owner and the date. Add a line to
   `knowledge/themes.md` when three or more flakes share a cause.

Checks before you finish:
- The failure was recorded before any rerun.
- The rerun count and each result are written down.
- The kind is one of product, test or environment, with the evidence for it.
- No test was skipped or deleted in this pass without an issue and an owner.
- A release decision that rests on a flaky check says so in the test plan.

Example (invented, for illustration): "checkout_total_test failed once on 2026-09-24 on the shared runner: expected 10.00, got
9.99. Three reruns on the same commit: pass, pass, fail. The test compares a float with sorting that changes per run. Kind:
test defect. Owner: the checkout team. Fix: compare in cents, sort the lines. Logged in test-notes."
