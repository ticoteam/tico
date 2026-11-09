---
name: find-the-owner-of-a-card
description: Work out who does a card's work in its current stage from the pipeline's stage owners, and say so when the owner seat is not hired, instead of guessing a stand-in.
---
# Find the owner of a card

When to use: before you assign a card, before you ask a card's owner something, and when a card has no owner.

Steps:
1. Read the stage's owner in `knowledge/pipeline.md`, and check it against `hub pipeline --type "<pipeline>"`, which shows each
   step's owners and whom each waits on.
2. If the stage names a bot or person who is active, assign the card to them: `hub task update <id> --owner <bot or person>`.
3. If the stage names a seat that is not hired, the card has no owner. Do not assign a stand-in. Name the seat in the report
   and ask the Team Manager as a task.
4. If the card already has an owner who is not the stage's owner, keep it and say why in one line, or ask the stage's owner
   whether it should move.

Checks before you finish:
- The owner you named is active, or the report says the seat is not hired.
- No card was given to a bot or person the pipeline does not name for that stage.
