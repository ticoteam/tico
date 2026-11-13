# Write the release report

Work that is ready to ship is released in batches. Each task in a batch carries the batch's label; one release task
carries the batch's one report; one person's OK on that report starts the release. The outcome is one page,
`reports/releases/YYYY-MM-DD.N.html`, on the release task. Nothing is tagged or deployed before the OK.

Work across the batch from what each task already holds: its Spec page, its approved QA page and its pull request.
Never open a task's own session or thread to learn what it did. If its pages do not say, it is pulled (step 5), not
guessed into the report.

---

## 1. A task becomes ready to release

Use this flow only where the release-ready stage has no automatic "in a release" requirement. Where it does (the
Engineering pipeline's Release stage waits for a published GitHub release), follow `playbooks/cut-a-release.md` as today
and label nothing.

You are woken when a task enters the stage where work waits to be released. Read it (`tico task show <id>`): its QA
page must carry a person's OK at the commit that ships. If it does, label it for the next batch:

    tico task label <id> --add release:next --version N

If it does not, send it back with why: `tico task move <id> "QA" -m "<what is missing>" --version N`.

## 2. Cut the release

On your release routine (for example, once a day on release days):

    tico task list --label release:next --status open --brief

None: end the run. Otherwise name the release `YYYY-MM-DD.N` (N is 1, or one more than the releases already cut
today) and move each task from the next batch to this one:

    tico task label <id> --remove release:next --add release:YYYY-MM-DD.N --version N

Tasks that become ready after this wait for the next cut.

## 3. Read the batch and check across it

For each task, `tico task show <id>` lists its Spec page and QA page under "From earlier stages": read both
(`tico file read <file id>`) and its pull request (`gh pr view <n> -R <repo> --json title,body,mergeCommit,files,url`).
Note what each pull request body says a release needs: data changes, settings, flags, a deploy order.

Then look across the batch for the same files or screens changed twice, one change undoing another, an order one
needs (a back end before the screen that calls it), and a data change that locks a large table or cannot be undone
(`skills/find-hidden-breaking-changes/SKILL.md`, `skills/judge-whether-a-release-can-roll-back/SKILL.md`). Write
"None found" only after checking.

## 4. Write the page

Follow `skills/build-the-page/SKILL.md` with its `templates/release.html`, top to bottom:

1. **Changelog.** Written for customers, good enough to publish as the company's changelog as it is. One entry per
   change people will notice: its kind (New, Improved, Fixed), a short plain headline, one or two sentences on what
   they can now do or what no longer goes wrong (`skills/write-release-notes-for-users/SKILL.md`), and the after
   picture from its QA page. The change people will like most comes first. No task or pull request numbers, no
   internal names, no jargon. Changes nobody will notice go in one closing line: "Plus 3 fixes behind the scenes."
2. **Why we're confident.** Short: each task's QA page and its approval, what was checked live, anything unusual.
3. **Plan.** Numbered steps, in order, each runnable as written: the release itself, data changes, settings, deploys,
   store submissions, and the checks after it. The release follows this list and nothing else.
4. **Biggest risks.** At most three, most likely first. Each says what could go wrong, how we would notice (the alert,
   the log line, the number), and how we would undo it.

Below the line: the tasks (title, repository, pull request, QA page version), what you checked across them, what is
watched after the release and for how long, and what was pulled. The opening names the batch, its state and the
suggested version with its reason (`knowledge/versioning.md`); a person records the version.

## 5. File the release task and ask once

    tico task create --owner <you> --title "Release YYYY-MM-DD.N" --label release:YYYY-MM-DD.N --body "<the tasks, one line each>"
    python3 skills/build-the-page/page.py build reports/releases/YYYY-MM-DD.N-draft.html --out reports/releases/YYYY-MM-DD.N.html
    python3 skills/build-the-page/page.py check reports/releases/YYYY-MM-DD.N.html --kind release
    tico page publish reports/releases/YYYY-MM-DD.N.html --task <release task id> --title "Release YYYY-MM-DD.N"
    python3 skills/build-the-page/page.py render <file id> --kind release

Look at both pictures `render` names before anyone sees the page. Then ask the person once, on the release task,
showing it:

    tico task ask <release task id> "Start release YYYY-MM-DD.N?" --to <owner> --kind ok --show page:<file id> -m "<the batch in a few words>" --version N

One OK covers the batch: never ask on each task. **A task pulled after the cut** (its QA OK no longer holds, or a
person asks): remove its label (`--remove release:YYYY-MM-DD.N`), say why on that task, publish a new version of the
report with it under Pulled, and ask for the OK again. An OK on an older version does not count.

## 6. After the OK

Move every task labelled `release:YYYY-MM-DD.N` on to its next stage
(`tico task move <id> next -m "In release YYYY-MM-DD.N" --version N`), then follow the plan, step by step, and nothing
else. A step that fails stops the release and goes to the owner as a question; something that must be undone follows
`playbooks/rollback.md`. Publishing the changelog where customers read it is a person's step (AGENTS.md, Sending): the
approved text is the text that goes out. Then update `knowledge/versioning.md`, say on the release task what shipped,
with the release link, and close it.
