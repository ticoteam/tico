---
name: write-the-stuck-card-question
description: Write the one question a stuck card's owner needs to answer, naming the card, the stage, the missing requirement and the single thing that would unblock it, so the owner can answer in one line.
---
# Write the stuck card question

When to use: when a card's requirement is missing or out of date, and its owner has not been asked this visit.

Steps:
1. Name the card and its stage in the first line: "Card 118, Specifying: ..."
2. Name the one requirement that is missing or out of date, and what it asks for, in the words the pipeline uses.
3. Say what would unblock it, in one thing the owner can do: attach the Spec page, name the reviewer, answer the decision.
4. Give a clear date to answer by, from the stage's threshold in `knowledge/thresholds.md`.
5. Ask it as a task question to the owner: `hub task question "<the question>" --on <person> --why "<one line>"`, or as a
   comment on the card with `hub task comment <id> "<text>"` when the owner is a bot.

Checks before you finish:
- One question per card per visit.
- The question names no person as the cause of the gap.
- The question fits in under 120 words.
