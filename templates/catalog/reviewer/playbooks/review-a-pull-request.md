# Review a pull request

Used in your Engineering review stage (Review 1 or Review 2), and for each pull request in
`playbooks/weekday-review-queue.md`. Budget 15 minutes.

Inputs: the pull request and the Spec page listed under "From earlier stages" in `tico task show <id>` (in Review 2,
also Reviewer 1's page and what the Builder said it fixed), the pull request's description, its linked issue, its diff, its checks, `knowledge/standards.md`, `knowledge/patterns.md`,
and `knowledge/review-practice.md`.

Outcome: one Review page at one head commit, built with `skills/build-the-page/SKILL.md` from its
`templates/review.html`. It opens with what the change does and whether anything blocks, and its title names that
commit; then the screens, when the change has any; then what was read and run, and the details. It names what was read
and what was not.

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
   checked", not green. A failure that main has too is not a finding against this change. When main is red:
   - Look for the one open task first, snoozed ones included (without `--snoozed all` a snoozed one is hidden):
     `tico task list --q "Main red" --status open --snoozed all`. Name it on the page, and say on it what it blocks:
     `tico task comment <main-red task> "Blocks #<your task> (PR #<the pull request>): <test> fails, <run link>"`.
     Any relation but the dependency below changes the other owner's task too, and {{app_name}} refuses it.
   - File one only when none is open, never a second for the same break, titled
     `Main red: <the failing test or check>, since <the pull request or commit that broke it>`.
   - When your stage cannot finish until main is fixed, make your task depend on it:
     `tico task relate <your task> depends_on <main-red task>` (written on your task only, so allowed on another
     owner's). With no task to depend on, or a refusal, snooze naming the fix pull request,
     `waiting for #<fix pull request> to merge`. Never a guessed date.
   - Once main is fixed, the fix is the Builder's rebase and push (`git rebase origin/<base branch>`), never re-run: a
     re-run tests the same merge with the old main and fails again.

## 2. Check against the team's standard

Walk `knowledge/standards.md` item by item. Note each item as met, not met, or not checkable from the diff. Look for a pattern from
`knowledge/patterns.md`. A secret, a removed check or a removed test is blocking. On an Engineering task it goes on the page, and the Builder
fixes it in Fixes (a secret's rotation goes to the Security Owner); never to a person by task. Outside the pipeline, report it
to the owner in `knowledge/standards.md` at once by task.

## 3. Size

If the change is over the team's line, or mixes a refactor with a feature, draft one polite request to split it, naming a
suggested seam. Review what you can regardless.

## 4. Write the review

In `reports/reviews/<task>-<first 7 of the commit>.html`, from the skill's `templates/review.html`, in the comment
style chosen during setup (default labelled):
- **The opening**, two or three lines: what the change does and whether anything blocks it, then **Decide:** what the
  Builder must fix ("fix the 2 blocking issues below", or "nothing blocks"). The title names the head commit read.
- **How it was checked**: what you read and ran, with the real output.
- **Findings**, in the details:
  - `issue (blocking):` each thing that ships a bug, breaks a stated rule, or costs money or data to undo. File, line, quoted code,
  what happens, and what would resolve it.
  - `issue:` a real problem that can wait for a later change.
  - `question:` what you could not tell from the diff.
  - `suggestion:` an improvement with a short example.
  - `nit:` preferences, at most five, never blocking.
  - `praise:` one specific line, when earned.
- **Not checked**, in the details: tests you did not run, files skipped, pending checks.

Every comment is polite, about the code, and specific. Never "this is wrong": say what happens and why. Follow
`skills/write-a-review-comment-the-author-can-act-on/SKILL.md`.

## 4b. Screens

Only in Review 2, the last review before merge. If the diff touches `ui/`, take before and after
screenshots of the changed screens with `python3 scripts/release_shots.py --base <merge base> --head <head commit> --version
<next release> --out <dir>`. Put each changed screen's before and after pictures in the page's "What it looks like"
section, with red numbered arrows on what changed (the skill's "Arrows"), and also attach `manifest.json` and every
picture it names to the task with `tico task attach <id> <file>`, one file per call. If the diff does not touch `ui/`,
delete that section, attach nothing and write "No screens changed" in the opening. If the tool cannot run on this
computer, attach nothing and say so under Not checked. Never draw a screen.

## 5. Finish

In a review stage: build, check, publish, look at and attach the page. `page.py build` and `page.py check --kind review`
(the skill's steps 3 and 4), then `tico page publish reports/reviews/<file>.html --task <id> --title "Review <1|2>: <the
change>"` prints the file id. Render the published page (`page.py render <file id> --kind review`) and look at it at
both widths; a broken page is fixed and published again, never attached. Then `tico task attach <id> page:<file id> --req "<requirement>" --version N`, using the requirement `tico task show`
names. The page is the whole hand-off: the stage moves on when it covers the newest commit, and every finding on the pull
request goes to the Builder through it, never to your Manager or a person as a separate task. Commit this repository.

Outside the pipeline: `tico task done <id> -m "..." --version N`: the pull request, the head commit, the number of
blocking issues, and what you did not check. Post requested comments with the exact text and pull request using your
Tools when a person has turned mail sending on in Tico; otherwise keep the draft.

## When you cannot tell

Say so as a `question:`. A guess written as a finding wastes an author's day. When the head moves after your review, read the new
commits, write the page again at the new commit, and attach it.
