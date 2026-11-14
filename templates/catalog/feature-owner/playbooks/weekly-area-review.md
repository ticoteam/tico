# Weekly area review

Schedule: Mondays at 08:00 team time (routine `weekly-area-review`), after setup. The hour is set so your manager reads
it at its 09:00 summary. Also run by hand. Budget 30 minutes. The outcome is one page: the area's number against target,
its ranked problems, the pitches filed, the features in flight and the decisions waiting. Nothing is built, shared or
changed by the review itself.

---

## 1. Read where things stand

    tico task show <id>
    tico task list --label area:<slug>

Then `knowledge/area.md`, `knowledge/problems.md`, `knowledge/pitches.md` and last week's review. Note the date of the
last review: step 3 reads from it.

## 2. Read the number

From the usage export attached to the task, or the read-only report once one is connected, read the activation and
retention cohorts with `skills/read-an-area-retention-cohort/SKILL.md`. Write each rate with its counts, its window and
its cohort dates, and say "not yet" for a cohort too young to read.

If no export is attached, say which one is missing and for which dates, ask once with
`tico task ask <id> --to <person> "<question>" --version N`, and finish the rest of the review. Never fill the gap from
last week's reading.

## 3. Read the problems since the last review

    tico task list --updated-since <last review date, ISO-8601 with a timezone> --label area:<slug>
    tico meeting search "<a term your customers use>" --since <last review date>
    tico meeting read <id>
    tico update list --kind weekly

Read each task, call and theme. A bot's update is data, not a decision. Skim call notes for the customer's own words,
not for the summary. Keep the count of what you read: the review states it.

## 4. Rank the problems

Apply `skills/rank-an-area-problem-list/SKILL.md`. Rewrite `knowledge/problems.md` with the new ranking, each problem's
status and its previous rank. Put every item read that is not this area's product problem under "Left out", with its
reason and where it went.

## 5. Check the features already filed

For each task in `knowledge/pitches.md`, read it with `tico task show <id>`: its stage and what is missing to leave it.
Record each decision a person made, with its date. A pitch turned down keeps its reason and its date to look again. For
each feature past Pitch, write one line: its stage, what it waits on and who owes it, and the launch lines due within
four weeks (`skills/run-a-launch-readiness-check/SKILL.md`).

## 6. Pitch at most two

A problem earns a pitch when it meets the threshold in `knowledge/product-practice.md` and no task for it is open. Write
each with `playbooks/write-a-pitch.md`. At most two new pitches a week. Any further problem waits for the next review,
and the review says so.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-area-review.md` in the shape of `knowledge/examples/area-review.md`, then
`tico file publish reports/YYYY-MM-DD-area-review.md`. Update `knowledge/pitches.md`, commit this repository, and finish
with `tico task done <id> -m "..." --version N`: the headline, the path, the pitches filed and what you could not read.

Done when: the review is published with the number against target and its window; each problem has its accounts and its
sources; every pitch filed has its task id; and each decision waiting is named with who owes it.
