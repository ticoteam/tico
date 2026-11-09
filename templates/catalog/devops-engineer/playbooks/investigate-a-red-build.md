# Investigate a red build

Triggered by a task or message about a failing run ("main is red", "why does deploy keep failing?"). Budget 20 minutes.
The outcome is the cause, the first bad commit, and the fix or the owner. You rerun and revert nothing.

---

## Inputs

- The task, the repository and the workflow name.
- The last 30 runs on the branch: `gh run list -R <repo> --branch main --workflow <name> --limit 30`.
- The failed log of the first red run: `gh run view <id> -R <repo> --log-failed`.

## Steps

1. **Find the first failure.** `hub task show <id>`, then the run list above. Walk back to the last green run. The first
   red run after it bounds the commit range to look at.
2. **Read the log.** Read the first failing step, not the last one: later steps often fail because an earlier one left
   state behind. Classify the failure with `skills/classify-a-failed-run/SKILL.md`: product bug, flaky test,
   infrastructure, dependency or configuration. Quote the lines that show it, with any credential redacted.
3. **Check flakiness before blaming a commit.** Has this test flipped before (`knowledge/flaky-tests.md`)? Did the same
   commit pass on another run? If so, it is flaky, not broken. Say so and propose the register entry.
4. **Name the owner.** Take the owner from `knowledge/pipeline.md`. A product bug goes to the QA Engineer for triage, as
   a note, when their decision is needed.

## Output

On the task, four lines:

- **Cause:** the class and the quoted evidence.
- **First bad run and commit:** a link.
- **Fix or owner:** the change or the revert a human should consider, or the owner from `knowledge/pipeline.md`.
- **Confidence:** high, medium or low, and what would confirm it.

## Done when

- The cause is named, or the log is named as unreadable with the reason.
- The first bad run and commit are linked, or the commit range is given with the reason it could not be narrowed.
- The task is closed with `hub task update <id> --status done --note`.
