---
name: decide-fix-or-quarantine
description: Decide whether a flaky test is fixed this week or quarantined with a ticket and a deadline, and record the decision in the flaky test register with its owner.
---
# Decide fix or quarantine

When to use: when a test is in the flaky test register with no decision, or its deadline has passed, or when a flaky test has blocked merges.

Decision rules:
- **Fix this week** when the suspected cause is known from the log, the fix is small (a frozen clock, a wait for a condition, an
  isolated temp directory, a fixture reset), and an owner can make it. The fix is a branch and a pull request, written when the
  task asks for it and a granted Tool allows it. Otherwise it is a fix plan for the owner.
- **Quarantine with a ticket and a deadline** when the cause is not known, or the fix is more than a week's work. A quarantine
  is recorded here; a person skips the test unless the task asks for it and a granted Tool allows it. The default deadline is 14
  days, and the ticket names the owner.
- **Never quarantine a must-test path** (`knowledge/thresholds.md`) without a second test that covers the same behaviour. If
  there is none, the path is named as uncovered in the report, and the decision is the owner's.
- **Never delete a test** as a decision. Deletion is a separate decision with its own reason, and it is never made by this bot.
- **Never widen a timeout, add a retry or lower an assertion** as a fix without writing down the cause. A retry is a symptom
  relief, and it is recorded as one.

Recording the decision, in `knowledge/flaky-tests.md`:
- The test, the suite and the repository.
- The evidence: the flips with run ids, commits and dates.
- The suspected cause and its class (from `skills/classify-a-test-failure/SKILL.md`).
- The decision: fix or quarantine, with the owner and the deadline.
- For a quarantine: the ticket, and what would make the test safe to restore.

Checks before you finish:
- Every flaky test in the register has a decision, an owner and a date.
- A decision names the evidence it rests on; "it feels flaky" is not a decision.
- No decision was carried out by this bot unless the task asked for it and a granted Tool allowed it.
