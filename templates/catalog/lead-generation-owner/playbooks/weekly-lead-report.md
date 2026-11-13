# Weekly lead report

Schedule: Mondays at 09:00 team time (routine `weekly-lead-report`), after setup. Also run when a task asks. Budget 45 minutes.

Inputs: `knowledge/lead-method.md`, `knowledge/sources.md`, last week's report, the lead export (a CRM read, or the export on
the task), and the hand-off tasks you created last week.

Outcome: one report in the shape of `knowledge/examples/qualified-leads-week.md`: the headline; the qualified leads by source
with arrivals, rate and four-week rate; the not-qualified leads by the first step that failed; the hand-offs made and any not
yet picked up; the targets built, on their own line; the one source to look at; and what could not be counted.

Done when every count has its source and window, the not-qualified reasons add up to the not-qualified total, every qualified
lead has a hand-off task, the report is published and the task note carries the headline.

---

## 1. Read where things stand

    tico task show <id>

Read last week's report, `knowledge/lead-method.md` and `knowledge/sources.md`.

## 2. Pull last week's arrivals

Use the export on the task if one is attached. Otherwise read the leads with their source tag, stage, owner and first-touch
time, from the CRM through `tico db list` and `tico db <name> "<select>"`, or from `tico sql "<select>"` for the team's own
tables. Monday to Sunday. Write the pull into `knowledge/sources.md` with its window and the date it was pulled. A lead with no
tag is counted as "no source" and listed for the tracking owner. If no source has the leads, every count reads "not measured".

## 3. Apply the rule to each arrival

Follow `skills/score-a-lead-against-the-profile/SKILL.md`. Each arrival is qualified, or not qualified with the first failing
step as its reason. Check that the qualified and the not-qualified add up to the arrivals before you write anything else.

## 4. Check each hand-off

For each qualified lead, find its hand-off task. List your own tasks with `tico task list --requester me`. A qualified lead with
no hand-off task is handed off now, following `skills/hand-off-a-qualified-lead/SKILL.md`. A hand-off past its pick-up time
is named in the report with the sales owner it went to.

## 5. Count by source and segment

Rate = qualified ÷ arrived, for the same source and window. The four-week average uses the same definition over the last four
full weeks. Cost per qualified lead only if spend per source is in an export; otherwise it reads "not measured". Sources with
fewer than ten arrivals are shown, not rated.

## 6. Count the targets

Companies added to a public list this week are counted on their own line. They are targets, not leads, and they never enter
the qualified count.

## 7. Choose one source change, or none

Rank the sources by the trend in their rate and by how much a change would add. Propose at most one change: add, keep or stop
a source, with the count and window behind it. Send it as a task to the person who runs sales. Do not change the source.

## 8. Write the report and publish it

Write `reports/YYYY-MM-DD-leads.md` in the shape of the example. Then:

    tico file publish reports/YYYY-MM-DD-leads.md

Log the week's counts in `knowledge/sources.md`. Record the qualified count as a KPI reading with `tico kpi log` if
`tico kpi list` shows one you own. Note progress on the task and commit this repository.

## 9. Close

    tico task done <id> -m "..." --version N

The note holds the headline, the report path, the hand-offs not yet picked up, and what could not be counted. Finish the task
even on a quiet week.

## When a source fails

Name it in the report and in the note: which export or database failed and what that leaves unknown. A missing count is "not
measured", never zero.
