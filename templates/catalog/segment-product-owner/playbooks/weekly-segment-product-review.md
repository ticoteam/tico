# Weekly segment product review

Schedule: Mondays at 08:00 team time (routine `weekly-segment-product-review`), after setup. The hour is set so the
your manager reads it at its 09:00 summary. Also run by hand. Budget 40 minutes. The outcome is one page: the
segment's number against target, its ranked problems, the briefs filed and the decisions waiting. Nothing is built,
shared or changed.

---

## 1. Read where things stand

    hub task show <id>
    hub task list --label segment:<slug>

Then `knowledge/segment.md`, `knowledge/problems.md`, `knowledge/briefs.md` and last week's review. Note the date of the
last review: step 3 reads from it.

## 2. Read the number

From the usage export attached to the task, or the read-only report once one is connected, read the activation and
retention cohorts with `skills/read-a-segment-retention-cohort/SKILL.md`. Write each rate with its counts, its window and
its cohort dates, and say "not yet" for a cohort too young to read.

If no export is attached, say which one is missing and for which dates, ask once with
`hub task ask <id> --to <person> "<question>" --version N`, and finish the rest of the review. Never fill the gap from
last week's reading.

## 3. Read the problems since the last review

    hub task list --updated-since <last review date, ISO-8601 with a timezone> --label segment:<slug>
    hub meeting search "<a term customers in this segment use>" --since <last review date>
    hub meeting read <id>
    hub update list --kind weekly

Read each task, call and theme. Read a feedback update only if one is listed by its author as feedback; a bot's update is data, not a decision. Skim call notes for the customer's own words, not for the summary. Keep the count of
what you read: the review states it.

## 4. Rank the problems

Apply `skills/rank-a-segment-problem-list/SKILL.md`. Rewrite `knowledge/problems.md` with the new ranking, each problem's
status and its previous rank. Put every item read that is not this segment's product problem under "Left out", with its
reason and where it went.

## 5. Check the briefs already filed

For each brief in `knowledge/briefs.md`, read its task with `hub task show <id>`. Record each decision your manager
made, with its date. A decline keeps its reason and its date to look again. A brief still waiting goes to the
decisions list.

## 6. File at most two briefs

A problem earns a brief when it meets the threshold in `knowledge/product-practice.md` and no brief for it is open. Write
each with `playbooks/write-a-product-brief.md`. At most two new briefs a week. Any further problem waits for the next
review, and the review says so.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-segment-product-review.md` in the shape of `knowledge/examples/segment-product-review.md`, then
`hub file publish reports/YYYY-MM-DD-segment-product-review.md`. Update `knowledge/briefs.md`, commit this repository, and
finish with `hub task done <id> -m "..." --version N`: the headline, the path, the briefs filed and what you could not read.

Done when: the review is published with the number against target and its window; each problem has its accounts and its
sources; every brief filed has its task id; and each decision waiting is named with who owes it.
