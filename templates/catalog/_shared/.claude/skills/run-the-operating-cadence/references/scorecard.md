# The weekly scorecard

Five to fifteen numbers that tell the owner, in one minute, whether the company is on track. Keep the list in
`knowledge/operating-cadence.md` under "Scorecard".

## What goes on it

Pick numbers that move before the money does. Revenue for the month is a result; this week's new trials, replies sent
on time and orders shipped are what cause it.

| Kind | What it is | Default rows |
|------|------------|--------------|
| North Star | The one KPI the strategy names as the measure of value delivered (`growth_role: north_star`) | 1 |
| Inputs | KPIs that drive the North Star and that a teammate can move this week (`input`) | 3 to 6 |
| Guardrails | Numbers that must not get worse while the others improve (`guardrail`): refunds, complaints, cost per order | 1 to 3 |
| Money | Cash and runway in months | 2 |
| Team health | Tasks done, failed runs, asks waiting on a person more than two days | 3 |

Find them with `tico kpi list`. If the team has no North Star yet, say so on the page and file a proposal to the
Strategy Owner; run on money and team health until it exists.

## One row, fully written

Each row in the knowledge file carries all of these. A row missing any of them is not ready.

| Field | Rule |
|-------|------|
| Name | Plain words a new teammate would understand |
| Definition | What is counted, what is excluded, over what period. Fixed; a change is a new version with a date |
| Unit and direction | "count, higher is better"; "minutes, lower is better" |
| Owner | One teammate who can move it |
| Target | A number for the week, or a range (min, max) for a number to hold steady |
| Tolerance | How far off still counts as yellow, not red |
| Source | The exact command, report or Tool view it is read from |
| Since | The date it joined the scorecard |

## Reading rules

1. **Read it this run.** From the source named on the row, for the week ending the Sunday just past.
2. **Show the period.** A number for a different week is labelled with its own week and shown gray.
3. **Not read is gray.** Write why in a few words ("sign-in expired", "report not ready") and file one task to the
   teammate who can fix the source. Do not copy last week's value.
4. **Estimates are labelled.** A reading logged with quality `estimate` or `partial` shows that word next to the
   value and is gray until the measured value replaces it.
5. **Do not log into another teammate's KPI.** The KPI's owner posts its readings. You read them.
6. **Corrections supersede.** A wrong reading is replaced with `tico kpi log ... --supersedes <reading>`, and the next
   page shows both values once with the word "restated".
7. **Show the trend.** Beside each value, the last eight weeks as a small line or eight small bars. Direction tells
   more than one week's colour.
8. **Tie money to the books.** Cash and runway come from `tico finance metric`. If that figure and the bank Tool
   disagree, show the finance figure and file the difference to the Finance Owner.

## Colour

| Colour | Meaning | What happens |
|--------|---------|--------------|
| Green | At or better than target | Nothing |
| Yellow | Off target but inside tolerance | Named on the page; watch |
| Red | Outside tolerance | Named on the page; owner writes one line on why. Two weeks red: an issue |
| Gray | Not read, or estimate or partial | Task to fix the source. Two weeks gray: an issue |

## Keeping it honest

- **A cap of 15.** To add a number, remove one. A number nobody acted on for a quarter comes off.
- **Counts beside rates.** Show "31 of 420 trials (7.4%)", not the percentage alone. Small bases make rates jump.
- **No target changes mid-quarter** without a proposal the owner confirms. A target that moves each time it is missed
  is not a target.
- **One definition.** If two teammates compute the same name two ways, that is an issue for this week's list.
- **Weekly numbers only.** A number that can only be read monthly goes on the monthly finance page, not here.
