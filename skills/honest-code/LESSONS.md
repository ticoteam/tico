# Lessons

What Tico learned the hard way about tests and checks. Each row is one lesson with the incidents that taught it. A
lesson seen twice stops being text: it becomes a rule in `scripts/landmines.py` or ruff, and the row records the rule.
`SKILL.md` stays short; the checks get stricter.

## When to write a row

In the same pull request as the fix, when any of these happens:

1. You fixed or deleted a flaky test. The row names the cause.
2. A reviewer or a second model corrected a test, a check or a comment, and no check would have caught it.
3. A check was green while the thing it checks was broken (a false green).

First search this file for the cause. If a row covers it, add your incident to that row and raise `seen` by one. Write a
new row only when none does. A preference with no incident is not a lesson and does not go here.

## Row format

One table, in id order. One line per row.

`| id | lesson | incidents | class | seen | status | where |`

- **id**: `L` and the next free number. Ids are never reused or renumbered. Failure messages cite them.
- **lesson**: one sentence in the present tense that says what to do. No names, no story.
- **incidents**: `YYYY-MM-DD #<PR>` or `YYYY-MM-DD <10-character SHA>`, separated by `; `. Every incident has one of
  the two. A few words in brackets may follow.
- **class**: `script` when a script can decide it from a diff, a file, a log or a count. `judgment` when a reader must.
- **seen**: the number of incidents in the row.
- **status**: one of
  - `open`: text only.
  - `proposed <rule-id>`: the rule is drafted in `references/rules.md` and not built.
  - `encoded <check>`: a check that blocks a merge enforces it. Name the rule id or the script.
  - `example`: a `judgment` lesson whose bad and good pair is in `references/examples.md`.
  - `retired <reason>`: no longer true or no longer needed. The row stays.
- **where**: the path the lesson is about, or the path of the check that encodes it. `-` when there is none.

## Promotion

The pull request that records an incident also does the promotion that incident triggers.

- **A `script` row reaches `seen` 2.** That pull request adds the rule to `scripts/landmines.py` (pattern, message, one
  bad and one good case in the rule's test, today's counts as the baseline) or selects the ruff rule, and sets the
  status to `encoded <rule-id>`. Existing hits go in the baseline, so adding a rule changes no other file. The rule's
  message ends `See LESSONS.md#<id>`.
- **A `judgment` row reaches `seen` 2.** That pull request adds its bad and good pair to `references/examples.md` and
  sets the status to `example`. If a script can list candidates for a reader, it also adds a report-only rule.
- **A `judgment` row reaches `seen` 4.** Text and examples are not working. Either find the part a script can decide
  and encode that, or add one line to `SKILL.md` and remove one, so the file stays under 200 lines.
- **A row is encoded.** Delete any sentence in `SKILL.md`, `CLAUDE.md` or a bot template that only repeats it. The
  check carries the rule now.

## Keeping this file true

`scripts/landmines.py --lessons` checks this file on every run of the script. It fails when:

- a row's `seen` differs from its number of incidents, or an incident has no PR number or SHA;
- a `script` row has `seen` 2 or more and its status is `open`;
- an `encoded` row names a rule or script that does not exist, or a rule's message cites an id that is not here;
- a `where` path does not exist. Fix the path, or retire the row with the reason.

It also prints the number of `proposed` rows on every run, so unbuilt rules stay visible. No calendar sweep is needed:
the check runs on every pull request. When the table passes 60 rows that are not `encoded` or `retired`, merge rows
that share a cause before adding another.

## The table

Seeded 2026-11-18 from merged pull requests and commits on `origin/main` at `7e7842e5a2`.

| id | lesson | incidents | class | seen | status | where |
|---|---|---|---|---|---|---|
| L01 | A test that fails and passes on one commit is flaky. Fix its cause or delete it; never rerun or retry it to green. | 2026-11-03 #2294 (merged after rerunning one browser leg); 2026-11-12 e9dece9583 (empty commit "Re-run PR checks"); 2026-11-18 #4124 (CI retries failed tests once and passes) | script | 3 | encoded no-retry | `scripts/pr_checks.py` |
| L02 | No fixed wait in a test. Wait for the event, the drawn state or the write that the assertion needs. | 2026-10-25 #1523; 2026-10-31 #2003; 2026-11-12 #3404; 2026-11-12 #3548; 2026-11-13 #3652; 2026-11-13 #3672; 2026-11-13 #3696; 2026-11-17 #3889; 2026-11-18 #4172 (`test_needs_lane` passed before its requests arrived) | script | 9 | encoded no-fixed-sleep-py, no-fixed-wait-ui | `ui/tests/support/e2e.cts` |
| L03 | Assert the work done, as a count. Never assert elapsed time or a ratio of times. | 2026-11-13 #3719 (462 ms against one draw of 187 ms on a shared runner); 2026-11-18 #4189 (4 bounds on "does not wait" claims that a held Event already proved, one a per-worker deadline a fake clock makes exact) | script | 2 | encoded timing-assert | `scripts/landmines.py` |
| L04 | A test reads time through a seam it controls (`H.now`, `time_machine.travel(..., tick=False)`) and measures from the record's own timestamp, never from "now". | 2026-10-17 #795; 2026-10-26 #1648; 2026-11-01 #2071; 2026-11-03 #2367; 2026-11-13 #3551; 2026-11-17 #4070 | script | 6 | proposed unseamed-clock | `backend/hubdb.py` |
| L05 | A test starts from state it made. Reset process-wide caches in an autouse fixture, and hold or await background work before asserting. | 2026-10-17 #809; 2026-10-22 #1212; 2026-11-09 #3103; 2026-11-16 #3851; 2026-11-17 #3893 | judgment | 5 | example | `conftest.py` |
| L06 | A test never reaches a live service. Refuse the network and use a fake or a recorded fixture. | 2026-11-08 #3044; 2026-11-09 #2859; 2026-11-09 #3060 | judgment | 3 | example | `backend/tests/live.py` |
| L07 | A fake answers the way the real thing does: same shape, same refusals, same order, and no catch-all answer. | 2026-10-18 #849; 2026-11-14 #3741; 2026-11-14 #3744; 2026-11-16 #3841; 2026-11-18 #4189 (the fake judge answered `value` as the email classifier read it; the judge answers `choice`, so every real email was held) | judgment | 5 | example | `ui/tests/support/mailboxes-fake.cts` |
| L10 | A test must fail when the line it guards is removed. A bug fix names the SHA where its test fails. | 2026-11-14 #3741; 2026-11-14 #3744; 2026-11-14 #3746; 2026-11-14 #3749 (each had a mutation that survived until review); 2026-11-18 #4296 (the speech prefetch test hushed after the prefetched task finished) | judgment | 5 | example | - |
| L12 | A deleted test is named in the commit body, with why it went and what is now unchecked. | 2026-11-11 #3255 (the loss was not written down); 2026-11-11 c59f968542 (1,919 cases, empty body; one test restored 32 minutes later in 3f7a8ebffc) | script | 2 | proposed deleted-test-reason | `scripts/gates/verifier_count.py` |
| L13 | When code is removed, the tests for the paths that stay are kept and run. | 2026-11-16 #3840 (4 privacy regressions found in review); 2026-11-17 #3832 | judgment | 2 | example | - |
| L15 | A rebase or formatter run keeps every suppression the change added. Compare the counts before and after. | 2026-11-06 #2792 (a `fmt: skip` was dropped) | script | 1 | open | - |
| L16 | A run that never ran exits non-zero and says why. | 2026-11-07 #2895 (`remote.py` exited 0 with nothing dispatched) | script | 1 | encoded `scripts/remote.py` exit 75 and 76 | `scripts/remote.py` |
| L18 | An alarm is run once against a known-red input before anyone trusts its silence. | 2026-11-18 #4128 (the main-red alarm had not fired for four red runs) | judgment | 1 | open | - |
| L19 | A guard runs on the tree that will land, not only on the pull request's old head. | 2026-10-18 #849 | script | 1 | encoded `scripts/ci/merge_commit_checks.sh` | `scripts/ci/merge_commit_checks.sh` |
| L20 | A gate whose result changes on the same base is flaky. Run it twice on one base before it blocks anything. | 2026-11-07 #2926 | judgment | 1 | open | - |
| L21 | A shipped migration is never edited. The change goes in a new migration file. | 2026-11-17 #3916 (a release rolled back; fixed by #3939) | script | 1 | encoded `scripts/check_migration_order.py` | `scripts/check_migration_order.py` |
| L22 | Two pull requests never take the same migration version. | 2026-11-04 #2454 (with #2463; main could not start) | script | 1 | encoded `scripts/check_migration_order.py` | `scripts/check_migration_order.py` |
| L25 | Wait for a value and read it in one call. A wait followed by a separate read still races. | 2026-11-17 #4002 (first fix, still raced); 2026-11-18 #4125 | judgment | 2 | example | `ui/tests/stage-column-menu.cts` |
| L26 | A longer timeout is not a fix. Remove the wait, or keep the number only as a hang guard that no assertion depends on. | 2026-10-14 #503; 2026-10-20 #1013 | script | 2 | encoded no-raised-timeout | - |
| L27 | A tolerance on an exact count hides drift. Fix the input so the count is exact, or delete the test. | 2026-11-17 #4018 (`abs(more - steps) <= 5`, still on main) | script | 1 | open | `backend/tests/test_status_reads.py` |
| L28 | A failure that depends on which batch ran is a selection gap or a product bug until the file alone, with `-n 0`, passes. | 2026-11-17 #3980 | judgment | 1 | open | - |
| L29 | A test that flips can be showing a race in the product. Fix the product, and have the test hold one side until the other lands. | 2026-11-17 #3891; 2026-11-17 #3894 | judgment | 2 | example | `ui/health.ts` |
| L30 | A Rust test never holds a script open for writing while another test forks. Write it with `crate::util::write_script`. | 2026-10-15 #609; 2026-11-09 3210df2026 | script | 2 | proposed rust-test-script-write | `runner-rs/src/util.rs` |
| L31 | A module's test seam is a `def` that calls the stdlib (`def _sleep(seconds): time.sleep(seconds)`), not an assignment (`_sleep = time.sleep`) that binds the real function at import. | 2026-11-18 #4172 (an assignment seam hung a test that swaps the module's `time` for a fake clock); 2026-11-18 #4189 (4 assignment seams were left after pass 2 turned 7 into defs) | script | 2 | encoded no-assigned-clock-seam | `scripts/landmines.py` |
| L32 | An assertion passes on one outcome. An `or` between two expectations hides which one the code produced, and that one can be wrong. | 2026-11-18 #4172 (`test_stage_rule_timers` expected the wrong text; the `or` passed it) | script | 1 | proposed or-assert | `backend/tests/test_stage_rule_timers.py` |
| L33 | A ban matches every spelling of the call it bans (`setattr`, `patch.object`, `patch("a.b.c")`) and every attribute with the same harm, with a bad case for each in its test. | 2026-11-18 #4179 (`no-global-sleep-patch` was green with 7 `patch.object`/`patch(...)` sleep patches and 14 `time`/`monotonic` patches left) | script | 1 | encoded no-global-time-patch | `scripts/landmines.py` |
| L34 | A skip that fires on every CI run is a deleted test with no record. Make its premise hold in CI, or delete the test and say what is unchecked. | 2026-11-18 #4179 (`test_safe_git` skipped on every run: no git we run refuses the clone it expected refused) | script | 1 | open | `runner_support/tests/test_safe_git.py` |

## Ids not used

These ids came from the research notes and were not kept. The numbers stay unused.

- L08 (no doubles of our own code) and L14 (every suppression says why): owner rulings with no incident. They are
  HC-3 and HC-5 in `SKILL.md`, and rules in `references/rules.md`.
- L09 (a regression test fails on the old code): merged into L10.
- L11 (line coverage is not proof) and L24 (a report says what ran): no incident where it went wrong. Kept as text in
  `SKILL.md`.
- L17 (a cancelled leg is not a pass): no pull request or commit to cite. The `checks` job now names the failed leg.
- L23 (SQL changes show a query plan): not about tests or checks, and no incident.
