# The supplier scorecard

One scorecard per supplier, used twice: to choose between quotes, and after every lot to decide whether to keep
buying. Keep it in `knowledge/suppliers/<supplier>.md`.

## Step 1: kill criteria

A supplier that fails any of these is out, whatever its price. Record which one.

1. Will not sign a confidentiality agreement, or shared another customer's drawings with you.
2. Did not quote to the drawing: a different material, tolerance or process, not declared as an exception.
3. Cannot meet the MOQ or the quantity: the order is far below their minimum or far above their capacity.
4. Will not accept inspection or the agreed sampling plan, or will not replace a rejected lot.
5. Cannot supply the materials declarations the markets require.
6. Is excluded by the settings (country, ownership, a past failure), or appears on a government restricted-party list.
   Screen the company name on the official consolidated list for your country that day and record the date.
7. Sent samples that failed critical dimensions and could not say why.
8. Asks for full payment before anything is made.

## Step 2: score what is left

Score each factor 1 to 5 against the anchors. Give the evidence for every score and mark it **observed** (you measured
it, timed it or counted it) or **stated** (they told you). A stated score counts, but it is shown as stated, and any
factor scored only on statements is listed under "to verify".

| Factor | Default weight | 1 | 3 | 5 | Best evidence |
|--------|---------------|---|---|---|----------------|
| Quality | 30% | Samples out of tolerance; no inspection records | Samples in tolerance; basic outgoing inspection | Samples in tolerance with full report; process controls shown; clean lot history with you | Measured samples, first-article report, your own lot records |
| Landed cost | 25% | Highest landed cost per good unit | Middle | Lowest, and consistent with should-cost | The normalised quote table |
| Lead time and delivery | 15% | Longest; or has delivered late to you | Fits the date | Fits with margin; on-time record with you | Dates of past deliveries against promised dates |
| MOQ and flexibility | 10% | MOQ far above need; no flexibility | MOQ near need | MOQ at or below need; will split lots or hold stock | The quote; their written answer |
| Engineering and communication | 10% | Slow, vague answers; no DFM comments | Clear answers in a few days | Useful DFM comments; fast, precise answers; one named contact | The RFQ thread itself |
| Risk | 10% | New, unknown, single site, your only source, weak terms | Some record; acceptable terms | Long record, references checked, sound terms, a second source exists | References called, terms in the quote, public records |

The weights are defaults. Change them in the settings for the product: a safety-critical part might put quality at
50%; a commodity part might put cost at 40%. Fix the weights before quotes arrive, not after.

Total = sum of score × weight. Show the total, but decide from the table: a supplier with a 1 on quality is not
rescued by a 5 on cost.

## Rules that keep the score fair

- **Same evidence for all.** If one supplier's samples were measured, measure all, or score that factor "stated" for
  everyone.
- **Observed beats stated.** Where the team has its own record with a supplier, use it and ignore the brochure.
- **Separate their fault from yours.** A late delivery caused by a late design change, a defect caused by an unclear
  drawing, or a delay at customs is not the supplier's score. Record the cause before scoring. (The same idea as the
  catalog skill `score-a-crew-fairly`.)
- **Enough data.** With fewer than three lots, write the counts ("2 of 2 on time"), not a percentage.
- **No score without a sentence.** Each number has one line saying what it rests on.

## Step 3: references and checks before award

For the leading supplier:

- Call or write to two customers they name. Ask: were lots on time, what was the defect rate, how did they handle a
  problem, would you award to them again?
- Confirm the legal entity, address and bank details match across the quote, the agreement and the invoice. A change
  of bank details by email is verified by phone to a known number before any payment (see the `pay-bills-and-buy` skill).
- For a large or long commitment, arrange an audit or a third-party factory visit. A bot cannot visit; a person or an
  inspection firm does.

## Step 4: after each lot

Update the record:

| Lot | Ordered | Promised | Delivered | Days late | Lot size | Sample | Critical | Major | Minor | Verdict | Cause if supplier's |
|-----|---------|----------|-----------|-----------|----------|--------|----------|-------|-------|---------|---------------------|

Then recompute, from observed data only:

- **Observed lead time**: order date to delivery date, median and longest. Use this, not the quoted lead time, for
  reorder planning (the catalog skill `compute-a-reorder-point`).
- **On-time**: lots delivered by the promised date, as a count of total.
- **Lot acceptance**: lots accepted first time, as a count of total.
- **Defect rate**: defects found in samples over units sampled, by class.
- **Responsiveness**: days to answer a corrective-action request.

## Triggers

| What happened | What you do |
|---------------|-------------|
| One rejected lot | Corrective-action request to the supplier; next lot on normal inspection |
| Two of five lots rejected | Tightened inspection; open an issue; ask the runner-up to re-quote |
| A critical defect | Stop shipments of that lot; tell the owner the same day; engineer reviews; consider a recall check for shipped units |
| Late twice running | Add the observed delay to planning; ask for a recovery plan |
| Price rise on re-quote | Re-run should-cost with current inputs before answering |
| Ten lots accepted in a row | Consider reduced inspection, with the owner's OK |
| Supplier changes material, process, site or sub-supplier | Treat as a new first article |

## The scorecard page

```markdown
# <Supplier>
Entity, site, contact. Parts supplied. Agreement and tooling held. Last updated <date>.

## Scores
| Factor | Score | Observed or stated | Evidence |

## Record
<the lot table>

## Open actions
<corrective actions with owner and due date>

## To verify
<every stated claim not yet observed>
```
