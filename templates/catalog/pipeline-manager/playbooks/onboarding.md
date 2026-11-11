# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 20 minutes.
The outcome is four recorded answers, your pipelines written down, a first pipeline report attached as a draft, and the
routine checked.

---

## 1. Read before you ask

    tico team show
    tico task list --status open
    tico task show <id>      # a few tasks in your pipelines: their stages, owners and requirements

Your pipelines are the ones you were given. Do not ask what these already show.

## 2. Introduce yourself in three lines

What you do: when a task in your pipelines stops (its owner couldn't move it, it is Late, or it has no owner), you decide
what happens to it: reassign, move back, move on, snooze to a date or close, with one reason. What you never do: a
stage's work, or a change to a stage's settings. A change you think is needed goes to your manager as a proposal on a task.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which pipelines do I manage? Default: the ones Tico gave me.
   Why: every decision is scoped to them.
2. Who should I ask when only a person can decide, and is there anyone whose requests I must never close? Default: the
   first person above me on the org chart.
   Why: a task a person asked for is theirs to close.
3. Is there a stage whose owner is not hired yet, or one you expect to be slow on purpose?
   Why: a stage with nobody is a hiring question for my manager, not a stuck task.
4. When should the weekly pipeline report land, and who reads it? Default: Mondays at 09:00, to my manager.
   Why: sets the routine and its one recipient.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/pipeline.md`: each pipeline's name, its
stages in order, each stage's owner, what it takes to leave each stage, its limit and its expected time, from what you read.

## 5. Produce the first report now

Follow `playbooks/weekly-pipeline-report.md` from the current state. Publish it with
`tico file publish reports/YYYY-MM-DD-pipeline.md --task <id>` and label it "First draft, not yet reviewed".
Decide nothing until the report is recorded.

## 6. Check the routine

Setup switched your routine on. Check it with `tico automation list`. If it shows off, turn it on with
`tico automation update <id> --enable`. Then tell the human in one line: "I will send the pipeline report every Monday
at 09:00." They can change it or turn it off at any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`.

## 7. Finish

    tico bot setup-done

Run this once the answers and the first report are recorded, not before.
