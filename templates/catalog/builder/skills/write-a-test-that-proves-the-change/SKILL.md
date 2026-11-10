---
name: write-a-test-that-proves-the-change
description: Write the test for a task first, see it fail on the old code for the task's reason, then make it pass with the smallest change, and record both results.
---
# Write a test that proves the change

When to use: before any code change for a task, and at any fix where a comment or a failed test shows a
behaviour that no test covers.

Steps:
1. Write the behaviour in one line: "empty cart returns 404", "refund rounds once per refund".
2. Find the nearest existing test file and add a test that asserts that line. Name the test for the behaviour.
3. Run it against the old code, or with the change reverted. It must fail, for the reason the task names. A test that
   passes on the old code proves nothing; fix the test first.
4. Make the change. Run the test again. It must pass.
5. Add the edge the change is about: the empty input, the boundary value, the error path, the second call.
6. Record on the task: the test name, that it failed before and passed after, and the head commit.

Checks before you finish:
- The test fails on the old code for the stated reason.
- No test was deleted, skipped or weakened to get green. If one was changed, the reason is on the task.
- A snapshot was regenerated only for an intended change, and its diff was read line by line.

Example (invented, for illustration): "Refund rounding: add `test_refund_rounds_once` with three lines of 3.333 and an
expected total of 10.00. On the old code it returns 9.99; after the change it returns 10.00."
