# Show what was built

Triggered in Fixes 2, after the fixes are pushed. The stage leaves when the checks pass and a page showing what was
built is attached: Jev checks it, then a person OKs it. The person reads this page, not the diff, so it must let them say
yes or no in a minute.

---

## 1. Write the page

`reports/<task id>-built.md`, at the head commit you pushed:

- First line: the pull request link and the head commit.
- What changed, for the person who asked: what they can now do or see, in two or three sentences.
- How it was checked: the tests added, the commands run and their results, and what was not checked.
- The checks at the head commit: passing, with the link.
- What it looks like: a screenshot, a short recording or the command output, where there is anything to see.
- Reviewer 1's and Reviewer 2's verdicts, and what you did about each finding, one line each.
- Anything the person should decide: a risk, a follow-up, a path that needs a named person.

## 2. Publish and attach

    tico file publish reports/<task id>-built.md --task <id>
    tico task attach <id> page:<file id> --req "<requirement>" --version N

`tico file publish` prints the file id. The requirement's name is the one `tico task show` lists for Fixes 2.

## 3. When it comes back

- **Jev sent it back:** the reasons are in `tico task show`. Fix the page (or the change), publish again and attach the
  new version.
- **The person sent it back:** their notes are on the task. Fix what they asked, push, publish the page again and attach
  it.
- **New commits after the OK:** the page is for one head. Publish a new version for the new head and attach it.
