---
name: decide-fix-or-quarantine
description: Decide whether a flaky or low-value test is fixed this week, deleted with a recorded reason, or quarantined with a ticket and a deadline, and record the decision in the flaky test register with its owner.
---
# Decide fix or quarantine

When to use: when a test is in the flaky test register with no decision, or its deadline has passed, when a flaky test has blocked merges, or when a test proves too little to be worth its time.

Decision rules:
- **Fix this week** when the suspected cause is known from the log, the fix is small (a frozen clock, a wait for a condition, an
  isolated temp directory, a fixture reset), and an owner can make it. The fix is a branch and a pull request, written when the
  task asks for it and a granted Tool allows it. Otherwise it is a fix plan for the owner.
- **Delete** a test that flips and has no small fix, or a test of low value: one that would still pass if the behaviour it names
  broke, repeats another test, or checks the code against itself. No test is better than a flaky one, and fewer strong tests
  beat many weak ones. The reason is recorded here and in the commit that deletes it. The deletion is a branch and a pull request
  when the task asks for it and a granted Tool allows it.
- **Quarantine with a ticket and a deadline** only when the test guards something that matters, the cause is not known, and
  deleting it would leave that path uncovered. A quarantine is recorded here; a person skips the test unless the task asks for it
  and a granted Tool allows it. The default deadline is 14 days, and the ticket names the owner.
- **Never quarantine or delete a must-test path's test** (`knowledge/thresholds.md`) without a second test that covers the same
  behaviour. If there is none, the path is named as uncovered in the report, and the decision is the owner's.
- **Never widen a timeout, add a retry or lower an assertion** as a fix without writing down the cause. A retry is a symptom
  relief, and it is recorded as one.

Recording the decision, in `knowledge/flaky-tests.md`:
- The test, the suite and the repository.
- The evidence: the flips with run ids, commits and dates.
- The suspected cause and its class (from `skills/classify-a-test-failure/SKILL.md`).
- The decision: fix, delete or quarantine, with the owner and the deadline.
- For a deletion: why the test was low value or could not be fixed, and what still covers the behaviour, if anything.
- For a quarantine: the ticket, and what would make the test safe to restore.

Checks before you finish:
- Every flaky test in the register has a decision, an owner and a date.
- A decision names the evidence it rests on; "it feels flaky" is not a decision.
- No decision was carried out by this bot unless the task asked for it and a granted Tool allowed it.
