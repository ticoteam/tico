# The proposal and the business case

A proposal is the document the champion forwards to the person with the money. Write it for that reader: someone who
was not on the calls, has three minutes, and wants to know what problem this solves, what it costs, what they get
back and what could go wrong.

One page. If it needs more, the extra goes in an appendix nobody has to read.

## Before writing

All of these must be in the deal record:

- Pain, Impact and Decision met; Critical Event known.
- The buyer's own words for the problem.
- The inputs for the business case, each with who supplied it.
- The price and terms, read from the current list today (`check-quotes-against-the-current-text`).
- Anything promised in calls checked with `check-a-feasibility-claim-before-it-is-promised`.

If one is missing, get it. Do not write around it.

## Layout

```
<Buyer>: <the result they want, in their words>
Prepared for <champion's role> and <decision-maker's role>, <date>. Valid until <date>.

1. The problem
   Two or three lines in their words, with the example they gave.
   What it costs today: <figure>, <per period>. Source: <their figures, given on date>.

2. What changes
   Three to five lines: what they will be able to do, in the order of their day.
   By when: <date>, which meets <their critical event>.

3. The business case
   (table below)

4. Options
   Up to three, one marked "recommended" with one line on why.

5. Price and terms
   From the price list dated <date>. Payment terms. Contract length. What is included, what is not.

6. What we need from you, and what happens next
   The two or three steps on their side with dates, taken from the mutual action plan.
   The one next step, with a date.

7. Risks and open points
   Anything not yet confirmed, stated plainly, with who is resolving it and when.
```

## The business case table

| Line | Figure | Whose number | How it was worked out |
|---|---|---|---|
| Cost of the problem today, per <period> | | Theirs | <frequency> x <cost each>, from <who>, <date> |
| Expected after | | Assumption / evidence | <state the basis> |
| Difference per <period> | | Calculated | Line 1 minus line 2 |
| Price per <period> | | Ours | Price list, <date> |
| One-time costs (setup, their staff time) | | Ours / theirs | |
| Net per <period> | | Calculated | |
| Time to pay back | | Calculated | One-time costs divided by net per period |

Rules:

- **Every input is labelled** theirs, ours or an assumption. Theirs names who gave it and when.
- **An assumption is called an assumption** in the document, in words, beside the number.
- **Evidence for the "after" line** is, in order of strength: a measured result from this buyer's own pilot; a
  measured result from a named customer who has agreed to be quoted; a published study, cited; a stated assumption.
  If all you have is the last, say so and show the break-even instead: "this pays for itself if it prevents N of
  those a month". A break-even needs no claim about the future and is usually more persuasive than a forecast.
- **Use their cost of the problem, not an industry figure.** If they could not give one, the business case is not
  ready.
- **Include their costs:** the time their people spend switching, anything they must buy besides, the overlap period.
  A business case that leaves these out gets taken apart by their finance person.
- **No guaranteed results,** no "up to" figures, no averages across customers presented as what this buyer will get.
- **Show the arithmetic.** Anyone should be able to redo it with a calculator.
- **Round honestly** and use their currency and their period (per week, per job, per month).
- **Soft benefits go in words, not in the total.** "Fewer angry calls" is real; do not price it unless they did.

## Claims check

Before sending, list every factual claim in the proposal and beside each write its source:

| Claim | Source | Status |
|---|---|---|
| "<claim>" | Price list / product docs / the buyer, date / customer evidence on file / none | Keep / fix / cut |

A claim with "none" is cut or sourced. This covers features, integrations, dates, security statements, customer
names and results. A customer is named only with their permission on record.

## Options

- At most three. More than three delays a decision.
- Each differs in a way the buyer cares about (scope, speed, term), not just in price.
- Mark one as recommended and say why in one line tied to their goal.
- Do not include a decoy option you hope they will not pick.
- If one option is clearly right, offer one.

## Price and terms

- Copy from the current price list and standard terms. State the date of the list.
- Give the total they will actually pay in the first year, including one-time costs and any tax you can state.
- State what is not included.
- Give an expiry date for the quote. Default: 30 days, or the end of the current price list if sooner.
- A discount, a free period, a custom term or a non-standard payment schedule is not in the proposal unless an OK is
  on record for exactly that.
- Pricing questions about packaging or discount patterns go to the pricing skills (`compare-prices-like-for-like`,
  `read-discounts-as-a-distribution`) and the set-and-test-prices skill, not into a one-off deal.

## Sending

- Draft the email with `tico email draft <mailbox> --to <address> --subject "<subject>" --body-file <file>
  --attach <proposal file>`; an attachment may need a person's OK under the team's sending policy, and Tico will ask.
- The email body carries the whole case in five lines, so it works unopened: the problem and its cost, what they
  get, the price, the pay-back or break-even, the next step and date.
- Send to the champion, and offer to walk the decision-maker through the numbers in ten minutes.
- Record the version sent. If anything changes, send a new version with what changed at the top.

## A pilot or trial instead of a proposal

When they need to see it work first, agree in writing before it starts:

- what will be measured, and how;
- the pass mark, as a number;
- the start and end dates (short: two to four weeks is a common default, set by how long the measure takes to show);
- who does what on each side;
- what happens on a pass: the order they will sign, at what price.

A pilot with no pass mark and no "then what" is a free trial that ends in silence. A paid pilot, or one that needs
engineering time, needs an OK.
