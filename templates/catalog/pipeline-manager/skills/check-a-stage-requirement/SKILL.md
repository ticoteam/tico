---
name: check-a-stage-requirement
description: Decide whether a card meets its stage's requirement before you move it on, from the requirement as the pipeline sets it and the evidence on the card, and say so when the evidence cannot be read.
---
# Check a stage requirement

When to use: before you move a card to its next stage, and before you call a card stuck for a missing requirement.

Steps:
1. Read the requirement: `hub requirements <stage_id>`. Write down its kind, its reviewer if it has one, and its version.
2. Read the evidence on the card: `hub task show <id>` and `hub task answers <id>`. Evidence is a company file, an upload or a
   link attached to the requirement. Note the version each piece of evidence was attached at.
3. Compare: is each piece attached, is it the current version, and has the named reviewer accepted it?
4. Decide: met when all three hold for every requirement; waiting for work when one is missing or out of date; waiting for a
   reviewer when the work is attached and the decision is open.
5. If you cannot open the evidence, the answer is "not read". Do not move the card.

Checks before you finish:
- The requirement was read on the day you decided, not taken from an earlier run.
- A requirement's evidence that is out of date is named as out of date, with its version.
