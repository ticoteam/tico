---
name: honest-code
description: Keep Tico's tests and checks telling the truth. Use before pushing a change that adds or edits a test; when a test failed and then passed on the same commit, or CI printed "Flaky (failed, then passed on retry)"; when you are about to add a sleep, a retry, a longer timeout, a mock, a skip or a lint suppression; when you touch a test that sleeps or mocks; when reviewing a pull request; when a reviewer makes a correction you have seen before; and before you report that tests pass.
---

# Honest code

A check that stays green on broken code is worse than no check: people and agents stop looking. An agent under
pressure to get green will sleep until a race usually wins, retry, raise a timeout, mock the code under test or silence
the lint. Each of those passes CI and none of them is true. This skill says what to do instead.

This file lives in Tico at `$HUB_DIR/skills/honest-code/SKILL.md`. `LESSONS.md` and `references/` are in this skill's
folder. Three places hold the rules:

- `scripts/landmines.py` and ruff hold every rule a script can decide. Their failure messages say what to do.
- This file holds the judgment calls, and what to do when a check fails.
- `LESSONS.md` holds what Tico learned the hard way. A lesson seen twice becomes a script rule.

## The rules

- **HC-1.** A flaky test is fixed at its cause the day it flips, or deleted with its reason recorded. No test is better
  than a flaky test.
- **HC-2.** A test that passes only on retry failed. A sleep, a retry, a longer timeout, a looser assertion and a rerun
  are not fixes.
- **HC-3.** A test doubles only what is outside the process: the network, a model, another company's API, the clock. Our
  own code runs for real.
- **HC-4.** A test fails when the behaviour it names breaks, and passes when only the internals change. Fewer strong
  tests beat many weak ones: a test that fails this is rewritten or deleted.
- **HC-5.** A suppression or a skip carries its reason on the same line.
- **HC-6.** A comment states a rule or a reason the code cannot show, in the present tense, and it is true.
- **HC-7.** A claim carries evidence from this turn: what ran, where, how many times, and what did not run.

Keeping the words of a rule while defeating its purpose breaks the rule. `asyncio.sleep` is a sleep, and so is a loop
of 50 waits of 20 ms. A helper that calls the test body twice is a retry. Cite the id in a review.

## Where tests run

- Locally: up to about 100 Python tests (a file or a few), or one to three browser scripts, under `nice -n 19`, with
  `-n 2` at most and never `-m slow`. Skip the local run when the one-minute load is above 30.
- Bigger: `python3 scripts/remote.py [--setup python|browser|rust|all] -- <command>` on RunsOn (it runs the pushed
  commit; exit 75 or 76 is not a pass), or mark the pull request ready and read the PR checks.
- A Tico bot working a task follows the bot section of CLAUDE.md for where its tests run. Everything else here applies.

## When a check fails

`scripts/landmines.py` counts each rule's hits per file and compares them with `baselines/landmines/<path>.json`. The
count must equal the baseline. `scripts/lint.sh` runs it, so pre-push, PR checks and the merge-commit checks do too.
Rules marked "proposed" in `references/rules.md` are not built yet: read your own diff against them.

- **Count above the baseline.** The message names the replacement. Make that change. Do not raise the baseline.
- **Count below the baseline.** You removed a hit. Lower the baseline in the same commit.
- **The rule is wrong for this line.** Fix the rule's pattern and its test case in the same pull request. Do not
  rewrite the line so the pattern misses it.
- **Ruff.** Fix the finding. If the rule is wrong for this line, write `# noqa: CODE - <reason>` with a reason a
  reviewer can check against the code.

## Judgment calls

### Does the test check behaviour?

For each test you wrote or changed, imagine two edits to the code under test.

1. **Break the behaviour**: a wrong value, a skipped write, a doubled send. The test must fail. If it would still pass,
   the assertion is too loose, the expected value comes from the code under test, or the test asserts data it built.
2. **Change the internals** and keep every result a caller can see. The test must still pass. If it would fail, it is
   pinned to how the code works. Rewrite it against the route, the public function or the page.

Keep a test that fails on the first edit and passes on the second. Rewrite or delete the rest: a deleted low-value
test is named in the commit body with why it went. Expected values are
literals you worked out by hand. For a bug fix, the test fails on the old commit with the failure the bug caused, and
the pull request names that SHA. The 80% diff-coverage gate shows that lines ran, not that anything checks them.

### Doubles

- Use the real app and database: the `api` fixture (`conftest.py`), `live(api)` (`backend/tests/live.py`), and in the
  browser `startHub` and `pageAs` (`ui/tests/support/real-hub.cts`).
- Double only what is outside Tico, with a fake that exists: `backend/tests/helpers/fake_github.py`,
  `scripts/fake_stripe.py`. `block_outside_network(monkeypatch, reason)` refuses every other host.
- A fake answers the way the real thing does: same shape, same refusals, same order. A `page.route` handler returns a
  body the real route returns. No catch-all handler that answers `{}`.
- Never `monkeypatch.setattr` or `mock.patch` a function of ours to return a canned value. To force a failure in our
  own code that cannot be produced from outside, say why on the line.
- An assertion on a mock checks the mock. Assert what the caller, the database or the page shows.

### Waiting and time

Wait for the thing the assertion needs. Move time; do not wait for it. Count work; do not time it.

| You need | Use |
|---|---|
| An event on the stream | `Stream.until(match)` in `backend/tests/helpers/events.py`. It raises and names the events it saw. |
| A call to reach a fake | A `threading.Event` the fake sets: `assert model.asked.wait(10), "..."` (`backend/tests/test_topic_change.py`) |
| Background work to finish | A helper that waits for the pending set to empty: `media_settled(api)` (`backend/tests/helpers/visuals.py`) |
| A later or fixed time | `monkeypatch.setattr(H, "now", lambda: at)` with `from backend.store import H`, or `time_machine.travel(..., tick=False)` |
| A product pause skipped | The module's own seam: `_sleep` in `backend/store.py`, `CLOCK` in `backend/company_billing.py`. Never patch `sleep` on the `time` module. |
| Drawn or written browser state | `until(check, what)` and `drawn(...)` in `ui/tests/support/e2e.cts`, or one `page.waitForFunction` that returns the value |
| A request the page sends | `page.waitForResponse(...)` registered before the action, awaited after it (`ui/tests/changelog.cts:110-112`) |
| A cost or speed promise | A count: draws, reads, SQL statements (`statement_scope.recorder`). Print milliseconds; never assert them. |
| Order between two Rust tasks | A `Notify` the first side sets (`hold_first` in `runner-rs/src/upload_pace/streamed.rs`) |

- Wait and read in one call. A wait followed by a separate read still races.
- A deadline is allowed only when it fails the test with a message that names what never arrived. A loop that stops
  quietly at its deadline lets the next assertion run on state that is not there.
- "Nothing else happens" is not proved by sleeping. Drive each step from the test and assert the count.

### Guards and suppressions

- A new `try/except`, fallback value, clamp or retry in product code hides a bug unless the state it handles is
  legitimate. Name that state. If it should never exist, find what produced it and fix that.
- Before you silence a diagnostic, ask whether it is the failure your change should fix. If it is, fix it.
- The form is `<tool comment> - <reason>`: `# noqa: BLE001 - every network failure is one answer: the check failed`.
  The same goes for `type: ignore`, `pyright: ignore`, `pragma: no cover`, `#[allow]`, `@ts-expect-error`, `skipif`
  and `xfail`.

### Comments

- Keep a comment that states a rule or a reason the code cannot show: an outside constraint, why a constant has its
  value, why a suppression is safe.
- Delete narration of the change ("now uses", "previously", "per review"), comments that restate the code, divider
  banners, commented-out code, and dates, versions, pull request numbers and names.
- If your change makes a comment false, fix or delete it in the same commit.
- A comment that says "do not change X" is weaker than a test that fails when X changes. Write the test, then delete
  the comment.

## A test failed, then passed on the same commit

It is flaky, whoever wrote it. CI's line `Flaky (failed, then passed on retry): <id>` is a failure. Do not rerun,
sleep, retry, raise a timeout or add a `ci/quarantine/` entry. If nobody on the pull request can fix it today, hand the
test id and run ids to the Release Manager bot, which fixes or deletes it the same day.

1. **Rule out a real failure.** Run the file alone at the failing SHA on RunsOn, three times, with `-n 0`. If it fails
   every time it is a product bug or a test-selection gap, not a flake. Fix that.
2. **Name the cause.** Read the failing log. The classes Tico has seen, with the fix that held for each, are in
   `references/examples.md`: a read before the state lands, a fixed sleep, the wall clock, state left by another test
   or thread, a race in the product.
3. **Fix the cause.** If the race is in the product, fix the product. Widening the window on a scratch commit (a held
   route, a delay in the fake) makes the old failure happen on every run and shows you have the right cause.
4. **Prove it.** Run it 20 times in a row (one test fits the local limit). pytest-repeat is not installed; loop:
   `python3 scripts/remote.py -- bash -c 'for i in $(seq 20); do .venv/bin/python -m pytest -q <file>::<test> || exit 1; done'`.
   For a browser script, loop `node scripts/ui-tests.cts <name>` with `--setup browser`. Report "20 of 20, run <id>".
5. **If you cannot fix it today, delete it.** The commit body and the pull request body state: the test id, the run
   ids where it flipped, the cause if known, the behaviour that is now unchecked, and what still covers it or
   "nothing". If the behaviour is a security, privacy, money, approval or data-safety path, the same pull request adds
   a deterministic test for it or links a ticket for one.
6. **Two fixes failed on the same guess?** Stop. Write the guess down and test it with a probe, or delete the test.
7. **Add or update the row in `LESSONS.md`.**

## Excuses to reject

| Excuse | Answer |
|---|---|
| "It failed on a pull request that does not touch it. Rerun." | It flipped on one commit, so it is flaky. HC-1 starts now, in the pull request that saw it. |
| "CI passed on the retry." | A pass on retry is a failure. The `Flaky (...)` line names the test you now fix or delete. |
| "The runner was slow. Give it more time." | A loaded runner is normal. Wait for the event, or count the work. |
| "Mocking that function is simpler." | Then the test checks the caller against your guess of the callee. |
| "The skip is temporary." | Nothing removes it. Fix the test or delete it with its reason. |
| "Deleting a test loses coverage." | A flaky test covers nothing: its red is ignored and its green is luck. Record what is unchecked and delete it. |
| "It passed when I ran it." | One run is not a rate. Give the count and the run id. |

## Red flags

Stop and reread the rules when your diff has any of these, or when you catch yourself thinking the last three.

- `sleep(`, `waitForTimeout`, `setTimeout` inside a test, or a bigger number after `timeout`
- `retry`, `rerun`, `flaky`, `skip`, `xfail`
- `mock.patch`, `MagicMock`, or `monkeypatch.setattr` on a module of ours
- `noqa`, `type: ignore`, `pyright: ignore`, `pragma: no cover`, `#[allow`, `@ts-expect-error`
- An assertion removed or loosened: `==` to `<=`, an exact count to `abs(a - b) <= n`, `toBe(3)` to `toBeGreaterThan(0)`
- A baseline number that goes up, or a deleted test with no reason in the commit body
- "Should pass now." "Probably a flake." "Unrelated to my change."

## Before you say done

- [ ] The diff has no red flag, or each one carries its reason on the line.
- [ ] `scripts/lint.sh` and `scripts/landmines.py` pass, and every baseline you lowered is in the commit.
- [ ] For each new or changed test you can name the broken behaviour it fails on. A bug fix names the SHA where its
      test fails.
- [ ] The tests ran on RunsOn or in PR checks. Zero tests run is a failure. A cancelled leg is a failure until its log
      shows no `FAILED`. A skip is not a pass.
- [ ] No test flipped on this commit. If one did, it is fixed with 20 of 20 and a run id, or deleted with its record.
- [ ] Your report reads `Ran: <command>, RunsOn run <id>: 14 passed, 0 failed, 0 skipped. Not run: <what>. Not proven:
      <what>.` A mock is not a live check. Another agent's "tests pass" is a claim until you read the run.
- [ ] Anything you learned the hard way has its row in `LESSONS.md`.

## Learn from it

Write a row in `LESSONS.md`, in the same pull request, when any of these happens:

- You fixed or deleted a flaky test.
- A reviewer or a second model corrected a test, a check or a comment of yours, and no check would have caught it.
- A check was green while the thing it checks was broken.

If a row already covers it, add your incident to that row and raise `seen`. When that takes a `script` row to 2, the
same pull request adds the rule to `scripts/landmines.py` or ruff and marks the row `encoded`. `LESSONS.md` has the row
format and the promotion rules. This file stays at 200 lines or fewer: to add a line here, remove one.

## References

- `references/examples.md`: cause class to deterministic fix to Tico helper, and bad and good pairs with file and line.
- `references/rules.md`: the `scripts/landmines.py` rules and the ruff rules, with today's counts.
