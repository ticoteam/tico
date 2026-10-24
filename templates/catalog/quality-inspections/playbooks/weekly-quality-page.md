# Weekly quality page

Schedule: mondays at 09:00 team time (routine `weekly-quality-page`), after setup. Budget 30 minutes. The outcome is the report below, ready for a
person to act on. Nothing is sent, paid, signed or changed.

---

## 1. Inputs

The inspection results, the re-do list and the jobs export on the task (ask once if missing), `knowledge/checklists.md`, and last week's page.

## 2. Work

1. Add the week's inspections to `knowledge/inspection-log.md`: job, crew, result and failed items.
2. Add each re-do to `knowledge/redos.md` with its cause (missed item, wrong method, customer change, other) and the hours it cost.
3. Recompute the score per crew: pass rate and re-do rate, with the number of jobs behind each. Under 10 jobs, say the score is not yet reliable and show no ranking.
4. Find repeat failures by checklist item across crews; if several crews fail the same item, propose a checklist or training change rather than a crew finding.
5. Write the page with the answer first, and list checklist changes as proposals for a person to approve.

## 3. Write and hand over

Write `reports/YYYY-MM-DD-quality-page.md` in the shape of `knowledge/examples/quality-page.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note` with the headline.
