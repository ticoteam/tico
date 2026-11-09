# Prepare calibration

Schedule: triggered by a task from the HR owner once manager ratings are in. Budget 45 minutes. Inputs: the ratings grid the HR
owner attached, `knowledge/rating-scale.md` and the level expectations. Outcome: a calibration sheet the facilitator can run a
session from, attached to the task for the HR owner only. You flag inconsistencies; the managers and the HR owner decide.

Done when every review on the grid has an evidence mark, the team-level spread is written with groups under five merged, the
sheet has five to ten discussion items, the sheet is attached to the task, and the local copy is deleted.

---

## 1. Read the definitions first

`knowledge/rating-scale.md` and the level expectations (`hub doc ask "What do our level expectations say for <level>?"`). If
the scale has no written meaning per point, say so on the task: calibration without definitions is an argument.

## 2. Build the grid

From the ratings the HR owner attached: reference, team, level, manager, rating, and whether the review cites evidence (yes,
partly, no). No names beyond references, no pay, no tenure notes, nothing about leave or health.

## 3. Find what to discuss

- Run `skills/check-a-review-for-evidence/SKILL.md` on each review, and `skills/check-a-team-rating-spread/SKILL.md` for the spread
  by team and level. A team far above or below the whole is a question for its manager, not a correction.
- Ratings at either end of the scale with no evidence cited.
- Two reviews with the same rating described very differently, or different ratings described alike.
- Wording about personality or style rather than work and its effect.
- A review affected by an absence during the cycle: mark it by reference for the facilitator to handle under the cycle policy.
  The reason stays out of the sheet.

Five to ten discussion items, most consequential first.

## 4. Write the sheet

A one-page summary (spread, items to discuss, the definitions) and the grid as an appendix. Attach it with
`hub task attach <id> <file>` and delete the local copy. Nothing goes into `knowledge/` or `reports/`.

## 5. After the session

Record in `knowledge/review-cycle.md` only process lessons (which definition caused debate, which phase ran late), never an
outcome about a person. Then `hub task update <id> --status done --note`.
