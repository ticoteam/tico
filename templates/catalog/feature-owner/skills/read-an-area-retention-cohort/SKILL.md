---
name: read-an-area-retention-cohort
description: Read an area's activation and retention from a cohort export without calling an immature cohort a result, naming its window, its size and its definition, and showing counts when a rate is too small to trust.
---
# Read an area's retention cohort

When to use: before any activation or retention figure goes into a review, a pitch, a spec or a question, and before a change in either is called a trend.

Steps:
1. Write the export's header: the area's label, the cohort dates (first and last sign-up day), the export date, the file or view name, and the activation and "active" definitions from `knowledge/area.md`.
2. Check that the export's definitions match `knowledge/area.md`. If the export counts "active" differently, say so. Report the export's own figure, labelled with its definition, and do not restate it on the area's definition unless the export has the columns to do it.
3. Mark maturity. An activation cohort is read once its activation window (default 14 days) has passed for every account in it. A retention cohort is read at day 30 only when every account is at least 30 days old, and at day 90 likewise. A younger cohort is "not yet", never 0%.
4. Count the cohort: accounts in, accounts activated, accounts still active at each window. Write the counts beside every rate.
5. Show a rate only when the cohort has at least 30 accounts (the default). Below that, show the counts and say "too few for a rate".
6. For a change, compare two mature cohorts of the same length, for example sign-ups from two consecutive fortnights. One week's movement is movement, not a trend.
7. Name each exclusion: test accounts, internal accounts, accounts that moved area. Count them and say how many. Do not drop them silently.
8. Write the reading as: the rate, the counts, the window, the cohort dates, the export date and its source.

Checks before you finish:
- No rate comes from an immature cohort.
- Each rate has its counts and window beside it.
- The definition used is named.
- A customer segment marked Next on the Strategy page is read on its pilot or test accounts, and labelled that way.

Example (invented, for illustration): "Creators activation, sign-ups 2026-10-05 to 2026-10-18, 14-day window, usage export 2026-11-02: 48 of 117 activated, 41%. Target 50%. Retention at day 30: not yet, the last sign-up is 15 days old. Earlier cohort, sign-ups 2026-09-21 to 2026-10-04: 52 of 121 activated, 43%. Movement of 2 points between two cohorts of this size: too small to call a trend."
