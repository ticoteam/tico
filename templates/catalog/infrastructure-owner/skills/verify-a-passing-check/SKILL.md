---
name: "verify-a-passing-check"
description: "Confirm that a green check is a real pass for a specific commit: the right conclusion, the right head commit, tests that ran, and no skipped or ignored step counted as success."
---
# Verify a passing check

When to use: before you count a deploy as successful, before you report a merge check as green, or before you accept a
"pass" from a fix.

Steps:
1. Read the conclusion, not the colour. The run's `conclusion` can be `success`, `failure`, `cancelled`, `skipped`,
   `neutral` and others. Only `success` counts as a pass. GitHub's status-checks reference says a skipped job reports
   success and can satisfy a required check. Third-party reports disagree, so test your own branch-protection setup
   before you rely on it.
2. Check the commit. The run's head SHA must equal the commit you are reporting on. A pass on an older head does not
   cover a newer push.
3. Read the check's description or summary. A "pass" whose description says the step was skipped, ignored or canceled
   did not run the work.
4. Check that the tests ran. Find the line that reports the number of tests run, or the test step's output, and confirm
   the number is above zero and in line with earlier runs. A step that exits with 0 and runs no tests is a silent pass.
5. For a deploy, a green job is not enough. Read the version the running service reports (its commit, release or build
   identifier) and its health output. Record both, and name the commit they show.
6. Write the result as "pass verified at <sha>, by <evidence>", or "not verified: <reason>". Never write "green" alone.

Checks before you finish:
- The conclusion is `success`, and the head SHA matches the commit named in the report.
- The description does not say skipped, ignored or canceled.
- The test count is above zero, or the job has a named reason for running no tests.
- For a deploy, the running version or the health output is recorded.
- Any check you could not read is "not verified", never "pass".

Example (invented, for illustration): The deploy job for commit 4f2a9c shows green. Its description reads "build step
ignored", and the health check was never called. Result: not verified. The deploy did not run for this commit. The report
names the run, the commit and the reason, and does not count the deploy.
