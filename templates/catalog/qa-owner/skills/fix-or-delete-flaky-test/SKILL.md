---
name: fix-or-delete-flaky-test
description: Decide, the same day, whether a flaky or low-value test is fixed at its root cause or deleted with a recorded reason, and record the decision in the flaky test register with its owner. A flaky test is never quarantined, skipped, retried or given a longer wait.
metadata:
  function: engineering
---
# Fix or delete a flaky test

When to use: when a test is in the flaky test register with no decision, when a flaky test has blocked merges, or when a test proves too little to be worth its time.

No test is better than a flaky one. A flaky test gets one of two decisions, the day it is found:

Decision rules:
- **Fix it at its root cause today** when the cause can be named from the log and the code (a clock the test did not freeze,
  a wait on time instead of a condition, a shared temp directory or port, state one test leaves for the next, a real network
  call). The fix removes the cause, so the test passes every time. It is a branch and a pull request, written when the task
  asks for it and a granted Tool allows it. Otherwise it is a fix plan for the owner, due today.
- **Delete it** when it cannot be made deterministic today and it adds little: it would still pass if the behaviour it names
  broke, repeats another test, or checks the code against itself. Fewer strong tests beat many weak ones. Say why, here and
  in the commit that deletes it, and name what still covers the behaviour, if anything. The deletion is a branch and a pull
  request when the task asks for it and a granted Tool allows it.
- **A test that guards a must-test path** (`knowledge/thresholds.md`) and cannot be fixed today is still not set aside: write
  a deterministic test of the same behaviour, then delete the flaky one. Until that is done the path is named as uncovered in
  the report, and the owner decides today.
- **Never quarantine, skip, mark xfail, rerun, add a retry, widen a timeout or lower an assertion.** Each one hides the flake;
  none of them is a fix.

Recording the decision, in `knowledge/flaky-tests.md`:
- The test, the suite and the repository.
- The evidence: the flips with run ids, commits and dates.
- The root cause and its class (from `skills/classify-a-test-failure/SKILL.md`).
- The decision: fix or delete, with the owner and today's date.
- For a fix: what the cause was and how the fix removes it.
- For a deletion: why the test could not be made deterministic or was low value, and what still covers the behaviour, if anything.

Checks before you finish:
- Every flaky test in the register has a decision, an owner and a date, and none is older than a day without one.
- A decision names the evidence it rests on; "it feels flaky" is not a decision.
- No decision was carried out by this bot unless the task asked for it and a granted Tool allowed it.
