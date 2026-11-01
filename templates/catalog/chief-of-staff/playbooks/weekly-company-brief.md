# Weekly team brief

Schedule: Fridays at 15:00 team time (routine `weekly-company-brief`), after setup. Also run by hand on request. Budget 25 minutes. The outcome is one page for the
owner. Nothing is sent to anyone else.

---

## 1. Read what was decided last time

    hub task show <id>

Then `knowledge/rhythm.md` (recipient, thresholds, exclusions) and `knowledge/open-loops.md`. If
`reports/` already holds this week's brief, update it; do not write a second.

## 2. Read the team, once each

    hub goal list --all
    hub goal show <id>                     # each goal that is red, yellow, or has no reading
    hub update list --kind weekly              # the bots' week in review
    hub task list --status open --status doing --status waiting
    hub meeting search --since <last Friday>

Read only what changed since last week's brief. Team meetings only; you cannot see private ones,
and you never guess at them. If a source refuses you, note which one and carry on.

## 3. Sort into five things

| Section | What belongs in it |
|---|---|
| Needs you | Approvals, questions and tasks waiting on the owner, oldest first, with the age |
| Goals | One line per goal: colour, movement since last week, the evidence, next step and its owner |
| Stalled | A goal or task with no change past the threshold in `knowledge/rhythm.md` |
| What moved | Shipped, decided or closed this week, from tasks, updates and meetings |
| Watch | Anything trending the wrong way that is not yet stalled |

A goal with no reading this week says "no reading". Never colour a goal yourself; propose a colour
and the evidence, and leave the choice to its owner.

## 4. Chase what is stalled

For each stalled item find who owns the next step (`hub goal show`, `hub task show`). Then:
1. Check `knowledge/open-loops.md`. If you already asked this week, do not ask again; say so.
2. Draft one nudge of one or two sentences that names the item, what has been quiet and for how long,
   and the specific thing you need. Put it in the brief under the item.
3. Send a nudge only when the task asks for it and a granted Tool allows it (`hub message send --fyi <person> "..."`),
   and within the limit of ten unsolicited messages to a human a day. Otherwise it stays a draft under the item.
4. Add the loop to `knowledge/open-loops.md` with today's date.

## 5. Draft Monday's agenda

Follow `playbooks/monday-agenda.md` and place it under the brief as the last section.

## 6. Write, publish, tell

Write `reports/YYYY-MM-DD-weekly-brief.md` in the shape of `knowledge/examples/weekly-brief.md`: one
page, headline first, every line cited. End with "Could not read" naming any blocked source.

    hub file publish reports/YYYY-MM-DD-weekly-brief.md

Once the routine is armed, tell the owner with `hub message send --fyi <owner> "<one line and the link>"`.
Before it is armed, attach the report to the task and say it is a draft.

## 7. Finish

Commit, then `hub task update <id> --status done --note` with the headline, how many goals are
red, yellow and green, how many items are stalled, and the report path. Always finish it: a
routine task left open absorbs next Friday's.

## When a source fails

Name the source and what is therefore unknown, keep going, and say it in the brief. A brief that read
three sources out of five and looks complete is worse than a short one that says which two are missing.
