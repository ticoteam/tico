# Review a pull request

Triggered by a task naming one pull request, and used for each pull request in
`playbooks/weekday-review-queue.md`. Budget 15 minutes. The outcome is one draft review on the task that a
human can post with one edit.

---

## 1. Read the change in order

    gh pr view <n> -R <repo> --comments
    gh pr diff <n> -R <repo>
    gh pr checks <n> -R <repo>

1. The description: what it says it does, and the issue it links. If it says nothing, that is the first finding.
2. The design: should this change exist, and does it fit the code around it? Ask this before any line.
3. The files and tests: are the tests changed with the code, and do they test behaviour, not lines?
4. The details: naming, comments, error handling, complexity, a migration or a config change, a new dependency.
5. Checks: what passed, what failed, what is pending, and whether a failure is this change's.

## 2. Check against the team's standard

Walk `knowledge/standards.md` item by item. Note each item as met, not met, or not checkable from the diff.
Look for a pattern from `knowledge/patterns.md`. A secret, a removed check or a removed test is blocking, and
is reported to a human at once by task.

## 3. Size

If the change is over the team's line, or mixes a refactor with a feature, draft one polite request to split
it, naming a suggested seam. Review what you can regardless.

## 4. Draft the review

On the task, in the comment style chosen during setup (default labelled):
- **Summary**, two lines: what the change does and whether anything blocks it.
- `issue (blocking):` each thing that must change before merge, with file, line, the quoted code and why.
- `question:` what you could not tell from the diff.
- `suggestion:` an improvement with a short example.
- `nit:` preferences, at most five, never blocking.
- `praise:` one specific line, when earned.
- **Not checked:** tests you did not run, files skipped, pending checks.

Every comment is polite, about the code, and specific. Never "this is wrong": say what happens and why.

## 5. Finish

You post nothing. Commit, then `hub task update <id> --status done --note`: the pull request, the number of
blocking issues, and what you did not check. Post requested comments with the exact text and pull request using your Tools when a person has turned mail sending on in Tico; otherwise keep the draft.

## When you cannot tell

Say so as a `question:`. A guess written as a finding wastes an author's day.
