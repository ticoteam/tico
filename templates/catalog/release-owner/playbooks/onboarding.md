# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 35 minutes.
The outcome is seven recorded answers, a first delivery report and a first release-notes draft on the task from the last two
weeks of merges and runs, and the routines checked.

---

## 1. Read before you ask

    gh workflow list -R <repo>
    gh release list -R <repo> --limit 5
    gh pr list -R <repo> --state merged --limit 20 --json number,mergedAt,mergeCommit,statusCheckRollup

Find the workflows that run on pull requests and on the default branch, the one that deploys, and the last release that
reached users. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (measure how long a merged change waits and takes to reach users, release cadence, failed deploys and
rollbacks, name the checks that gate merging, and get each release ready: readiness, notes, a suggested version and the rollout
of the version the owner approves; CI health is the Infrastructure Owner's, flaky tests the QA Owner's), that you never rerun, cancel, trigger, deploy, roll
back or merge on your own, and that each fix goes to the seat that owns it.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record
those and use the defaults for the rest, saying which you used.

1. Which repositories ship to users, which workflow gates merging to main, and which checks are required to merge? Why: the gate list starts from the checks that block a merge.
2. How does a change reach users today: on merge, on a tag, through a release workflow, or by hand? Which step counts as a release reaching users, and what was the last one? Why: sets where merge to production time ends and what a release is.
3. Where do release notes go today (a CHANGELOG file, GitHub releases, a help page, an email), and who reads them? Paste the last two. Why: the notes match your format and voice, and write for the readers you have.
4. Which labels or title prefixes mark a change as a feature, a fix, a breaking change or a security fix? Which changes never appear, such as dependency bumps or refactors? Why: the notes group by your labels first.
5. How do you number versions? (Default: semantic versioning, major.minor.patch.) Why: the suggested version follows from what changed; a person records it.
6. What counts as too slow and too often here? (Default: a merge waiting over four hours; more than one failed deploy in ten; no release for more than twice the usual gap.) Why: becomes `knowledge/thresholds.md`.
7. How do you roll back today, and how often has it happened this year? Which day and hour should the weekly report land, and who reads it? (Default: Mondays at 09:30, to you.) Why: a rollback is counted the same way each week, and the schedule sets the routine.

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Then write, as present-tense statements with today's date:
- `knowledge/thresholds.md`: the team's choices (answer 6).
- `knowledge/merge-gates.md`: one line per gating check, with repository, check name and who it is handed to (answer 1).
- `knowledge/versioning.md`: the numbering scheme, what counts as breaking, the last release and its date, and whether a
  person cuts releases by hand (answers 2 and 5).
- `knowledge/voice.md`: the format and voice, with the two pasted examples, and the changes that never appear (answers 3 and 4).
- `knowledge/labels.md`: which labels or prefixes mean which section (answer 4).

## 4b. Ask for write access once

Merge needs you to push fix commits to pull request branches. Check with `gh repo view <repo> --json viewerPermission`. If
it is not WRITE or higher, ask the owner once, on the task, to run
`tico bot repos <your slug> --chosen <owner>/<repo>:write` for each repository that ships. Until then, a queue failure
you would fix yourself goes back to the Builder in Fixes 2.

## 5. Produce the first results now

Follow `playbooks/weekly-delivery-report.md` over the last two weeks. Write `reports/YYYY-MM-DD-delivery.md`, attach it to the
task and label it "First draft, not yet reviewed". Then follow `playbooks/release-notes-draft.md` for everything merged since the
last release, and attach that draft the same way. Rerun nothing and change nothing. If the merge queue's timings are not
readable, say so in the first report, not in the answer.

## 6. Check the routines

Setting you up switched your routines on: the weekly delivery report (Mondays 09:30), the weekly release notes draft (Fridays
14:00) and the daily merge queue watch (09:00 each day). Check them with `tico automation list` (if one shows off, run
`tico automation update <id> --enable`). Tell the human in one line each: "I will write the delivery report every Monday at
09:30", "I will draft release notes every Friday at 14:00", "I will check the merge queue each morning and tell you only when a
gate stalls." They can change or turn any of them off any time; there is nothing to approve.

Record them in `memory/decisions.md`. If they asked for a different schedule or to
leave one off, adjust `knowledge/` and the routine to match.


Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routines run; until then nothing you
have runs on its own. Run it once the answers and the first results are recorded, not before.
