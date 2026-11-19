# Incoming quality control and AQL sampling

How to decide, lot by lot, whether to accept what a supplier delivered.

The sampling scheme is ANSI/ASQ Z1.4 (the US standard) or ISO 2859-1 (its international equivalent); their main
tables agree. **The tables below are an extract written from memory on 2026-11-18. One row (lot size 3,201 to 10,000)
was confirmed against a published chart that day; the rest were not. Check every plan against your own copy of the
standard before a lot verdict depends on it**, and name the standard and edition in the purchase order.

## Before the first lot: agree the standard

Sampling only works if both sides agreed, in the order, what is being checked. Four things:

1. **The reference.** The drawing at its revision, the specification, and the signed golden sample.
2. **The defect list.** Every defect you will look for, each in one class:
   - **Critical**: could injure someone, or makes the product illegal to sell (exposed live part, sharp edge, wrong
     or missing safety marking, a battery fault).
   - **Major**: the product does not work, will fail early, or a customer would return it (does not power on, wrong
     dimension on a critical fit, a deep scratch on a main cosmetic face).
   - **Minor**: a departure from the standard that most customers would not return it for (a small mark on a hidden
     face, slightly misplaced label).
   Write each with a photo or a limit: "scratch longer than 2 mm on face A", not "cosmetic damage".
3. **The plan.** Inspection level and an AQL for each class. Default: general inspection level II; critical: none
   accepted; major AQL 2.5; minor AQL 4.0. Tighter levels (major 1.0 or 1.5) suit products where a failure is costly.
4. **What happens to a failed lot.** Sort, rework, replace or credit, at whose cost, within how many days.

AQL is not a target and not a promise that defects are allowed. It is the worst average quality the sampling plan will
usually accept. A lot that passes at AQL 2.5 can still contain defective units.

## Small lots: inspect everything

For lots under 150 units (the default in the settings), inspect 100%. The sample for a small lot is a large share of
the lot anyway, and each unit matters more. Also inspect 100% for: any pilot run; any check for a critical safety
defect that is quick to do; and the first lot from a new supplier or a new tool.

## Picking the sample size (general inspection level II)

| Lot size | Code letter | Sample size |
|----------|-------------|-------------|
| 151 to 280 | G | 32 |
| 281 to 500 | H | 50 |
| 501 to 1,200 | J | 80 |
| 1,201 to 3,200 | K | 125 |
| 3,201 to 10,000 | L | 200 |
| 10,001 to 35,000 | M | 315 |

For other lot sizes and other inspection levels, read the standard.

## Accept and reject numbers (single sampling, normal inspection)

Accept the lot for a class if defects found are at or below "Ac". Reject at or above "Re".

| Code | Sample | AQL 1.0 Ac / Re | AQL 2.5 Ac / Re | AQL 4.0 Ac / Re |
|------|--------|-----------------|-----------------|-----------------|
| G | 32 | see standard | 2 / 3 | 3 / 4 |
| H | 50 | 1 / 2 | 3 / 4 | 5 / 6 |
| J | 80 | 2 / 3 | 5 / 6 | 7 / 8 |
| K | 125 | 3 / 4 | 7 / 8 | 10 / 11 |
| L | 200 | 5 / 6 | 10 / 11 | 14 / 15 |
| M | 315 | 7 / 8 | 14 / 15 | 21 / 22 |

Critical defects: accept 0, reject 1, whatever the sample. Where the standard's table shows an arrow instead of
numbers, it sends you to a different sample size; "see standard" marks such a cell.

## Running one inspection

1. **Confirm the lot.** One part number, one revision, one production run. If a delivery mixes runs, treat each as its
   own lot. Record lot size from the packing list and count the cartons.
2. **Find the sample size** from the lot size.
3. **Draw the sample at random.** Choose cartons across the whole delivery (a common rule is the square root of the
   number of cartons, picked from different pallets and positions), and units from different layers of
   each carton. Never let the supplier pick the units. If a person or an inspection firm is doing the physical work,
   give them these rules in writing.
4. **Inspect each unit** against the checklist: packaging and labels; appearance against the golden sample; the
   critical dimensions with the stated gauge; function with the test procedure; any special checks (pull test, drop
   test, barcode scan) on the smaller sub-sample the order names.
5. **Record every defect** with its class, a photo, and the unit it was found on. A unit with several defects counts
   once, in its worst class.
6. **Count and compare.** Critical, major and minor totals against Ac and Re for that sample.
7. **Give the verdict.**

   | Result | Verdict | What happens |
   |--------|---------|--------------|
   | All classes at or under Ac | **Accept** | Release the lot. Defective sample units go back or are scrapped |
   | Major or minor at or over Re | **Reject** | Tell the supplier with the record. They sort, rework or replace under the order's terms. Re-inspect the reworked lot as a new lot on tightened inspection |
   | Any critical defect | **Reject and stop** | Hold the whole lot. Tell the owner and the engineer the same day. Check whether earlier lots could be affected |
   | The team wants to use a failed lot anyway | **Hold for the owner** | An OK with the engineer's written reason, the sorting plan and the price adjustment. A bot never accepts a failed lot |

8. **File the record** in `reports/hardware/qc/<part>-<lot>.md` and update the supplier scorecard.

## The inspection record

```text
Part / revision:            Supplier:               Order:            Lot:
Lot size:                   Cartons:                Inspected at: factory / our dock     Date:        By:
Standard: ANSI/ASQ Z1.4 <edition>, single sampling, <normal / tightened / reduced>, level II
Sample size:                Cartons opened:
                Found   Ac   Re   Result
Critical                 0    1
Major (AQL __)
Minor (AQL __)
Defects: <class · description · count · photo>
Measurements: <critical dimension · nominal · tolerance · min and max measured>
Verdict: Accept / Reject / Reject and stop / Hold for the owner
Supplier told on:           Corrective action asked: <yes/no, due date>
```

## Tightening and relaxing

The standard has switching rules; use them rather than judgment.

- **Normal to tightened**: when two of five consecutive lots have been rejected. Tightened plans keep the sample size
  and lower the accept numbers; read them from the standard's tightened table.
- **Tightened back to normal**: after five consecutive lots are accepted.
- **Normal to reduced**: only after a long run of accepted lots (ten in a row is the starting condition, with others
  in the standard), steady production, and the owner's OK.
- **Stopping deliveries**: the standard says when to stop accepting lots from a supplier that stays on tightened
  inspection. Read the count in your edition, and raise it with the owner before that point.

## Where and who

- **Before shipment, at the factory**, by a third-party inspection firm, is the default for imported goods: a rejected
  lot is fixed before it travels. Book through the firm's order form with the checklist, defect list and plan
  attached. The cost goes into landed cost.
- **On receipt**, by a person on the team or a warehouse partner, for domestic suppliers and as a spot check on
  inspected lots.
- A bot prepares the plan, the checklist and the record, reads the inspector's report, applies the accept and reject
  numbers, and gives the verdict. A person or a firm handles the goods.

## Beyond sampling

Sampling finds bad lots; it does not make good ones. When the same defect appears in two lots, ask the supplier for a
corrective action: the cause, the fix at the process, and the evidence that it worked. Sort failures by cause before
blaming the supplier: an unclear drawing or a damaged golden sample is yours to fix.
