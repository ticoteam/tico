# Stage practice

The standing method for moving the cards of one pipeline. The pipeline's own stages, owners and requirements are in
`knowledge/pipeline.md`; this file says how to act on them.

## A card's standing
- A card is **met** at its stage when every requirement of that stage is attached, current and accepted by the engine,
  as `hub requirements <stage_id>` and the card's evidence show.
- A card is **waiting for work** when its requirement is missing, out of date, or waiting on a reviewer.
- A card is **waiting for room** when its next stage is at its limit. Say so; do not count it as stuck by its owner.
- A card is **stuck** when it has passed its threshold in `knowledge/thresholds.md` while waiting for work or for room.
  Time spent on hold does not count toward the threshold.

## Moves
- Move a card only when it is met, and only to the stage the pipeline names as next. Leave a card where it is when the
  engine refuses the move, and report the refusal with its reason.
- Never move a card backward to make a report look better. A refile is an action with a comment saying why.
- Never move a card forward on a requirement that you cannot read. Unread is not met.

## Assignment
- Each stage names who does its work. Put the card with that bot or person. If the seat for the stage is not hired, the
  card waits on the Team Manager's hiring decision; do not assign it to a stand-in without a task that says so.
- When a seat is hired or a bot is removed, the cards it owned are re-read on the next run.

## Asking
- One question per card per visit. It names the missing requirement and the one thing that unblocks it.
- A question to a reviewer names the work to review and the date it was waiting since.
- A question with no answer for a week goes to the Team Manager, once, as a task.

## Tidying
- Cards with no next step, duplicates of another card, and snoozes that have run out are raised with their owner. A card
  is closed only by its owner or on the Team Manager's decision.

## Sources
- The pipeline engine's rules for requirements, limits and stage moves, as the company's pipeline is set up.
