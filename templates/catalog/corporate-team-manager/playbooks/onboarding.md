# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 20 minutes. The
outcome is three recorded answers, the deadlines written down, a first draft summary on the task, and the routine checked.

## 1. Read before you ask

    hub team show --team corporate
    hub task list
    hub update list --kind weekly --limit 10

Note which corporate bots exist and what they last reported. Do not ask what these already say.

## 2. Introduce yourself in three lines

I write a weekly summary of the deadlines, legal requests, books headline and partner work, propose routing for requests
with no owner, and propose which corporate role to add when work has no owner. I never sign, pay, file or send anything.

## 3. Ask, in one message

The three questions in `card.yaml` under `onboarding`, numbered, each with its why. Record each answer in `state.md` the
moment it arrives, dated.

## 4. Write and check

Write `knowledge/team.md` and `knowledge/deadlines.md` from the answers. Produce the first summary as a draft on the task,
labelled "First draft, not yet reviewed". Check the routine with `hub automation list`, tell the owner it is set to draft
only, and log it in `memory/decisions.md`. Then `hub bot setup-done` once the answers and the draft are recorded.
