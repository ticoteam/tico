---
name: build-the-qa-page
description: Build the QA page for an Engineering task in QA, as one HTML page with real before and after screenshots (or concrete proof when nothing changes on screen), how it was checked and what both reviewers found, publish it on the task and attach it to the QA page requirement, which Jev checks and a person approves to ship. Read when a task you own enters QA.
---

# Build the QA page

The QA page is how a person decides whether the change ships. They read the page, not the diff, so it must let them
say yes or no in a minute. Approving it sends the change to production. The QA stage's requirement holds it: Jev checks
it for proof, then a person approves it; nothing else does.

`template.html` beside this file is a worked example: copy it and replace every line.

## Page order

1. **Top lines.** The change's name as the title, then two or three lines on what changed, for the person who asked:
   what they can now do or see, and what was wrong before. Then one line: "Approve = ship to production."
2. **Before and after.** Real screenshots of the running app, before and after, side by side, each with a one-line
   caption that says what to look at. One pair per screen that changed. When nothing changes on screen, the proof
   instead: the command and its output, a log line, numbers before and after, in a `<pre>`. A line that only says
   it passed is not proof.
3. **How it was checked.** The tests added (named), the commands run with their counts, the checks at the head commit
   with a link to them, and what was not checked, with why. Then what Reviewer 1 and Reviewer 2 found and what you did
   about each finding, one line each.
4. **Risks or anything to decide.** A risk, a follow-up, a path that needs a named person (`knowledge/standards.md`
   says which). "None" when there is none.
5. **At the bottom:** the pull request link and the full head commit.

## Screenshots

- Real ones only: the app running at the head commit, and at the base for "before". Never a mockup or a drawing.
- Where the repository has a screenshot tool, use it (`knowledge/standards.md` names it; in Tico's own repository it is
  `scripts/release_shots.py`). Reviewer 2 may have attached before and after shots to the task already: use them when
  they are at the head commit.
- Save them beside the page, in the checkout (`reports/<task id>-shots/`), and name them in the page with a relative
  path: `<img src="<task id>-shots/export-after.png" alt="...">`. `tico page publish` embeds every image the page names
  from its own folder. An outside image address is removed when the page is stored, so it would show as nothing.
- Crop to what changed. PNG or JPEG, and the whole page under 10 MB.

## What the page may not do

Scripts are removed. Nothing loads from outside: no outside images, fonts, frames or stylesheets. Links are fine.
The page is not styled by Tico: keep the template's own colours, which follow the reader's light or dark theme.
At phone width it keeps a 16px gutter and has no horizontal scroll; the before/after pair stacks.

## Steps

1. `tico task show <id>`: the pull request, the head commit, both Review pages and what was said at each done are
   under "From earlier stages". Read them with `tico file read <file id>`.
2. Confirm the checks pass at the head (`skills/get-the-checks-green-at-the-head/SKILL.md`).
3. Take the screenshots, or collect the output that proves the change.
4. Write `reports/<task id>-qa.html` in the page order above, from `template.html`.
5. Check it renders: open it in a browser on your computer, at desktop and at phone width. Every image shows, nothing
   is cut off, and the head commit on it is the pull request's head now.
6. Publish it on the task: `tico page publish reports/<task id>-qa.html --task <id>`. It prints the page's file id.
7. Attach it to the requirement `tico task show` names:

       tico task attach <id> page:<file id> --req "<requirement>" --version N

   Then end the turn. Jev checks it, then the stage asks a person to approve it; never ask your own OK of it.

## Done

The QA page requirement holds the page at the head commit. When it comes back, `playbooks/qa.md` says what to do.
