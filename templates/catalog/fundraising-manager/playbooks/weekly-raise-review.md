# Weekly raise review

Schedule: Mondays at 09:00 team time (routine `weekly-raise-review`), after setup. Budget 30 minutes.

Inputs: the investor list, recent meeting notes and the data room folder on the task (ask once if missing),
`knowledge/pipeline.md`, `knowledge/data-room.md`, `knowledge/raise-tracker.md` and last week's review.

Outcome: the report in the shape of `knowledge/examples/raise-review.md`: a headline, follow-ups due with their draft
status, meetings this week with prep briefs, data room gaps, and sources. Nothing is sent, paid, signed or changed.

Done when: every investor with a follow-up due has a draft on the task, the tracker matches what the human confirmed (or the
difference is at the top of the report), the report is published, and the task is closed with the headline.

---

## 1. Work

1. Update each investor's stage and last contact from what the human and the threads show. An investor not heard from in
   14 days in a live round goes on the follow-up list.
2. Update the data room checklist with `skills/check-data-room-readiness/SKILL.md`: what was added, what an investor asked
   for that is missing, who owns it.
3. Update the raise tracker with `skills/track-committed-capital/SKILL.md`. Only confirmed commitments, each with its
   written source. Never round a verbal maybe into committed.
4. For each meeting in the next 7 days, write the one-page prep brief (`playbooks/meeting-prep.md`).
5. Draft a follow-up for each investor who needs one with `skills/draft-an-investor-follow-up/SKILL.md`. Drafts only; a
   person approves each one or sends it.

## 2. Write and hand over

Write `reports/YYYY-MM-DD-raise-review.md`, `hub file publish` it, commit, and `hub task update <id> --status done --note`
with the headline.
