# Budget against actual

Triggered by a task after the month is closed (the Bookkeeper's close status says "ready to close" or
a human says the books are final), or by a request for "how are we doing against budget". Budget 40
minutes. The outcome is one page: each budget line's actual, the variance, the material ones explained
and owned, and the forecast rolled forward. Nothing in the budget or the books is changed.

Inputs: the month's profit-and-loss export or the Bookkeeper's close status, `knowledge/forecast.md` (budget and current forecast), and the variance rule in `knowledge/thresholds.md`.

Done when: every budget line has its actual and variance, each material variance has a cause and an owner, the forecast is rolled with a reason for each changed line, the report is published, and the task is closed with the headline.

---

## 1. Check the inputs are final

    hub task show <id>

Read `knowledge/forecast.md` (the budget and the current forecast, by line and month) and the
variance rule in `knowledge/thresholds.md`. Get the month's actuals: the profit-and-loss export
attached to the task, or the Bookkeeper's close status and the export it cites. If the close is not
finished, say so in the first line and label every figure "preliminary".

## 2. Compute, line by line

For each budget line: actual, budget, variance in money and in percent (actual minus budget; for a
cost line, positive is over). Keep the budget's own line names; map an actual to a line only through
the category rules, and list anything unmapped rather than guessing.

## 3. Pick the material variances

Material means over both the percentage and the amount the team agreed (default 10 percent and
2,000). For each: the cause as a source shows it (a vendor line, a hire date, a one-off), whether it is
timing (spend moved between months) or permanent, and the owner who should answer for it. A cause no
source shows is a question for the owner, never a guess.

## 4. Roll the forecast forward

Replace the month just closed with its actual. For the remaining months, change a line only for a
reason on record: a permanent variance, a signed contract, a hire date a human gave. Each changed
line names the reason and the date in `knowledge/forecast.md`. Show the full-year outlook against the
budget: budget, forecast, difference.

## 5. Write and hand over

`reports/YYYY-MM-budget-vs-actual.md`: the headline first ("August: costs 4 percent over budget, two
material variances; full-year outlook 1.2 percent over"), then the material variances, then the
full table as a linked file, then "Could not read". `hub file publish` it, commit, and
`hub task update <id> --status done --note` with the headline and the path. The Head of Finance reads
it before anyone else.
