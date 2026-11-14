---
name: classify-a-test-failure
description: Classify one failing test as a product bug, a flaky test, a test defect, an environment fault or a dependency change, with quoted evidence, before anyone blames a commit or a test.
---
# Classify a test failure

When to use: when a test is red and someone asks why, when you add a failure to the flaky test register, or before you hand a product bug to its feature owner.

Steps:
1. Read the failed log only: `gh run view <id> -R <repo> --log-failed`. If the log is gone, the class is "unknown" and you stop.
   Do not guess from the test name.
2. Find the first failing assertion, not the last. Later failures often follow from an earlier one that left state behind.
3. Match the failure to one class:
   - product bug: the same assertion fails on every run of the same commit, and the failing line checks behaviour the code changed.
   - flaky test: the same test failed and passed on the same commit, or the error is a timeout, a port already in use, a clock
     that moved, or state that depends on test order.
   - test defect: the test asserts something the product should not do, uses a fixture that changed, or was written against an
     implementation detail that moved. The product is right and the test is wrong.
   - environment: the runner was lost, the network reset, a service was down, a rate limit was hit or a credential expired.
   - dependency: a package changed upstream, a lockfile moved, or a yanked version was installed.
4. Check the history: whether the same commit passed on another run. A pass on the same commit makes "product bug" unlikely.
5. Quote the three lines that show the class, with any credential, token or private path redacted. Say which lines you cut.
6. Write the class, the evidence, the first failing run and commit, and a confidence. Use high when the history and the log agree.
   Use low when only one of them can be read.

Checks before you finish:
- The class is one of the five, or "unknown" with the reason.
- The first failing assertion is named, and the quoted lines come from that step.
- A flaky call names the runs in which the test flipped.
- No credential or customer detail appears in the quote.
- The cause is named as the change, the test or the environment, never as a person.

Example (invented, for illustration): Run 88140 failed in the `test-api` job, at the step "Run integration tests", in
`test_reminder_sends_at_7am`. The log shows the reminder was not sent when the clock read 23:30 UTC. The same commit passed in runs
88131 and 88150, and the test reads the wall clock. Class: flaky test, timing (the test depends on the time of day). Confidence:
high, because the history and the log agree. Hand-off: the decision in `skills/decide-fix-or-quarantine/SKILL.md`, with a
proposed frozen clock.
