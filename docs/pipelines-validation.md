# Pipelines: approval checklist

What has to be true before the pipeline engine (stages, requirements, judges, review mode, managers, flow map) is approved
for customers. Each item has a way to check it. Nothing here is a feature flag: a pipeline with no stage requirements behaves
as it did before, so rolling back a stage is retiring its requirement rows.

## 1. Automated bars

- End to end: `python3 -m pytest -q -n 2 backend/tests/test_pipeline_e2e.py` has no xfail left. Every scenario passes:
  Product to Engineering and back, Marketing to Sales, missing and stale evidence block, an unsure judge goes to a person,
  a person who is away hands to the backup, Warn after then one ask per visit then the Factory Manager's bottleneck, a stopped
  manager opens one BotOps task, a bot that never answers is Late, a PR ejected from the merge queue sends the card back with
  the reason, Mark met by the owner (refused for a bot), a private pipeline hidden from non-members, and flow-map numbers equal
  the tables.
- Factory sim: `pipeline_requirements`, `pipeline_attach`, `pipeline_review`, `pipeline_manager`, plus `pipeline_marketing` and
  `pipeline_handoff` (register them first, see their docstrings) pass seeds 1-3, and every older scenario gives the same
  results and turn counts as before.
- `scripts/check.sh` is green (lint baseline, format, types, UI strict, openapi) and the board payload is still under budget.

## 2. Judge model bar

Every pipeline requirement is judged by Jev alone (Decide Low), so this is the one model to check. Run
`python -m evals.pipeline_judge.run --models jev` (real calls; not CI; keys in the environment as
`evals/pipeline_judge/adapter.py` says). 34 labeled cases, Dev and Marketing, with traps (missing file, older work version,
wrong screen, lead with no email, draft, placeholder page, self-review).

- Jev must have **0 false "met"** (a met verdict where the label says not met or unsure).
- Accuracy 90% or better, and the unsure rate no higher than the labeled unsure share (10 of 34, about 29%, since every screenshot is unsure: the judge is shown no image text) plus 10 points. Unsure is
  cheap (it goes to a person); a wrong met is not.
- p95 latency under the judge tick's budget (30 s), and cost per decision under 1 cent.
- Jev must pass.
- Keep the JSON and the table from the run with the approval.

## 3. UI: desktop and phone

Check on the Tico Team company, at desktop width and at phone width (390 px), light and dark.

- Pipeline page: dots on cards; hover or long-press names each requirement, its state and who is next; "in step" time shows only over
  Warn after; dragging past an unmet requirement asks to override (editors only).
- Ticket page: its requirements with who passed them; "Attach to"; Mark met from the menu needs a Why.
- Review mode (the ▶ N for you): queue order (over Warn after first, then oldest), claim shown to a second reviewer, Approve with
  Cmd/Ctrl+Enter, refused after a push, Undo within 10 s, Message assignee, Message manager.
- Manager rail: Late list, log with errors first, last run time, chat.
- Pipeline editor (the pencil by the name): stages, requirements, reviewers, Warn after (number plus Minutes/Hours/Days, or Don't
  warn), From and Then links, owner, manager, prefix.
- Flow map (Overview > Factories): period picker 1 / 7 / 30 days, input circles, lines (no motion with reduced-motion), tap a
  pipeline opens its page, the header shows done, overdue and live.
- Phone: nothing scrolls sideways, targets are at least 44 px, the review screen works one-handed.

## 4. Tico Team spot check

After the setup seed has been applied to Tico Team (factories Product development, Growth; Success; private Legal):

1. As a non-member, confirm Legal is not on the map, the pipeline list or search; as a member it is.
2. Make a Product card, give it a requirement, and walk it through Engineering and back to Product. Read the numbers on the flow
   map against Tasks (cards, overdue, done).
3. Attach a wrong-screen screenshot to a Dev card with the judge on; it must not turn met. Attach the right one; it turns met.
4. Push to the PR after met: the dot goes out of date and the move is refused.
5. Leave a card past Warn after: the pipeline manager asks once; a second tick asks nobody; the Late list shows the card.
6. Pause the manager for 40 minutes: one BotOps task opens, not two.
7. Remove a PR from the merge queue: the card goes back with the reason in a comment.
8. Check the cost lines on Finance for the judge calls for the day.
