# Weekly win/loss notes

Schedule: Fridays at 10:00 team time (routine `weekly-win-loss`), after setup. Also run by hand. Budget 45 minutes. The outcome is one page: why this week's deals were won or
lost in buyers' words, what it adds to the pattern, and one thing to change. Nothing leaves the team.

---

## 1. Find the closed deals

    tico task show <id>

From the Account Executive's review, the Sales Operations Manager's report or a CRM read: every deal won or lost since last
Friday, with amount, segment, competitor and the recorded reason. Also deals marked lost quietly.

## 2. Read the evidence

For each, `tico meeting search "<customer>"` and read the last two or three calls, then the seller's notes.
Extract four to six decision drivers: what the buyer valued, feared, compared, and who decided. Each is a
quote or close paraphrase with call and timestamp. Where the evidence and the recorded reason disagree,
say so; the correction is for the deal's owner and `sales-ops`.

## 3. Compare with the pattern

Add the deals to `knowledge/win-loss.md`. Count each driver over the last 90 days, wins and losses
separately. A driver in three or more deals is a pattern; fewer is an anecdote and is labelled so.

## 4. One change

Pick the one change the evidence supports most: a talk track to add, an objection answer that failed, a
discovery question asked too late. Write the proposed wording and the calls behind it.

## 5. Ramp milestones

For each seller in `knowledge/ramp/`, list milestones due this week and the calls to listen to next.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-win-loss.md` in the shape of `knowledge/examples/win-loss.md`, `tico file publish`
it, commit, and `tico task update <id> --status done --note`: deals covered, the change proposed, calls not
imported. Once a quarter (the last Friday), add the synthesis: patterns by segment and competitor, counts.
