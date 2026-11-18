# Rules for scripts

**Status.** `scripts/landmines.py` has six rules built: `no-retry`, `no-fixed-sleep-py`, `no-fixed-wait-ui`,
`no-raised-timeout`, `no-unittest-mock` and `no-global-time-patch`, plus the `--lessons` check. Every other row below is
a proposal. None of the ruff changes below is made. When a rule is built, add it to the "Built" table and mark its row
in `../LESSONS.md` `encoded`.

Counts are `git grep -E` line counts on `origin/main` at that commit, with the pattern shown. "Python test files"
means `**/tests/**/*.py`, `**/test_*.py` and `**/conftest.py` (1,515 files). `ui/tests` has 409 files. Generate the
baseline from the script on the day it merges; do not copy these numbers into it.

## How `scripts/landmines.py` works

- One baseline file per source file: `baselines/landmines/<path>.json`, for example
  `{"no-fixed-sleep-py": 2, "no-unittest-mock": 1}`. This is the layout of `baselines/ruff/`.
- The count for each (file, rule) must equal its baseline.
  - Above: fail, and print every hit in the file with the rule's message.
  - Below: fail as stale. `<rule>: <path> now has <count>; lower its baseline to <count>, or remove the entry at 0.`
  - No entry means 0.
- `--check` compares. `--check --base origin/main` also fails when any count in the baseline is higher than on the
  base. `--write` lowers counts and removes entries at 0; it never raises one. These are the flags of
  `scripts/lint_baseline.py`.
- A **ban** rule has no baseline: its count is 0 everywhere. A **ratchet** rule starts at today's counts. A **report**
  rule prints its hits and does not fail; it is how a rule with an untested pattern starts.
- Every message has three parts: what was found, the named replacement, one clause of why. It ends
  `See skills/honest-code/LESSONS.md#<id>` when a lesson is behind it.
- Each rule has a test with one bad and one good case. A hit inside a string or a diff fixture is not a hit.
- There is no inline opt-out comment. A line the rule should not match is fixed in the rule.
- `--lessons` checks `LESSONS.md` (see that file, "Keeping this file true").
- It runs from `scripts/lint.sh`, so pre-push, PR static checks and the merge-commit checks all run it.

## Rules

| id | Pattern | Count today | Message (what to do instead) | Mode | Lesson |
|---|---|---|---|---|---|
| no-fixed-sleep-py | `(time\|asyncio)\.sleep\(` in Python test files | 108 lines in 66 files (101 `time.sleep`, 7 `asyncio.sleep`) | Fixed sleep in a test. Wait on the event the fake emits (`threading.Event`, `Stream.until`), or move the clock with `H.now` or `time_machine.travel(..., tick=False)`. A sleep guesses how long real work takes and fails on a loaded runner. | ratchet | L02 |
| no-fixed-wait-ui | `waitForTimeout\(` and `new Promise.*setTimeout\(` in `ui/tests` | 395 lines in 143 files; 111 lines in 67 files | Fixed wait in a browser script. Use `until(check, what)` or `drawn(...)` from `support/e2e.cts`, one `page.waitForFunction` that returns the value, or `page.waitForResponse` registered before the action. | ratchet | L02 |
| no-raised-timeout | In `ui/tests`: `timeout: *[0-9]{4,}` not wrapped in `t()`; any `t(N)` with N of 40000 or more. With `--base`: a timeout literal in a test file that is larger than on the base. | 116 lines in 46 files; 5 lines | A longer timeout is not a fix. Wait for the event the step depends on. A deadline goes through `t()` and fails with a message that names what never arrived. | ratchet; the `--base` part is a ban | L26 |
| no-retry | `pytest\.mark\.flaky`, `rerunfailures`, `--reruns`, `reruns=` in Python tests, `pytest.ini` and workflows; a `playwright.config.*` file; `retries:` in `ui/` | 0 | Do not retry a test. A test that passes on a rerun is flaky: fix its cause or delete it with its reason (SKILL.md, "A test failed, then passed"). | ban | L01 |
| no-unittest-mock | `unittest\.mock`, `MagicMock`, `mock\.patch` in Python test files; a line whose only patch is `mock.patch.dict(os.environ, ...)` is not a hit | 94 lines in 30 files | Do not replace our own code in a test. Run the real code. For something outside Tico use a fake (`references/examples.md` lists them) or pass the dependency in. | ratchet | owner ruling |
| no-global-time-patch | `setattr(`, `patch.object(` or `patch('...time.x')` on an attribute of the `time` module (`sleep`, `time`, `monotonic`, ...) in Python test files | 0 (17 `sleep` lines moved to module seams in #4172; 7 more `patch.object`/`patch(...)` spellings and 14 `time`/`monotonic` lines in pass 2) | This replaces it on the `time` module for the whole process, so every thread sees it. Give the module its own seam (`def _sleep(seconds)`, `def _now()`, `def _monotonic()`, as in `clients/tico.py` and `docker/updater.py`) and patch that, or use `time_machine` for the wall clock. | ban | L31, L33 |
| no-own-module-patch | `monkeypatch.setattr(<module of ours>, ...)` where the target is not an allowed seam (`H.now`, a module's `_sleep` or `CLOCK`, an environment value), and `monkeypatch.setattr` on a stdlib module (`subprocess.run`, `os.open`, `time.time`). Needs `ast`, not a regex. | 2,860 `monkeypatch.setattr` lines in 548 files, all targets; the share that hits our own functions is not measured | A canned return value for our own function means the test checks only the caller. Call the real function, or pass the outside dependency in. To force a failure that cannot be produced from outside, say why on the line. | report, then ratchet | owner ruling |
| suppression-reason | A suppression with nothing after it on the line: `# pragma: no cover`, `# type: ignore[...]`, `# pyright: ignore[...]`, `# noqa: CODES`, `#[allow(...)]`, `@ts-expect-error` | `pragma`: 9 (7 in product code, 2 inside a diff fixture). `type: ignore`: 106 (72 backend, 31 scripts, 3 evals). `pyright: ignore`: 121. `noqa`: 998, of which 77 outside test files. `#[allow`: 53 of 60 outside `app/vendor` have no `//` on the line. `@ts-expect-error`: 5, the 3 read have a reason. | Say why on the same line: `<tool comment> - <reason>`, as in `# noqa: BLE001 - every network failure is one answer: the check failed`. If the diagnostic is the bug your change should fix, fix it. | ratchet | owner ruling |
| skip-reason | `pytest.mark.skip`, `skipif`, `xfail` call with no `reason=` argument (`ast`); `pytest.skip(...)` inside a deadline loop or after a service start (should be `pytest.fail`); `#[ignore]` with no reason; `test.skip` or `test.fixme` in `ui/tests` | 25 mark lines, 9 with no `reason` on the same line (multi-line calls not resolved); 34 `#[ignore` lines not read; 0 in `ui/tests` | A skip needs `reason=` that names what is missing. A test skipped because it fails is fixed or deleted. | ban for new; ratchet | owner ruling |
| deleted-test-reason | With `--base`: a removed `def test_`, `#[test]` function or `ui/tests/*.cts` file whose name is not in a commit body on the branch | not counted; `scripts/gates/verifier_count.py` already reports `deleted_test` | Name each deleted test in the commit body, with why it went and what is now unchecked. | ban | L12 |
| banner | Python `^\s*# ?-{3,}`; JS and TS `^\s*// ?-{3,}` | 1,922 lines in 468 files; 514 lines in 138 files | Delete the divider. A comment states a rule or a reason. | ratchet | owner ruling |
| change-comment | A comment containing "now uses", "previously", "per review", "used to", "as requested", a month and day, a version such as `v0.6.0`, or `(#1234)` | about 13 Python lines for the phrases (rough); dates and versions not counted | Change history goes in the pull request. Keep the reason in the present tense, without who or when. | ratchet | owner ruling |
| timing-assert | An assertion line with `perf_counter`, `monotonic()`, `time.time()`, `elapsed`, `Date.now` or `performance.now` | about 36 Python lines and 3 in `ui/tests` (rough; includes deadlines, not all are timing assertions) | Assert the work done, as a count. Print the time; never assert it. | report | L03 |
| count-tolerance | `assert .*abs\(.*\) *<=? *[0-9]` in Python test files | 6 lines (rough) | A tolerance on a count hides drift. Fix the input so the count is exact. | report | L27 |
| unseamed-clock | `datetime.now(`, `time.time(`, `Date.now(` or `new Date()` in a test that does not set `H.now` or use `time_machine` | not measured | Read time through `H.now`, or pin it with `time_machine.travel(..., tick=False)`. | report | L04 |
| silent-deadline-poll | A `while` loop bounded by a deadline whose exit is not followed by an assertion on the awaited condition (`ast`) | not measured; `backend/tests/test_flight.py:279` and `:131` are examples | This loop stops quietly at its deadline. Raise an `AssertionError` that names what never arrived. | report | L02 |
| double-reason | `mock.patch`, `MagicMock`, `Mock(` in a Python test with no `# double: <reason>` on the line | not measured | Say why this double is outside Tico, or run the real code. | report, then ratchet | owner ruling |
| or-assert | `assert .* or ` in Python test files | not measured | An assertion that passes on either of two outcomes cannot tell which one the code took. Assert the one that should happen. | report | L32 |
| catch-all-route | A `route` handler in `ui/tests` that answers `{}` for any path | about 49 lines match `json({})` or a `'{}'` body (rough; not all are catch-alls) | Add a handler for each request the page makes. An unmatched request fails the script. | report | L07 |
| no-instant-check | `.isVisible(`, `.isHidden(`, `.isEnabled(`, `.isDisabled(` in `ui/tests`; `force: true`; `networkidle` | 936 lines; 3; 17 | These return at once and do not wait. Use `locator.waitFor()` or `page.waitForFunction`. | report; `force` and `networkidle` ratchet | L25 |
| env-sniff | `PYTEST_CURRENT_TEST`, `process.env.CI`, `GITHUB_ACTIONS` in product code | 0 for `PYTEST_CURRENT_TEST` in `backend`, `scripts`, `runner_support`; the others not measured | Product code does not check whether a test is running. Pass the dependency in. | ban | outside source |
| rust-test-script-write | `fs::write` followed by `set_permissions` on the same path inside `#[cfg(test)]` code in `runner-rs` | not measured (98 `set_permissions` lines in `runner-rs/src`, product and test together) | Write a test script with `crate::util::write_script`. A test that holds the file open for writing makes a concurrent `exec` fail with ETXTBSY. | report | L30 |

## Checks that are not a pattern over source files

These belong in CI code, not in `landmines.py`.

- **Zero tests run.** A leg whose runner prints `no tests ran`, or whose JUnit file has 0 cases, fails. Home:
  `scripts/pr_checks.py`.
- **A pass on retry.** Today `scripts/pr_checks.py` prints `Flaky (failed, then passed on retry): <id>` and passes the
  leg. See `../OPEN-DECISIONS.md` item 1.
- **Suppression counts across a rebase or a formatter run.** Count `noqa`, `pragma`, `type: ignore` and `fmt: skip`
  before and after; fail when one the branch added is gone (L15). Home: `scripts/format_rebase.sh`.
- **Alarm canary.** Each alarm runs once in CI against a known-red input (L18).
- **Gate stability.** A new report-only tool runs twice on one base before it blocks; a different result is a flaky
  gate (L20).
- **Mutation run.** Remove the guarded line on a scratch branch, push it, run the test through `scripts/remote.py`,
  delete the branch (L10). `remote.py` runs only pushed commits.

## Built

Checks on `origin/main` that already enforce a lesson. Do not duplicate them in `landmines.py`.

| Check | What it enforces | Lesson |
|---|---|---|
| `scripts/landmines.py` | `no-retry` and `no-global-time-patch` (bans), `no-fixed-sleep-py`, `no-fixed-wait-ui`, `no-raised-timeout`, `no-unittest-mock` (ratchets), `--lessons` | L01, L02, L26 |
| `scripts/check_migration_order.py` | No edit to a migration the base already has; no duplicate version | L21, L22 |
| `scripts/ci/merge_commit_checks.sh` | Static checks run again on the merge commit that lands | L19 |
| `scripts/remote.py` exit codes | 75 when never dispatched, 76 when there is no result | L16 |
| `scripts/diff_coverage.py` (`bare_pragmas`) | An added `# pragma: no cover` needs a reason of 3 words or more. Added lines only. | owner ruling |
| `scripts/check_ui_strict.py` | No `@ts-ignore` in strict UI files | owner ruling |
| `scripts/check_test_imports.py` | A test file does not import another test file | - |
| `scripts/pytest_warning_ignores.py` | The `ignore:` lines in `pytest.ini` do not grow | - |
| `scripts/gates/verifier_count.py` | Reports new skips, new suppressions, deleted tests and raised baselines. It does not block. | L12 |

## Ruff

What is true on `origin/main` (`ruff.toml`, ruff 0.15.9):

- `select` already has `S110`, `BLE001`, `PGH004`, `E722`, `RUF100`, `B`, `ASYNC` and `DTZ` for product code.
- Test files are exempt from every one of them. The three test globs in `[lint.extend-per-file-ignores]` leave only
  `F821`, `F841` and `E9` on.
- `scripts/lint_baseline.py` says test files "are not part of the baseline". A rule switched on for tests has no
  ratchet there: its hits must be 0 in the same pull request.

Proposed, one pull request per step:

1. **Tests get the swallow rules.** Remove `S110`, `BLE001`, `PGH004` and `E722` from the three test ignore lists.
   Hits in backend tests, measured with ruff by an earlier research pass and not re-run here: `S110` 0, `PGH004` 0,
   `BLE001` 10. Fix the 10.
2. **Tests get the pytest rules.** Add `PT011` (36 hits: `pytest.raises` too broad), `PT012` (17: more than one
   statement in a `raises` block) and `PT017` (11: assertion on an exception inside `except`) to `select`, and ignore
   them outside tests. Fix the 64.
3. **Commented-out code and prints.** Add `ERA001` (7 in tests, 18 in product) and `T201` (23 in tests, 85 in product).
   Product hits go in `baselines/ruff/`; test hits are fixed.
4. **`B011`** (`assert False`) has 0 hits in tests. It is ignored there through the `B` prefix. Replace `B` in the test
   ignore lists with the `B` codes that stay ignored.
5. **Stale `noqa` in tests.** Most of the 998 bare `noqa` lines are `# noqa: F401` and `# noqa: F811` in test files,
   where ruff already ignores both codes. They suppress nothing. Delete them with a script, then take `RUF100` out of
   the test ignore lists.
6. **`TID251` is the end state for sleeps and mocks, not the start.** The messages below work on ruff 0.15.9. Tests
   have no ruff baseline, and there are 108 sleep lines and 94 mock lines today, so `landmines.py` ratchets them
   first. When a count reaches 0, add the ban, scoped to test files, and drop the `landmines.py` rule.

```toml
[lint.flake8-tidy-imports.banned-api]
"time.sleep".msg = "Wait on a signal, not a clock. Await the operation or the event it emits; use a fake clock to move time."
"unittest.mock".msg = "Do not replace dependency behaviour in tests. Use the real implementation; spies must only observe."
```

`TID251` sees imports and call sites. It does not see `mock.patch("backend.thing")` strings or `monkeypatch`.

Not adopted: `PT018` (8,749 hits, style), `PT009`, `PT006`, `PT027` (style), and `SIM105`, which suggests
`contextlib.suppress`, another way to swallow an error.

## Rejected rules

- A raised-timeout allowlist. Existing literals are a ratchet; no new one is allowed.
- A whole-file exemption from the sleep rule when a fake clock appears anywhere in the file. The rule counts lines.
- An inline `landmine-ok: <reason>` comment. It would become the easy way out.
- A playwright `retries` setting, `pytest-rerunfailures`, and `xfail(strict=False)` for a test that sometimes fails.
- A rule that requires `EXPLAIN QUERY PLAN` in a pull request that changes SQL. It is a review rule about speed, not
  about whether a check tells the truth.
