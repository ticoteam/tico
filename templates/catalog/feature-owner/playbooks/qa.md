# QA

Triggered when a task you own enters QA, after Fixes 2. The stage leaves when the checks pass at the head commit and
the QA page is attached: Jev checks it, then it is approved (`tico task show` says by whom). In OK release a person
then OKs the release from the same page, and that ships the change to production.

---

## 1. Run the checks

The bar: a person who reads the page is confident enough in your QA to skip their own. They see what was checked, how,
and the proof, without running anything.

1. The plan is the spec's acceptance criteria (`tico task show` lists the Spec page under "From earlier stages"): one
   row each, with its number. Add any check the change needs that the spec did not name (another screen that uses the
   same code, a permission, a data change on a copy), and say why.
2. Run each one at the head commit, where it really runs: the repository's demo or a preview for a screen (Tico:
   `python3 scripts/release_shots.py --base <merge base> --head <head> --version <next> --out shots/`; Reviewer 2's
   shots when they are at the head), the tests for logic (name the ones that fail on the old code), the real call or
   job for a change with no screen.
3. Each check gets a result: pass, fail, or not checked with why. A pass has its proof on the page: a screenshot with
   red numbered arrows at 390 and 1280 px, or the command as run and its real output (test names and counts, the call
   and its response, the query and its rows). "Looks good" is not proof, and a check with none is not checked.
4. A check that fails is yours to fix before the page goes out: fix, push, and run the checks it touches again
   (Reviewer 2 reads the new commit before the person is asked). Keep what you found and fixed under the findings. Anything that does not block the change becomes its own task, linked.

## 2. Build the page

Follow `skills/build-the-page/SKILL.md` with its `templates/qa.html`, in `reports/<task id>-qa.html`, at the head
commit:

- The opening: what changed for the person who asked and the result ("Passed: 5 of 5 checks"), then what was not
  checked, plainly, then **Decide:** "Approve = ship it, or send it back with what is wrong."
- Plan and results: the table, one row per check, each with its proof's letter or arrow.
- Proof: the screenshots and the output, lettered to match the table. A change with no screen has no pictures; say "No
  screens changed" in the opening.
- Findings: what failed and was fixed in this round, and the follow-ups filed, each with what was done, what happened,
  what was expected and `file:line`. Or "None."
- In the details: where it ran (the demo or preview, the pull request link and the full head commit, the checks at that
  head with the link), what Reviewer 1 and Reviewer 2 each found on the commit they read and what you did about each,
  and what was not checked, with why and what would check it.

A check only a person can do (a physical device, a reply someone owes) goes under not checked with why. The page never
asks its reader to test.

    python3 skills/build-the-page/page.py build reports/<task id>-qa-draft.html --out reports/<task id>-qa.html
    python3 skills/build-the-page/page.py check reports/<task id>-qa.html --kind qa
    tico page publish reports/<task id>-qa.html --task <id> --title "QA: <the change>"
    python3 skills/build-the-page/page.py render <file id> --kind qa
    tico task attach <id> page:<file id> --req "<requirement>" --version N

`tico page publish` prints the file id. Look at both pictures `render` names before you attach: a page that does not
draw, or has a broken picture, is fixed and published again, never attached. The requirement's name is the one
`tico task show` lists for QA. Then end the turn: Jev checks it, then the stage asks a person to approve it.

## 3. When it comes back

- **Jev sent it back:** the reasons are in `tico task show`. Fix the page (or the change), publish again and attach the
  new version.
- **The person sent it back:** their notes are on the task. Fix what they asked, push, publish the page again and attach
  it. Reviewer 2 reads a commit pushed after its review before the person is asked again: say so on the page, with the
  new head.
- **Jev is unsure, or you want the person's OK:** attach the new version; Tico asks the person. Never ask your own OK
  of this page, or a yes/no that shows it ("OK to merge?"): Tico refuses it, and an answer to it passes nothing.
- **New commits after the OK:** the page is for one head. A rebase that changes the same lines keeps the OK; other new
  commits are read by Reviewer 2, and the person is asked again only after it sends them back. Publish a new version
  for the new head and attach it.
- **Sent back from Merge:** the reason names what failed in the merge queue. Fix it on the same branch, push, and attach
  a new page for the new head.
