---
name: prove-a-test-catches-the-bug
description: Show that a test really guards a behaviour by checking it fails on the broken code and passes on the fix, before calling it a regression test or covering a path.
---
# Prove a test catches the bug

When to use: before you call a test a regression test for a fix, before you say a must-test path is covered, and before you write a test for a fix you are asked to make.

Steps:
1. Name the behaviour the test is meant to guard, in one sentence a person could check: "a refund larger than the invoice is refused".
2. Read what the test asserts. An assertion on a value the behaviour produces is evidence. An assertion only that the code runs,
   that a mock was called, or that a page loaded is not evidence of the behaviour.
3. For a regression test for a fix: run it at the commit before the fix (it must fail there) and at the fix (it must pass there).
   If you cannot run both, say so, and mark the test "unproven" in the report.
4. For a path with no test: write a test that would fail if the path broke, and show it fails when the path is broken on purpose
   in a scratch branch. Do not merge the scratch change.
5. If the test is green on broken code, it does not guard the behaviour. Name it as such, and do not count the path as covered.

Checks before you finish:
- The behaviour is named in one sentence.
- Each claim of coverage names the assertion that proves it, with its file and line.
- A test that cannot fail is named "does not guard" in the report, not "covered".
- No test asserts the buggy behaviour as correct.
