# Weekly stage report

Runs Mondays at 09:00 team time, and when a person asks for the stage report on the task.

## Steps
1. Read `knowledge/pipeline.md`, `knowledge/thresholds.md` and `knowledge/stage-practice.md`.
2. Read each stage's count and owners with `hub pipeline --type "<pipeline>"`. Read every open card's standing with
   `hub requirements <stage_id>` and `hub task show <id>`. A card you could not read is "not read", not clean.
3. Work out for each card: met, waiting for work, waiting for room, or stuck (see `knowledge/stage-practice.md`).
4. Move each met card on, assign each unowned card where its stage names an owner, and ask each card's owner once about
   a missing requirement (`skills/write-the-stuck-card-question/SKILL.md`).
5. Write `reports/YYYY-MM-DD-stages.md` in this order: the headline in one line, stuck cards with the owner each waits on,
   requirements that failed twice, cards waiting for room, the tidy-up asks, and what was not read. Use
   `knowledge/examples/stage-report.md` for shape.
6. Publish with `hub file publish reports/YYYY-MM-DD-stages.md` and send the Team Manager a task that links it, with the
   one decision that needs them, if there is one.

## Checks before you finish
- Every number has its window and its read time.
- Every stuck card names an owner and the gap.
- No card was moved past an unmet requirement, and no requirement was waived.
