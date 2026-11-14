---
name: write-how-we-will-know
description: Write the how-we-will-know part of a spec, covering the measure, its baseline with a date, the target and its source, the read date after the window, and the guard measure that must not get worse, so a stranger can judge the result.
---
# Write how we will know

When to use: for every spec, before it is attached, and when your manager asks how a change would be judged.

Steps:
1. Name the measure in the area's terms: the area's activation or retention, or the rate of the one step the problem blocks (for example, "connected a calendar within 14 days of sign-up"). It must be countable in the usage export. If it is not, say which export it needs.
2. Write the baseline: the current value, with its cohort dates, its count and the export it came from.
3. Write the target: a number, the change from the baseline, and its source. If the team has no target, write "missing: to be set by your manager". Do not invent one.
4. Write the read date: the date when the first cohort that has the change has passed its window. For activation, that is at least two activation windows after the change ships. For retention, it is after the 30-day window. Write a date, never "later".
5. Write the guard: one measure that must not get worse, for example the tickets in the same area, or the area's activation at another step, with its baseline.
6. Write the decision rule: what reading keeps the change, and what reading undoes it.

Checks before you finish:
- A stranger can read the baseline, the target, the read date and the guard, and say what happened.
- The read date is after the window, never before it.
- Every target has a source, or is marked missing.

Example (invented, for illustration): "Measure: share of new Creator accounts that connect a calendar within 14 days. Baseline: 21% for accounts with a shared calendar (3 of 14), sign-ups 2026-10-05 to 2026-10-18, usage export 2026-11-02. Target: missing, to be set by your manager; no target exists in `tico goal list`. Read on 2026-12-14, after two 14-day windows following an expected release. Guard: calendar-related tickets per week, baseline 9 (2026-10-26 to 2026-11-01). Keep the change if the measure rises and the tickets do not; undo it if the tickets rise."
