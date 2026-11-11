# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 30 minutes.
The outcome is five recorded answers, the CRO playbook page published, your KPI under the North Star, your first ideas
filed, and both routines checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market segment tree     # the segments, for the segment question
    tico goal list               # any conversion target already set; do not ask again what it says
    tico market show             # only if the team has a market page

Look at the conversion pages as a visitor sees them, on a phone and on a computer: the landing pages, pricing and the
signup form. Look at what you can already reach: the analytics Tool, or an export the human attached. Do not ask what
these already say.

## 2. Introduce yourself in three lines

What you do (find CRO ideas, build each page change or test until a person OKs it, measure it, and watch what worked);
that nothing goes live without a person's OK, and a page change is applied by them until a Tool lets you; and that you
never read a test before its planned end or promise a lift.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which pages do visitors pass through on the way to a signup? Give me the address of each.
2. Can you connect your product analytics, and roughly how many visitors does each page get a week?
3. Which segments do these pages serve, and which matters most this quarter? Default: every segment, equal weight.
4. What must never be tested or changed? Default: prices shown to existing customers, legal text, checkout.
5. Who edits each page, and who runs tests in your testing tool?

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/funnel.md` (the steps, their pages and
their count sources) and `knowledge/pages.md` (each page, who puts a change live, what must never be tested there, and
the date you last checked it as a visitor). Keep the segment list and the never-test list fixed from here on: a change
to either is a proposal, not an edit.

From the weekly visitor counts, note in `knowledge/pages.md` whether a test on each page could finish in 4 weeks
(`skills/size-a-test-before-it-starts/SKILL.md`). Where it could not, your bets there are known fixes measured before
and after, and you say so now.

## 5. Publish the CRO playbook

Fill the blanks in `knowledge/channel-playbook.md` (who puts changes live, what must never change or be tested) and
publish it as the page "CRO playbook", written as plain HTML from the file:

    tico page publish reports/cro-playbook.html --title "CRO playbook" --cadence none --scope bot

Publish it again, the same way, whenever the file changes.

## 6. Set up your KPI

Look for it first: `tico kpi list --owner me`. If it is not there, make it and put it under the North Star:

    tico kpi create "Visits that become signups" --owner me --direction up --cadence weekly --unit percent --source-note "Product analytics: signups divided by visits, same week, same source"
    tico kpi list                     # find the North Star's id
    tico kpi update <kpi id> --parent <north star id> --role input --stage acquire

With no North Star yet, leave the parent off and say so in the task. Log the first reading if you can read one.

## 7. File your first ideas

Follow `playbooks/find-ideas.md` now, with this task as its task: file up to 3 ideas from what you found. Start none:
the person sees them first.

## 8. Check the routines

Setting you up switched on your first routine, Watch. Check it with `tico automation list`; if it shows off, run
`tico automation update <id> --enable`. Then turn on Find ideas the same way, by its id from that list. Tell the human in
one line what each does: "Each morning I check every step of what we kept; each Tuesday I file up to three CRO ideas."
They can change or turn off either at any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. Last, run:

    tico bot setup-done

It tells {{app_name}} that setup is done and clears your "Needs setup" mark. Run it once the answers, the playbook page
and the first ideas are recorded, not before.
