# Write the release report

Work that is ready to ship is released in batches. Each task in a batch carries the batch's label; one release task
carries the batch's one report; one person's OK on that report starts the release. The outcome is one page,
`reports/releases/YYYY-MM-DD.N.html`, on the release task. Nothing is tagged or deployed before the OK.

Use this flow only where the release-ready stage has no automatic "in a release" requirement. Where it does (the
Engineering pipeline's Release stage waits for a published GitHub release), follow `playbooks/cut-a-release.md` as today
and label nothing.

Work across the batch from what each task already holds: its Spec page, its approved QA page and its pull request.
Never open a task's own session or thread to learn what it did. If its pages do not say, it is pulled (step 4), not
guessed into the report.

---

## 1. Cut the release

The release-ready stage is left by hand, so nothing wakes you when a task enters it. Cut on your release routine (for
example, once a day on release days), or when a person tells you to cut early:

    tico task list --pipeline <pipeline id> --stage "<release-ready stage>" --status open --brief

None: end the run. Otherwise name the release `YYYY-MM-DD.N` (N is 1, or one more than the releases already cut
today). For each task, read it (`tico task show <id>`): its QA page must carry a person's OK at the commit that ships.
If it does, label it:

    tico task label <id> --add release:YYYY-MM-DD.N --version N

If it does not, send it back with why: `tico task move <id> "QA" -m "<what is missing>" --version N`. Tasks that
reach the stage after this wait for the next cut.

## 2. Read the batch and check across it

For each task, `tico task show <id>` lists its Spec page and QA page under "From earlier stages": read both
(`tico file read <file id>`) and its pull request (`gh pr view <n> -R <repo> --json title,body,mergeCommit,files,url`).
Note what each pull request body says a release needs: data changes, settings, flags, a deploy order.

Then look across the batch for the same files or screens changed twice, one change undoing another, an order one
needs (a back end before the screen that calls it), and a data change that locks a large table or cannot be undone
(`skills/find-hidden-breaking-changes/SKILL.md`, `skills/judge-whether-a-release-can-roll-back/SKILL.md`). Write
"None found" only after checking.

## 3. Write the page

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

## 4. File the release task

The release task lives in the pipeline where releases wait for their OK (`<release pipeline id>`): its first stage
needs this report with a person's OK.

    tico task create --pipeline <release pipeline id> --owner <you> --title "Release YYYY-MM-DD.N" --label release:YYYY-MM-DD.N --body "<the tasks, one line each>"
    python3 skills/build-the-page/page.py build reports/releases/YYYY-MM-DD.N-draft.html --out reports/releases/YYYY-MM-DD.N.html
    python3 skills/build-the-page/page.py check reports/releases/YYYY-MM-DD.N.html --kind release
    tico page publish reports/releases/YYYY-MM-DD.N.html --task <release task id> --title "Release YYYY-MM-DD.N"
    python3 skills/build-the-page/page.py render <file id> --kind release
    tico task attach <release task id> page:<file id> --req "<requirement>" --version N

Look at both pictures `render` names before you attach. The requirement's name is the one `tico task show` lists;
attaching it asks the person for the OK, once for the whole batch: never ask on each task. The routine's run ends here.

**A task pulled after the cut** (its QA OK no longer holds, or a person asks): remove its label
(`--remove release:YYYY-MM-DD.N`), say why on that task, publish a new version of the report with it under Pulled, and
attach it again. An OK on an older version does not count.

## 5. After the OK

The OK moves the release task on by itself and wakes you on it: the release runs in this task's session, not the
routine's. Move the batch to the stage where it is deployed:

    python3 software/move_release.py release:YYYY-MM-DD.N "<deploy stage>" --except <release task id> -m "In release YYYY-MM-DD.N"

It prints what moved and anything that did not, with why. A task that did not move is fixed or pulled (step 4) before
anything deploys. Then follow the plan, step by step, and nothing else. A step that fails stops the release and goes to
the owner as a question; something that must be undone follows `playbooks/rollback.md`. Publishing the changelog where
customers read it is a person's step (AGENTS.md, Sending): the approved text is the text that goes out.

When it has shipped, move the batch on to done (or the stage where store submissions wait) the same way:

    python3 software/move_release.py release:YYYY-MM-DD.N done --except <release task id> -m "Shipped in release YYYY-MM-DD.N"

Then update `knowledge/versioning.md`, say on the release task what shipped, with the release link, and move it to done.
