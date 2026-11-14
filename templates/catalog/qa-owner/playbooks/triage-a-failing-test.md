# Triage a failing test

Triggered by a task or message about a failing test ("this test keeps failing", "why is the e2e suite red?"). Budget 20 minutes.
The outcome is the class of the failure, the first bad commit, and a decision or a hand-off. You rerun and change nothing.

---

## Inputs

- The task, the repository, the suite and the test name.
- The last 30 runs of the suite on the branch: `gh run list -R <repo> --branch main --workflow <name> --limit 30`.
- The failed log of the first red run: `gh run view <id> -R <repo> --log-failed`.

## Steps

1. **Find the first failure.** `tico task show <id>`, then the run list above. Walk back to the last green run. The first red run
   after it bounds the commit range to look at.
2. **Read the failing assertion.** Read the first failing step, not the last. Classify the failure with
   `skills/classify-a-test-failure/SKILL.md`: product bug, flaky test, test defect, environment or dependency. Quote the lines
   that show it, with any credential redacted.
3. **Check flakiness before blaming a commit.** Has this test flipped before? Did the same commit pass on another run? If so,
   it is flaky, not broken. Enter it in `knowledge/flaky-tests.md` with the run ids.
4. **Decide or hand over.** A test that fails on the default branch for every pull request is main red: follow
   "When main is red" in `AGENTS.md` (one `Main red:` task: look before you file, depend on it, never re-run). A flaky test gets a decision from `skills/decide-fix-or-quarantine/SKILL.md`. A test defect is fixed
   as a branch and a pull request when the task asks for it. A product bug is handed to the feature owner of its area as a Product
   task with the failing test, its runs and the commit range: `tico task create --owner <feature owner> --pipeline product --title "<the bug>" --body "<test, runs, commits, path>"`.
5. **Name the owner.** Take the owner of the suite from `knowledge/suites.md`. A product bug also goes to that owner as a note,
   when their decision is needed.

## Output

On the task, four lines:

- **Cause:** the class and the quoted evidence.
- **First bad run and commit:** a link.
- **Decision or hand-off:** the fix, the quarantine with its deadline, or the task the bug went to.
- **Confidence:** high, medium or low, and what would confirm it.

## Done when

- The cause is named, or the log is named as unreadable with the reason.
- The first bad run and commit are linked, or the commit range is given with the reason it could not be narrowed.
- The task is closed with `tico task done <id> -m "..." --version N`.
