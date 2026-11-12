# Build a bet

Your task is in Growth Experiments' Building stage. Budget: one or two runs.

Outcome: the real thing made but not live (the page change, the post, the email, the ad), the brand check passed, and a
Go-live page attached. Jev checks the page, then a person OKs it. Nothing goes live in this stage.

The same text is shipped to every channel Owner. What you may make and who applies it are in
`knowledge/channel-playbook.md`.

---

## 1. Read the idea

    tico task show <id>

The idea is one line: what to try and why. Read its source again. If it no longer holds (the number moved, the page
changed), close the task with the reason and stop:

    tico task close <id> -m "<why it no longer holds>" --version N

## 2. Make the real thing

Make it exactly as it will go live: the new title and copy, the post, the email, the ad and its audience. Keep the
drafts in this repository and attach them to the task. Work another teammate does is a sub-task, and the task stays
yours: writing to the Content Owner, design to the Design Owner.

    tico task file "<what you need>" --parent <id> --owner <their slug> --body "<what, by when, and where it goes>"

## 3. Pass the brand check

Run `tico brand show`. When no brand card is published yet, there is nothing to check against: file no brand check,
and say so on the Go-live page (step 4).

Otherwise run `tico brand check -` on every outward word and fix each hit. Check each claim against
`tico brand show --section claims`. Then ask the Design Owner for the brand check:

    tico task file "Brand check: <title>" --parent <id> --owner design-owner --body "<what goes live, where, and the links to the drafts>"

The task waits on it. Fix what it sends back. Write that the brand check passed only once the Design Owner's task says
so.

## 4. Write the Go-live page

One HTML page a person can read in two minutes (Tico refuses a Markdown page for an OK), with simple inline CSS and
nothing from elsewhere. At the top, two or three lines say what goes live and what the person decides. Then it shows:

- **The thing as it will look**: the copy or a picture of it, and where it goes live.
- **The metric** it moves (your KPI or the number under it) and where that number is read.
- **The prediction, as a number**: from what to what, by when. "Organic clicks on the pricing pages from 410 to 520 a
  week by Dec 15", not "more clicks".
- **The cost**: money, and a person's time if someone must apply it.
- **How long it runs**, with its end date.
- **When to stop early**: the number or event that stops it.
- **Brand**: how it fits the brand card, and "Brand check: passed" with the Design Owner's task. With no brand card
  published: "No brand card is published yet, so there was no brand check."
- **Who puts it live**: you, or the person your playbook names when no Tool lets you.

Publish it on the task and attach it to the requirement:

    tico page publish reports/<task>-go-live.html --title "Go-live: <title>" --task <id>
    tico task attach <id> page:<file id> --req "Go-live page" --version N

## 5. Wait for the OK

Jev checks the page, then a person OKs it. Only a person can. When the person sends it back, their notes are on the
task: change the thing or the page, publish again and attach the new version. Once OK'd, the task moves to Running by
itself, and `playbooks/run-and-measure.md` takes over.
