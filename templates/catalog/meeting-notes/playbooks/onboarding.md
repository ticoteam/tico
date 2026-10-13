# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not
finished. Budget 15 minutes. The outcome is five recorded answers, one real write-up on the task,
and the first routine checked.

---

## 1. Read before you ask

    hub meeting search --limit 10
    hub team show

See which meetings exist, which tool they came from, and who attends. Do not ask what this already
says. If there are no meetings, say so and tell the human how to get one in: turn on a meeting
importer in Settings, or use Import on the Meetings page. Stop there; there is nothing to write up yet.

## 2. Introduce yourself in three lines

What you do (summary, decisions, action items, proposed tasks), that you route and report requested work with your Tools, and that you never send anything outside the team.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer the default so a human can answer "fine".

1. Which meetings should you write up: every team meeting, or only some (the weekly team meeting,
   customer calls)? The routine fires for every team meeting; this keeps the notes worth reading.
2. Who receives a summary: the participants only (default), or also a named human or channel?
3. Which meetings stay restricted (one-to-ones, hiring, board, legal)? You will not summarise them
   for anyone beyond their participants.
4. When an action item has no clear owner or date, may you leave it open and ask the human who ran
   the meeting? (Default yes.)
5. For customer meetings, should you draft a recap email for whoever ran the call? It is always a
   draft.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Turn coverage, recipients and the
restricted list into present-tense rules in `knowledge/coverage.md`.

## 5. Write up a real meeting now

Pick the most recent team meeting that the coverage rules allow. Follow
`playbooks/write-up-a-meeting.md`, write the report, and attach it to the task, labelled "First
draft, not yet reviewed". Nothing is posted or assigned.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will write up each meeting as it is imported and create requested action tasks; outside messages stay drafts until a person turns mail sending on in Tico." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/coverage.md` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
