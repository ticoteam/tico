# Write the release report

Triggered when the release task for a batch asks for its report: the tasks that ship together wait on one release
task, and one person's OK on its one report starts the release. Budget 60 minutes. The outcome is one page,
`reports/releases/<name>.html`, on the release task, and one ask for that OK. Nothing is tagged or deployed before it.

Work across the batch from what each task already holds: its Spec page, its approved QA page and its pull request.
Never open a task's own session or thread to learn what it did. If its pages do not say, it is pulled (step 2), not
guessed into the report.

---

## 1. Read the batch

    tico task show <release task id>

The batch is the tasks the release task names (where it names none, every Engineering task waiting in Release for
that repository). For each, `tico task show <id>` lists its Spec page and QA page under "From earlier stages": read
both (`tico file read <file id>`) and its pull request (`gh pr view <n> -R <repo> --json title,body,mergeCommit,files,url`).
Note what each pull request body says a release needs: data changes, settings, flags, a deploy order.

## 2. Keep only what is ready

A task whose QA page was not approved at the commit that ships is pulled from the batch: say why under Pulled and on
its task. A task added or pulled after you ask is a new version of the report, and the OK starts over.

## 3. Check across the batch

Look for the same files or screens changed twice, one change undoing another, an order one needs (a back end before
the screen that calls it), and a data change that locks a large table or cannot be undone
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

## 5. Build, publish and ask once

    python3 skills/build-the-page/page.py build reports/releases/<name>-draft.html --out reports/releases/<name>.html
    python3 skills/build-the-page/page.py check reports/releases/<name>.html --kind release
    tico page publish reports/releases/<name>.html --task <release task id> --title "Release <name>"
    python3 skills/build-the-page/page.py render <file id> --kind release

Look at both pictures `render` names before anyone sees it. Where the release task has a page requirement, attach the
page to it (`tico task attach <release task id> page:<file id> --req "<requirement>" --version N`) and the stage asks
the person. Where it has none, ask once, showing the page:

    tico task ask <release task id> "Start release <name> at <commit>?" --to <owner> --kind ok --show page:<file id> -m "<the batch in a few words>" --version N

Never ask once per task: the one OK covers the batch.

## 6. After the OK

Follow the plan, step by step, and nothing else. A step that fails stops the release and goes to the owner as a
question; something that must be undone follows `playbooks/rollback.md`. Publishing the changelog where customers
read it is a person's step (AGENTS.md, Sending): the approved text is the text that goes out. Then update
`knowledge/versioning.md` and say on the release task what shipped, with the release link.
