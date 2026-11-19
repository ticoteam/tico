# Account health: signals, bands and calibration

A health score is a prediction: will this customer stay and grow, or shrink and leave? It is only as good as its
record of being right. This file gives starting rules that work with no history, and the method for replacing them
with weights learned from the team's own renewals and losses.

Every threshold below is a starting default chosen for this library on 2026-11-18. None is a benchmark. Save the
team's versions in `knowledge/health-rules.md`.

## The five signal families

Read each from a named Tool, with a date. Compare with 4 and 12 weeks ago.

| Family | What to read | Where it usually lives |
|---|---|---|
| Outcome progress | The measure in the outcome plan against its target; milestones on time; first value reached | The plan file; the customer's own reports; product data |
| Adoption | Active users against seats or expected users; frequency; use of the two or three features tied to their goal; breadth across their teams | Product usage Tool |
| Support | Contacts per month, severity, repeats on the same issue, open recoveries, time waiting on the team | Help desk Tool, `knowledge/recovery-log.md` |
| Relationship | A named champion who replies; a decision-maker who has been in touch this half-year; people leaving or joining; replies to your messages; attendance at reviews | Mailbox, calendar, CRM, meeting records |
| Commercial | Payments on time; plan changes; seats added or removed; days to the notice date; anything they have said about budget or alternatives | Billing Tool, contract, CRM |

Use `read-usage-without-misreading` for adoption: count people not events, use the same window each time, check that
a drop is not a tracking change, a holiday or a seasonal dip, and never read one week alone.

A family with no readable source is "not visible". Report that on the page and file a task to get the data.

## Reading each family

Give each family one of four readings:

- **Good:** at or above the plan or the account's own norm, and level or rising.
- **Watch:** below the norm or falling for two readings in a row, with a known cause being handled.
- **Bad:** well below the norm, or falling for three readings, or a cause nobody is handling.
- **Not visible:** no data.

"Norm" is the account's own trailing 12-week median where there is one, and the segment's median for new accounts.
A big account using the product lightly by design is not unhealthy; compare it with itself.

## Hard signals

Any one of these makes the account red whatever else is true. Check them first.

- They said, in writing or on a call, that they intend to leave, are evaluating a replacement, or will not renew.
- The champion left and no replacement has been in contact.
- A payment failed and is still unrecovered after 14 days.
- They asked for their data to be exported in full, with no stated reason.
- The outcome in the plan has been missed by its date and no new date has been agreed.

Add team-specific hard signals as you learn them (step 5 of the calibration finds them).

## Banding before calibration: plain rules

Use these until the calibration below has enough history.

- **Red:** any hard signal; or outcome progress is Bad; or any two families are Bad.
- **Yellow:** any one family is Bad; or any two are Watch; or outcome progress is Not visible; or there is no
  confirmed outcome plan.
- **Green:** everything else, which means outcome progress is Good, no family is Bad, at most one is Watch, and at
  most one other family is Not visible.

If you want a single number for sorting, use these **uncalibrated** starting weights and print "uncalibrated" beside
every score: outcome progress 30, adoption 30, support 15, relationship 15, commercial 10. Score each family Good = 1,
Watch = 0.5, Bad = 0; a Not visible family is left out and the rest are rescaled, and the score is marked "partial".
The plain rules above decide the band; the number only orders accounts within a band.

## Calibrating against real outcomes

Do this twice a year, and the first time as soon as the history allows.

**1. Collect decided accounts.** From the billing Tool or `tico growth`, list every account that reached a renewal
decision in the last four to eight quarters. For each: the decision date and the result (renewed at the same or
higher value, renewed lower, left). Treat "renewed lower" and "left" together as a **bad outcome** unless there are
enough of each to study separately. Leave out accounts lost for reasons no signal could show, such as the customer
closing down or being acquired, and say how many you left out.

**2. Check there is enough.** As a rule of thumb for this library, you need about 30 decided accounts including
about 10 bad outcomes before weights mean anything. Below that, stop here, keep the plain rules, and write in
`knowledge/health-rules.md`: "uncalibrated, <n> decided accounts, <m> bad outcomes, as of <date>". Read the lost
accounts one by one instead, and write down what each one showed beforehand; that is how hard signals are found.

**3. Rebuild what each signal said 90 days before the decision.** Use only data that existed on that date. For each
account and each signal: Good, Watch, Bad or Not visible. Ninety days is the default look-back because it leaves
time to act; if contracts have a long notice period, look back from the notice date instead.

**4. Measure how well each signal separated the two groups.** For each signal, fill a two-by-two count:

| | Bad outcome | Good outcome |
|---|---|---|
| Signal was Bad or Watch | a | b |
| Signal was Good | c | d |

Then: the bad-outcome rate when the signal was poor = a / (a + b); when it was fine = c / (c + d). The **lift** is
the first divided by the second. A lift near 1 means the signal told you nothing. Write the four counts beside every
lift; with small counts a large lift can be chance, so treat any cell under 5 as "too few to tell".

**5. Look for hard signals.** Any single event that preceded a bad outcome nearly every time it appeared, with at
least five appearances, becomes a hard signal.

**6. Set weights from the lifts.** Weight each family in proportion to (lift minus 1), with a floor of 0. Cap any one
family at 40 so one signal cannot drown the rest, then rescale to 100. A family with a lift at or below 1 gets weight
0 for scoring but stays on the page, because it may matter for a different reason than churn.

**7. Set the band cut-offs from a table, not by feel.** Score the historical accounts with the new weights and sort
them. Choose the red cut-off so that red catches most bad outcomes without flooding the list, and write the result
as counts:

| | Bad outcome | Good outcome |
|---|---|---|
| Scored red | caught | false alarm |
| Scored yellow | | |
| Scored green | missed | |

A miss (green, then left) is the worst error. A false alarm costs an unnecessary helpful message. Lean toward
catching more.

**8. Test on history you held back.** Before any of the above, set aside the most recent quarter of decided
accounts. Score them with the new rules and fill the same table. If the rules do no better than the plain rules on
the held-back quarter, keep the plain rules.

**9. Save the version.** In `knowledge/health-rules.md`: version number, date, the accounts and quarters used, the
lift table with counts, the weights, the cut-offs, the held-back result, and what changed from the last version.
The weekly page prints the version and "calibrated <date> on <n> decided accounts".

**10. Keep score going forward.** Every time an account reaches a decision, add a row: the band 90 days before, and
what happened. Twice a year, redo the calibration with the new rows. If misses rise, do it sooner.

## Honest limits

- A score built on few accounts is a description of the past, not a forecast. Say how many accounts it rests on.
- Calibration finds what predicted leaving, not what caused it. A signal with a high lift is where to look, not
  proof of why.
- Segments differ. If the team has clearly different customer types, calibrate each only when each has enough
  decided accounts; otherwise calibrate together and note it.
- When the product or pricing changes a lot, old history predicts less. Note the change date in the rules file and
  weight recent quarters more by reading them first.
- Never tune the rules to make this week's book look greener.
