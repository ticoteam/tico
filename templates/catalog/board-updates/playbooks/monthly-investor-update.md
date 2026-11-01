# Monthly investor update draft

Schedule: the third of each month at 09:00 team time (routine `monthly-investor-update`), after setup. Also run by hand. Budget 40 minutes. The outcome is one page
for the owner to review and send. Nothing is sent or shared.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/metrics.md`, `knowledge/asks.md`, `knowledge/exclusions.md`, `knowledge/finance-inputs.md` and last month's update in `reports/`. Note the month
covered (the calendar month just ended).

## 2. Collect the numbers

1. For each metric in `knowledge/metrics.md`, read the current value from its source and the value a month ago: `hub kpi show <kpi id>`, or the source the line names.
2. Compare each with plan or target where one exists (`hub goal list --all`).
3. Cash, burn and runway: use `knowledge/finance-inputs.md` for this month. If missing, ask once with `hub task ask <id>` ("Cash, burn and runway for <month>, please")
   and write "not supplied" meanwhile.
4. Check each figure with `skills/check-a-figure-before-it-goes-out/SKILL.md`. A move over 20% needs a one-line reason from a dated source, or "reason not found".

## 3. Collect the month's story

Read `hub update list --kind weekly` and `hub meeting search --since <first of month>`. Apply `skills/write-the-highlights-and-lowlights/SKILL.md`: three to five
highlights, one to three lowlights, each dated and sourced, with the exclusions removed.

## 4. Write the asks

Start from the open asks in `knowledge/asks.md`: repeat one that is still open, and close one that was answered with a line of thanks. Add at most three new asks,
each specific: who, for what, by when.

## 5. Write the page

In this order, in the shape of `knowledge/examples/monthly-update.md`: headline sentence, metrics table (this month, last month, plan), asks, highlights, lowlights,
recap in two lines. Under 350 words. Mark the draft incomplete at the top if any figure is missing.

## 6. Hand over

Write `reports/YYYY-MM-DD-investor-update.md` and publish it with `hub file publish`. Commit, then `hub task update <id> --status done --note`: the headline, the path,
the missing figures, and any lowlight that may deserve a call before the update. Always finish the task. The owner sends it.
