# Show what was built

Triggered in Fixes 2, after the fixes are pushed. The stage leaves when the checks pass and a page showing what was
built is attached: Jev checks it, then a person OKs it. The person reads this page, not the diff, so it must let them say
yes or no in a minute.

---

## 1. Write the page

`reports/<task id>-built.html`, at the head commit you pushed. A page for an OK is HTML: Tico refuses a Markdown or
plain-text one. Keep it short and readable, with simple inline CSS (a `<style>` block, Tico's colour tokens such as
`var(--ink)` and `var(--line)`) and nothing from elsewhere: no script, no outside image, font or stylesheet. The spec
page skill (`build-the-spec-page`, the Product Owner's) is the model for its look.

- At the top, two or three lines: what this page is (the change, for which task) and what the person decides (OK it,
  or send it back with what to fix), with the pull request link and the head commit.
- What changed, for the person who asked: what they can now do or see, in two or three sentences.
- How it was checked: the tests added, the commands run and their results, and what was not checked.
- The checks at the head commit: passing, with the link.
- What it looks like: real screenshots as images where there is anything to see (`<img src="<task id>-shots/x.png">`,
  saved next to the page in the checkout: publishing embeds them), or the command output in a `<pre>`.
- What Reviewer 1 and Reviewer 2 found, and what you did about each finding, one line each. Both Review pages and your
  Fixes 1 note are under "From earlier stages" in `tico task show`.
- Anything the person should decide: a risk, a follow-up, a path that needs a named person.

## 2. Publish and attach

    tico page publish reports/<task id>-built.html --task <id>
    tico task attach <id> page:<file id> --req "<requirement>" --version N

`tico page publish` prints the file id. The requirement's name is the one `tico task show` lists for Fixes 2.

## 3. When it comes back

- **Jev sent it back:** the reasons are in `tico task show`. Fix the page (or the change), publish again and attach the
  new version.
- **The person sent it back:** their notes are on the task. Fix what they asked, push, publish the page again and attach
  it. A commit pushed here was not reviewed by Reviewer 1 or 2: say so on the page, with the new head. Tico says it on
  the person's ask too.
- **Jev is unsure, or you want the person's OK:** attach the new version; Tico asks the person. Never ask your own OK
  of this page, or a yes/no that shows it ("OK to merge?"): Tico refuses it, and an answer to it passes nothing.
- **New commits after the OK:** the page is for one head. Publish a new version for the new head and attach it.
