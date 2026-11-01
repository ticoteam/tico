# Weekly quality page

Schedule: Mondays at 09:00 team time (routine `weekly-quality-page`), after setup. Budget 30 minutes. Nothing is sent,
paid, signed or changed. The page goes to the Operations Manager; a checklist change is a proposal for them to approve.

Inputs: the week's inspection results, the re-do list and the jobs export on the task (ask once if one is missing),
`knowledge/checklists.md`, `knowledge/quality-method.md`, and last week's page.

Outcome: one page in the shape of `knowledge/examples/quality-page.md`: the headline, scores per crew with job counts and
ranges, repeat failures by checklist item, re-dos with their causes, proposed checklist changes, and what could not be read.

Done when every inspection in the export is in `knowledge/inspection-log.md`, every re-do has one cause and its hours,
every crew score shows its job count and range, every repeat failure is classed as a system or a crew cause, the page is
published, and the task note carries the headline.

---

## 1. Read the inputs

Read the export and the re-do list on the task. Check the job count against the number of inspections recorded. A
mismatch is named on the page, not smoothed over.

## 2. Log the week

1. Add each inspection to `knowledge/inspection-log.md`: date, job, crew, result, failed items, and whether it was a random
   check or a complaint.
2. Add each re-do to `knowledge/redos.md`: job, crew, one cause from `knowledge/quality-method.md`, and hours.

## 3. Score each crew

Compute pass rate and re-do rate per crew over the last 8 weeks, with the number of jobs and the range (95 percent,
Wilson), as `skills/score-a-crew-fairly/SKILL.md` describes. Under 10 jobs, the score is shown as not yet reliable and the
crew is not ranked.

## 4. Find the repeat failures

Count each checklist item that failed across crews. Where two or more crews fail the same item, follow
`skills/sort-failures-by-cause/SKILL.md`: it is a checklist or training finding, and the proposal goes to the checklist, not
to a crew. A failure that repeats in one crew only is listed for the Operations Manager, with the facts and no judgement.

## 5. Propose checklist changes

For each repeat failure with a system cause, write the proposed change: the item, the new pass standard in one line, and
the photo it needs. List each proposal under "for approval". Do not edit `knowledge/checklists.md` until a person approves.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-quality-page.md` in the shape of `knowledge/examples/quality-page.md`. Then:

    hub file publish reports/YYYY-MM-DD-quality-page.md

Commit, then:

    hub task update <id> --status done --note "..."

The note holds the headline and the pass rate, the number of crews with a reliable score, and anything you could not read.
A quiet week is one line, and the task is still finished.
