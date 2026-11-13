# QA

Triggered when a task you own enters QA, after Fixes 2. The stage leaves when the checks pass at the head commit and
the QA page is attached: Jev checks it, then a person approves it, and approving it ships the change to production.

---

## 1. Build the page

Follow `skills/build-the-qa-page/SKILL.md`: what changed, before and after screenshots of the running app (or the
output that proves a change with nothing to see), how it was checked with the checks at the head and what both reviewers
found, risks, and the pull request with its head commit. A page for an OK is HTML: Tico refuses a Markdown or
plain-text one.

## 2. Publish and attach

    tico page publish reports/<task id>-qa.html --task <id>
    tico task attach <id> page:<file id> --req "<requirement>" --version N

`tico page publish` prints the file id and embeds the screenshots saved beside the page. The requirement's name is the
one `tico task show` lists for QA.

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
