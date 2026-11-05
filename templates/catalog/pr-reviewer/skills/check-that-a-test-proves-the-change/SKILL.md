---
name: check-that-a-test-proves-the-change
description: Decide whether the tests in a pull request would fail if the change were wrong, by tracing each behaviour change to an assertion that depends on it, and say plainly when a test only checks the happy path.
---
# Check that a test proves the change

When to use: when a pull request changes behaviour and adds or edits tests, or when it changes behaviour and adds no test.
A green check only shows that the tests pass with the change in place. It does not show the tests would catch the change
going wrong.

Steps:
1. List each behaviour change in the diff in one line: "refund rounds per refund, not per line", "empty cart returns 404".
   Skip pure renames and formatting.
2. For each one, find the assertion that depends on it. Open the test and read what it asserts, not only its name. Note the
   file and line.
3. Ask whether that assertion would fail if the change were reverted. Trace it: the value the test checks would come out
   the same under the old code, or it would not. If the old code would pass, the test does not prove the change.
4. Check the edges the change is about: the empty input, the boundary value, the error path, the second call, the
   concurrent call, the value at the end of a list. A happy-path test does not prove an edge.
5. Where no test covers a behaviour change, say so as a question: "What test would fail on the old code?" Suggest the
   smallest one by name, with its input and its expected result.
6. Do not run code to settle it unless the team's rules allow it. A reading that cannot settle the question is a
   question, not a finding.

Checks before you finish:
- Each behaviour change has a named test, or a question.
- Each named test was read, and the reason it would fail on the old code is written in one clause.
- Snapshot or golden-file tests are checked for what they hide: a regenerated snapshot can hide a bug.
- Tests that were deleted, skipped or weakened in the diff are listed as blocking until explained.

Example (invented, for illustration): "Refund rounding change: `test_refund_total` checks a single line of 10.00, which the old
code also gets right. Question: what test fails on the old code? Suggest a case with three lines of 3.333, expected total
10.00."
