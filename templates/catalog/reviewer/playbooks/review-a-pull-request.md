# Review a pull request

Used in your Engineering review stage (Review 1 or Review 2), and for each pull request in
`playbooks/weekday-review-queue.md`. Budget 15 minutes.

Inputs: the pull request and the Spec page listed under "From earlier stages" in `tico task show <id>` (in Review 2,
also Reviewer 1's page and what the Builder said it fixed), the pull request's description, its linked issue, its diff, its checks, `knowledge/standards.md`, `knowledge/patterns.md`,
and `knowledge/review-practice.md`.

Outcome: one Review page at one head commit. Its first line names that commit; then a summary and whether anything
blocks. It names what was read and what was not.

Done when every changed file was read or named as skipped, each risky surface has its evidence or a blocking question, every
comment has a label, a location and a quote, and the page is attached to the task (or, outside the pipeline, the task note
carries the blocking count).

---

## 1. Read the change in order

    gh pr view <n> -R <repo> --json headRefOid
    gh pr view <n> -R <repo> --comments
    gh pr diff <n> -R <repo>
    gh pr checks <n> -R <repo>

1. The description and the linked issue: what the change is for, in the issue's words. If the description says nothing, that is
   the first finding.
2. The risk map: follow `skills/map-the-risk-in-a-diff/SKILL.md`. Mark the risky surfaces before you read any line.
3. The design: should this change exist, and does it fit the code around it? Ask this before any line.
4. The callers of each changed function, the tests, and whether a test would fail on the old code (follow
   `skills/check-that-a-test-proves-the-change/SKILL.md`).
5. The details: naming, comments, error handling, complexity, a migration or a config change, a new dependency.
6. The other open changes on the same files, and the base branch's commits since the branch point.
7. Checks: what passed, what failed, what is pending, and whether a failure is this change's. A check that did not run is "not
   checked", not green.

## 2. Check against the team's standard

Walk `knowledge/standards.md` item by item. Note each item as met, not met, or not checkable from the diff. Look for a pattern from
`knowledge/patterns.md`. A secret, a removed check or a removed test is blocking. On an Engineering task it goes on the page, and the Builder
fixes it in Fixes (a secret's rotation goes to the Security Owner); never to a person by task. Outside the pipeline, report it
to the owner in `knowledge/standards.md` at once by task.

## 3. Size

If the change is over the team's line, or mixes a refactor with a feature, draft one polite request to split it, naming a
suggested seam. Review what you can regardless.

## 4. Write the review

In `reports/reviews/<task>-<first 7 of the commit>.md`, in the comment style chosen during setup (default labelled):
- **Summary**, two lines: what the change does, the head commit read, and whether anything blocks it.
- `issue (blocking):` each thing that ships a bug, breaks a stated rule, or costs money or data to undo. File, line, quoted code,
  what happens, and what would resolve it.
- `issue:` a real problem that can wait for a later change.
- `question:` what you could not tell from the diff.
- `suggestion:` an improvement with a short example.
- `nit:` preferences, at most five, never blocking.
- `praise:` one specific line, when earned.
- **Not checked:** tests you did not run, files skipped, pending checks.

Every comment is polite, about the code, and specific. Never "this is wrong": say what happens and why. Follow
`skills/write-a-review-comment-the-author-can-act-on/SKILL.md`.

## 4b. Screens

Only in Review 2, the last review before merge. If the diff touches `ui/`, take before and after
screenshots of the changed screens with `python3 scripts/release_shots.py --base <merge base> --head <head commit> --version
<next release> --out <dir>`, then attach `manifest.json` and every picture it names to the task with `tico task attach <id> <file>`,
one file per call. The review page shows only what is attached here. If the diff does not touch `ui/`, attach nothing and write
"No screens changed" in your summary. If the tool cannot run on this computer, attach nothing and say so under Not checked.

## 5. Finish

In a review stage: publish and attach the page. It is HTML (`reports/reviews/<task>-<first 7 of the commit>.html`): Tico
refuses a Markdown or plain-text page for a requirement. At the top, two or three lines say which commit it reviews and
what it found (blocking or not); then the sections above, with simple inline CSS and nothing from elsewhere.
`tico page publish reports/reviews/<file>.html --task <id>` prints the file id; then `tico task attach <id> page:<file id> --req "<requirement>" --version N`, using the requirement `tico task show`
names. The page is the whole hand-off: the stage moves on when it covers the newest commit, and every finding on the pull
request goes to the Builder through it, never to your Manager or a person as a separate task. Commit this repository.

Outside the pipeline: `tico task done <id> -m "..." --version N`: the pull request, the head commit, the number of
blocking issues, and what you did not check. Post requested comments with the exact text and pull request using your
Tools when a person has turned mail sending on in Tico; otherwise keep the draft.

## When you cannot tell

Say so as a `question:`. A guess written as a finding wastes an author's day. When the head moves after your review, read the new
commits, write the page again at the new commit, and attach it.
