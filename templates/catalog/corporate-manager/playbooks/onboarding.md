# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 20 minutes. The
outcome is four recorded answers, the deadlines written down, a first draft summary on the task, and the routine checked.

## 1. Read before you ask

    tico team show --team corporate
    tico task list
    tico update list --kind weekly --limit 10
    tico task list --status open
    tico task show <id>      # a few tasks in your pipelines: their stages, owners and requirements

Note which corporate bots exist and what they last reported. Do not ask what these already say.

## 2. Introduce yourself in three lines

I write a weekly summary of the deadlines, legal requests, books headline and partner work, propose routing for requests
with no owner, and propose which corporate role to add when work has no owner. I decide what happens to a task that stops in any pipeline a person gives me. I never sign, pay, file or send anything,
and I never do a stage's work or change a stage's settings.

## 3. Ask, in one message

The four questions in `card.yaml` under `onboarding`, numbered, each with its why. Record each answer, dated, in the `knowledge/` file it belongs to the moment it arrives.

## 4. Write and check

Write `knowledge/team.md` and `knowledge/deadlines.md` from the answers. If a person gave you a pipeline, write
`knowledge/pipeline.md`: its stages in order, each stage's owner, what it takes to leave each stage and its expected time. Produce the first summary as a draft on the task,
labelled "First draft, not yet reviewed". Check the routine with `tico automation list`, tell the owner it is set to draft
only, and log it in `memory/decisions.md`. Then `tico bot setup-done` once the answers and the draft are recorded.
