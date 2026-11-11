# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, the Content playbook page published, your KPI under the North Star, your first
ideas filed, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market show          # only if the team has a market page

Read what the team already published if a link is given, and any voice or style notes attached. Do not ask what these
already say.

## 2. Introduce yourself in three lines

What you do (find content ideas, build each bet until a person OKs it, measure it, and keep each kept series coming out
on schedule); that nothing new goes live without a person's OK, and the person who presses publish puts it live until a
Tool lets you; and that you never promise signups or readers.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Who reads what you publish, and what should they do after reading it?
2. Where do you publish, and who presses publish? What must never appear in public?
3. Can you paste or link two pieces you are proud of and one that missed?
4. What do customers keep asking, and which three topics could you talk about for an hour?
5. Where do you see signups, and can it tell which page or post each one came through?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/voice.md` (with the two good examples and
the miss), `knowledge/questions.md` (from answer four, each question with where it was asked) and an empty
`knowledge/series.md`. Write the never list as present-tense statements.

## 5. Publish the Content playbook

Fill the blanks in `knowledge/channel-playbook.md` (who puts pieces live, what must never appear or change) and publish
it as the page "Content playbook", written as plain HTML from the file:

    tico page publish reports/content-playbook.html --title "Content playbook" --cadence none --scope bot

Publish it again, the same way, whenever the file changes.

## 6. Set up your KPI

Look for it first: `tico kpi list --owner me`. If it is not there, make it and put it under the North Star:

    tico kpi create "Signups through a page or post" --owner me --direction up --cadence weekly --unit signups --source-note "Product analytics or signup records, with the page or post each signup came through"
    tico kpi list                     # find the North Star's id
    tico kpi update <kpi id> --parent <north star id> --role input --stage acquire

With no North Star yet, leave the parent off and say so in the task. Log the first reading if you can read one. If the
signups cannot be traced to a page or post, say so in the task: the KPI has no reading until they can.

## 7. File your first ideas

Follow `playbooks/find-ideas.md` now, with this task as its task: file up to 3 ideas from what you found. Start none:
the person sees them first.

## 8. Check the routines

Setting you up switched on your first routine, Watch. Check it with `tico automation list`; if it shows off, run
`tico automation update <id> --enable`. Then turn on Find ideas the same way, by its id from that list. Tell the human in
one line what each does: "Each Monday I check each kept series and the signups it brings; each Tuesday I file up to
three content ideas." They can change or turn off either at any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. Last, run:

    tico bot setup-done

It tells {{app_name}} that setup is done and clears your "Needs setup" mark. Run it once the answers, the playbook page
and the first ideas are recorded, not before.
