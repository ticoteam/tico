# Summarise a pulse survey

Schedule: triggered by a task with a survey results export. Budget 40 minutes. Inputs: the export attached to the task and the
anonymity floor in `knowledge/survey.md`. Outcome: a two-page readout for the owner:
participation, what changed, themes, and two or three actions with owners. It is shared further, so it must hold up on its own.

Done when every group shown has passed `skills/check-a-group-before-reporting/SKILL.md`, the readout follows
`skills/write-a-pulse-readout/SKILL.md`, any serious comment was routed under `skills/route-a-serious-comment/SKILL.md`, the
readout is attached to the task, and the export is deleted from the working tree.

---

## 1. Check the export before reading it

Count the responses in each group. Apply `skills/check-a-group-before-reporting/SKILL.md` to every group in the export. If the
export has individual rows, work from it without saving it to the repository, and never look for who wrote what.

## 2. Participation

Overall and per group above the threshold in `knowledge/survey.md`, against the number invited and against last quarter. Low
participation in one group is itself a finding.

## 3. Scores and trends

Per question: this quarter, last quarter, the change. Only identical questions are compared. The two biggest rises and the two
biggest falls go first. Changes within the noise rule in `knowledge/survey.md` are called noise.

## 4. Themes from comments

Read every comment. Serious comments (harassment, safety, discrimination, someone at risk) are routed first, untouched, under
`skills/route-a-serious-comment/SKILL.md`. Group the rest into four to seven themes, each with a count and the groups it came from
(above threshold). Quote only comments that identify nobody; paraphrase the rest.

## 5. Actions

Propose two or three actions, each tied to a finding, with a suggested owner, a date and one line on what employees will be told.
Open with last quarter's actions and whether they happened.

## 6. Write, attach and share

Write `reports/YYYY-MM-DD-pulse-readout.md` and attach it to the task with `hub task attach <id> <file>`. Publish it with
`hub file publish reports/YYYY-MM-DD-pulse-readout.md --scope task --task <id>`. Share it only with the audience the task names,
within the requested work and your Tools. Ask the owner on the task only when the audience is missing. Add the requested actions to
`knowledge/actions.md`. Delete the export from the working tree, commit, and finish the task.
