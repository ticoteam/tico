---
name: test-first
description: Write the test before the code. State one behaviour in the caller's words, write one test for it, run it and see it fail for the right reason, write the least code that makes it pass, then prove the test would catch the bug by breaking the behaviour on purpose. Covers new features, bug fixes (the test fails on the old commit), flaky tests, and what to do when no good test can be written. Use when a task says build, implement, add, fix, bug, regression, write a test, add coverage, TDD, red green, flaky test, or prove this test catches the bug, and whenever you are about to change code that ships. Do not use it for throwaway prototypes that are never merged.
metadata:
  function: engineering
  roles: [feature-owner, builder, qa-owner]
  actions: [read, draft]
  review: none
---
# Test first

When to use: you are about to write or change code that will be merged.

Words used here: **red** means a test that fails; **green** means it passes. A **seam** is a place where a test can
call the code from outside, the way a real caller does: an HTTP route, a command, a public function. A **double** is a
fake stand-in for something real (a mock, a stub). A **flaky test** passes and fails on the same code.

Tico words: a **task** is one piece of work with an owner. A **pipeline** moves a task through **stages**. A **page**
is an HTML file a bot publishes onto a task for people to read. An **OK** is a named person's approval, requested
with an ask. Commands that change a task take `--version N`, the number that `tico task show <id>` prints.

## What great looks like

Each test names one behaviour a user or a caller would notice. It fails when that behaviour breaks and keeps passing
when only the insides of the code change. The bot saw it fail before the code existed, and the failure message said
the behaviour was missing.

Expected values are written out by hand, never computed the way the code computes them. A bug fix comes with a test
that fails on the old commit, and the PR names that commit. There are few tests and each one earns its place: no test
is better than a test that passes whatever the code does, or one that fails at random.

## First time at a company

Read `knowledge/engineering.md` in your own repository. If it has a "Tests" section, skip this. If not, ask the owner
these in one numbered message and save the answers under "Tests" with the date. Work on the defaults meanwhile.

1. What is the command to run one test file, and where does the whole suite run? Default: read the repo's README,
   its agent instruction file (`AGENTS.md` or `CLAUDE.md`) and its CI config. Run single test files on your
   Computer (where Tico runs you); run the whole suite only in CI.
2. What counts as outside our code, so a test may use a double for it? Default: other companies' services over the
   network, the clock, randomness and payment providers. Everything else is real, including our own database.
3. Is there a coverage rule? Default: whatever CI already enforces. Coverage shows that lines ran, not that any test
   checks them, so it is never the goal.
4. What happens to a flaky test? Default: fixed at the cause the same day or deleted. Never retried, skipped or
   parked.
5. Which parts have no cheap way to test today? Default: none known. Record each one you find in the "Tests"
   section so the next plan can fix the seam.

## Steps

1. **State the behaviour.** One sentence in the caller's words: "When a customer pauses a subscription, the next
   renewal does not charge them." Take it from the acceptance criteria or the bug report. One behaviour per test.
2. **Find the seam.** Pick the highest public entry point where the behaviour shows and the test still runs fast: the
   API route, the command, the public function. Test through it, not through private helpers. If no such seam
   exists, that is a finding in its own right: say so in the task, and use the closest thing that runs real code (a
   small script that calls the real function and exits non-zero on the wrong answer). Do not write a bad test to
   fill the gap. A bad test is one that checks a double, copies the code's own logic, depends on timing, or needs
   heavy setup.
3. **Write one test.** Name it after the behaviour. Work the expected value out by hand and write it as a literal
   (`assert total == 1250`, not `assert total == price * qty + tax(price)`). Use doubles only for the things listed
   as outside in `knowledge/engineering.md`.
4. **Run it and read the red.** The test must fail, and for the right reason: the assertion says the behaviour is
   missing. A failure from a typo, a missing import or a setup error is not the red you need; fix it and run again.
   Keep the failing line.
5. **Write the least code that passes.** Run the test: green. Then run the neighbouring tests the way the repo says,
   and let CI run the whole suite. Report any failure by name, including ones you believe are not yours.
6. **Prove the test bites.** Make two edits, one after the other, then undo each:
   - Break the behaviour: flip the condition you just added, or return the old value. The test must go red.
   - Change only the insides: rename a private helper or reorder internal steps. The test must stay green.
   If the first edit leaves it green or the second turns it red, the test is wrong. Rewrite it.
7. **Tidy, then take the next behaviour.** Clean up the code while the test is green. Then repeat from step 1: one
   test, then its code. Never write all the tests first and all the code after; tests written in bulk describe the
   design you imagined, not the one that works. Give the edges the change is about their own tests: the empty
   input, the boundary value, the error path, the second call.
8. **For a bug fix, land the red first.** Commit the failing test before the fix, so the history shows red then
   green. Run the test on the commit before your fix and record that commit's SHA and the failing line. Put both in
   the PR.
9. **If code was written before its test,** do not keep it and test around it. Take the code out, write the test,
   see it fail without the code, then put the code back.
10. **Handle a flaky test the same day.** A test that failed and then passed on a rerun has failed. Find the cause
    (shared state, time, ordering, a real race in the product) and fix it, or delete the test and say what is now
    unprotected. A flake fix is proven by repeated runs: Tico's own rule is 20 passes out of 20. If your role has the
    skill `fix-or-delete-flaky-test`, use it for this step and log the test in `knowledge/flaky-tests.md`.
11. **Report with evidence from this session.** In the PR's "How it was checked" section and as a task comment:
    `Ran: <command>, <where it ran and run id>: <n> passed, 0 failed, 0 skipped. Red before: <failing line> at <sha>.
    Not run: <what>. Not proven: <what>.`

## Gates

- No red seen, no green claimed. If you did not watch the test fail, you do not know what it checks.
- A pass on retry is a failure.
- Zero tests run is never a pass. Read the count, not the colour.
- Never make a check pass by weakening it: no loosened assertion, deleted test, new skip marker, raised timeout, or
  edit to CI or lint config. If one of these is truly right, it goes in the PR under its own heading "Gate edits"
  with the reason on the changed line.
- A double for something inside our own code is removed. Test the real thing.
- A snapshot (a saved copy of expected output) is regenerated only for an intended change, and its diff is read line
  by line first. A regenerated snapshot can hide a bug.
- No test asserts the buggy behaviour as correct.
- An expected value the test computes the same way the code does proves nothing. Replace it with a literal.
- A change with no test and no stated reason does not go to Review. The reason must be the missing seam from step 2,
  with the closest executable check attached.

## Autonomy

Alone: everything in the steps, including deleting a flaky test, adding a seam, and refusing to hand a change to
Review without proof.

Needs an OK: nothing by default. One thing must be visible instead of asked: any test you deleted or weakened is
listed under "Gate edits" in the PR so the Reviewer sees it without hunting.

## Output

- Commits in order: failing test, then the code that passes it. For a bug, the test commit comes first.
- The PR's "How it was checked" section with the evidence line from step 11 and, for a bug fix, the old SHA where
  the test fails.
- A task comment with the same line: `tico task comment <id> "<evidence line>"`.
- New rows in the "Tests" section of `knowledge/engineering.md` for any part found to have no seam.

## Checks before you finish

- For each test: can you say in one sentence which behaviour breaks if it goes red?
- Did you see each one fail first, for the right reason?
- Did the break-the-behaviour edit turn it red?
- Are all expected values literals?
- Is every double standing in for something outside our code?
- Does the evidence line say what ran, where, how many, and what did not run?

## Example

Invented example.

Bug task 518: "Customers with a 100% discount code are charged 1 cent."

Behaviour: "An order with a 100% discount totals 0." Seam: `POST /api/orders/quote`, the route the checkout screen
calls.

Test written first: quote an order of one item at 20.00 with code `FREE100`; `assert response.total_cents == 0`.
Ran it: red, `assert 1 == 0`. That is the customer's symptom, so the red is the right one. Ran the same test on the
commit before the branch, `3f9c2ab`: red with the same line.

Least code: the discount was applied after rounding the tax; moved the rounding to the end. Test green.

Bite check: put the old order of operations back, test red; restored. Renamed the private helper `_round_tax`, test
still green.

Evidence line in the PR: "Ran: pytest tests/api/test_quote.py, CI run 8841: 14 passed, 0 failed, 0 skipped. Red
before: `assert 1 == 0` at 3f9c2ab. Not run: browser checkout flow (QA covers it). Not proven: other discount types
above 100% combined."
