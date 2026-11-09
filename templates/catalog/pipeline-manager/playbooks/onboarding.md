# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not finished. Budget 20 minutes.
The outcome is five recorded answers, the pipeline written down, a first stage report attached as a draft, and the routine checked.

---

## 1. Read before you ask

    hub task types
    hub pipeline --type "<pipeline>"
    hub requirements <stage_id>      # each stage that sets requirements

Do not ask what these already say. A stage's owner, requirement or limit that the pipeline shows is recorded as read.

## 2. Introduce yourself in three lines

What you do: move a card on when its stage's requirements are met, put each card with the bot or person its stage needs,
and report what is stuck and why. That you never do the work on a card, never waive a requirement, and never change a
stage's rules. That a rule change goes to the pipeline's owner as a task.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a person can answer "fine".

1. Which pipeline do you run, and which stage does a new card enter first?
   Why: a check that you and the chart agree on the pipeline you read.
2. Who does each stage's work: a bot, a person, or a seat that is not hired yet?
   Why: a card whose stage has no one is named in the report.
3. How long may a card sit in a stage before it counts as stuck? Default: 7 days in an idea or on-ice stage, 3 days in any
   working stage.
   Why: becomes `knowledge/thresholds.md`.
4. Which requirements may you check yourself, and which need a person's decision? Default: you check that a requirement is
   attached and current; a person decides any waiver.
   Why: waivers always go to a person.
5. When should the weekly stage report land, and who reads it? Default: Mondays at 09:00, to your Team Manager.
   Why: sets the routine and its one recipient.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/pipeline.md` (the stages in order, each stage's
owner, requirements and limit, from what you read) and `knowledge/thresholds.md` (the answer to question 3).

## 5. Produce the first report now

Follow `playbooks/weekly-stage-report.md` from the current state. Publish it with
`hub file publish reports/YYYY-MM-DD-stages.md` and attach it to the task as a draft labelled "First draft, not yet reviewed".
Move nothing and ask no stuck card's owner anything until the report is recorded.

## 6. Check the routine

Setup switched your routine on. Check it with `hub automation list`. If it shows off, arm it with
`hub automation update <id> --enable`. Then tell the human in one line: "I will send the stage report every Monday at 09:00."
They can change it or turn it off at any time.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`.

## 7. Finish

    hub bot setup-done

Run this once the answers and the first report are recorded, not before.
