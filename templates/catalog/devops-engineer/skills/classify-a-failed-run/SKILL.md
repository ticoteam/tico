---
name: classify-a-failed-run
description: Classify one failed CI run as a product bug, a flaky test, an infrastructure fault, a dependency change or a configuration change, with quoted evidence, before anyone blames a commit.
---
# Classify a failed run

When to use: when a CI run is red and someone asks why, or when you add a failure to a report or to the flaky test register.

Steps:
1. Read the failed log only: `gh run view <id> -R <repo> --log-failed`. If the log is gone, the class is "unknown" and you
   stop. Do not guess from the job name.
2. Find the first failing step, not the last. Later steps often fail because an earlier one left state behind.
3. Match the failure to one class:
   - product bug: the same assertion fails on every run of the same commit, and the failing line touches changed code.
   - flaky test: the same test failed and passed on the same commit, or the error is a timeout, a port already in use,
     or state that depends on test order.
   - infrastructure: the runner was lost, the network reset, a rate limit was hit, a service was down, or a credential expired.
   - dependency: a package changed upstream, a lockfile moved, or a yanked version was installed.
   - configuration: a workflow file, a variable or a branch rule changed, and the failure starts at that change.
4. Check the history: the register in `knowledge/flaky-tests.md`, and whether the same commit passed on another run.
   A pass on the same commit makes "product bug" unlikely.
5. Quote the three lines that show the class, with any credential, token or private path redacted. Say which lines you cut.
6. Write the class, the evidence, the first failing run and commit, and a confidence. Use high when the history and the log
   agree. Use low when only one of them can be read.

Checks before you finish:
- The class is one of the five, or "unknown" with the reason.
- The first failing step is named, and the quoted lines come from that step.
- A flaky call names the runs in which the test flipped.
- No credential or customer detail appears in the quote.
- The cause is named as the change or the test, never as a person.

Example (invented, for illustration): Run 88140 failed in the `test-api` job, at the step "Run integration tests". The log
shows a connection refused on the database port in one test. The same commit passed in runs 88131 and 88150. Class: flaky
test, timing (the database container was not ready). Confidence: medium, because the container logs are past retention.
Owner: the platform owner in `knowledge/pipeline.md`, with a proposed wait-for-ready step.
