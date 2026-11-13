# QA

Triggered when a task you own enters QA, after Fixes 2. The stage leaves when the checks pass at the head commit and
the QA page is attached: Jev checks it, then a person approves it, and approving it ships the change to production.

---

## 1. Build the page

Follow `skills/build-the-page/SKILL.md` with its `templates/review.html`, titled "QA", in `reports/<task id>-qa.html`,
at the head commit:

- The opening, two or three lines: what changed for the person who asked (what they can now do or see, and what was
  wrong before), then **Decide:** "Approve = ship to production, or send it back with what is wrong."
- What it looks like: before and after screenshots of each changed screen at phone and desktop width, both from the
  running app (Tico: `python3 scripts/release_shots.py --base <merge base> --head <head> --version <next> --out
  shots/`; Reviewer 2's shots when they are at the head), with red numbered arrows on what changed. Never a drawn
  screen. A change with no screen deletes this section and says "No screens changed" in the opening; the output that
  proves it (the command and its output, numbers before and after) goes under how it was checked.
- How it was checked: the tests added (named), the commands run and their real output, and what was not checked, with
  why. A line that only says it passed is not proof.
- In the details: the pull request link and the full head commit; the checks at that head (passing, with the link); what
  Reviewer 1 and Reviewer 2 found and what you did about each, one line each; any risk or follow-up to decide, or "None".

## 2. Build, look, publish and attach

    python3 skills/build-the-page/page.py build reports/<task id>-qa-draft.html --out reports/<task id>-qa.html
    python3 skills/build-the-page/page.py check reports/<task id>-qa.html --kind review
    tico page publish reports/<task id>-qa.html --task <id> --title "QA: <the change>"
    python3 skills/build-the-page/page.py render <file id> --kind review
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
