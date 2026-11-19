---
name: source-and-certify-hardware
description: Get a hardware product made and legal to sell. Writes the request for quotation (RFQ) package, builds a should-cost estimate before quotes arrive, sends the same package to at least three suppliers, puts quotes on one landed-cost basis, scores suppliers on quality, cost, lead time, minimum order quantity (MOQ) and risk from observed data, runs first-article inspection, sets incoming quality control with AQL sampling and a lot verdict, and plans certification by market, FCC for the United States, CE marking for the European Union, and UL or another safety listing, with lab quotes and a schedule. Every regulatory fact carries the date it was read. Use when a task says RFQ, quote, supplier, vendor selection, factory, contract manufacturer, MOQ, lead time, should-cost, landed cost, tariff, duty, tooling, first article, golden sample, incoming inspection, AQL, QC, defect rate, FCC, CE, UKCA, UL, RoHS, REACH, certification, compliance testing, test lab, declaration of conformity, or "can we sell this in".
metadata:
  function: hardware
  roles: [procurement-manager, vendor-manager, quality-inspections, inventory-planner, logistics-coordinator, compliance-manager]
  actions: [read, draft, send, spend, change-records]
  review: professional
---
# Source and certify hardware

When to use: when a design has passed its release gate and needs suppliers; when quotes arrive and must be compared;
when goods arrive and must be accepted or rejected; and from the first prototype onward, to plan what the product
needs before it can be sold in each market.

Words used here: an **RFQ** (request for quotation) is the package sent to suppliers so they can price the work.
**MOQ** is the minimum order quantity. **Lead time** is order to delivery. **Tooling** is the custom equipment a
supplier needs, paid once. **NRE** is any other one-time charge. **Landed cost** is everything paid to get one good
unit to the door: price, tooling share, freight, duty, inspection, scrap. **Should-cost** is your own estimate of what
a part ought to cost, built from materials and process. A **first article** is the first unit off the real process,
measured against every drawing dimension. A **golden sample** is the approved reference unit both sides keep. **AQL**
(acceptance quality limit) sampling inspects a set number of units from a lot and accepts or rejects the lot on the
defects found. A **lot** is one delivery made in one run.

This skill sits beside two others. `review-hardware-for-manufacture` decides whether the design is ready and hands
over the pinned release package. `pay-bills-and-buy` owns purchase approvals, purchase orders and paying. Use it for
the money steps; do not repeat its checks here.

On `review: professional`: a compliance engineer or the test lab confirms the certification plan before lab money is
spent and before anything is declared. A lawyer checks a supply or tooling agreement before it is signed. A person
signs every declaration of conformity. A customs broker confirms tariff classification before it is relied on.

## What great looks like

- **Suppliers quote the same thing.** One frozen package, the same quantities, the same terms asked of all. Quotes
  that cannot be compared are not quotes yet.
- **You know the price before you ask.** A should-cost estimate turns "is this a good quote?" into "which line is
  different, and why?"
- **Total cost, not unit price.** Tooling, freight, duty, inspection, scrap, payment terms and the cost of a late
  delivery are in the comparison.
- **Evidence over promises.** A supplier is scored on samples measured, deliveries timed and defects counted. A stated
  lead time is a claim until a delivery proves it.
- **Quality is agreed before the order.** The drawing, the golden sample, the defect list and the sampling plan are in
  the purchase order. An inspection with no agreed standard is an argument.
- **Certification is planned from the first prototype.** The cheapest compliance decision is a design decision: a
  pre-certified radio module, a listed power supply. The plan is checked against the regulators' own current pages,
  and every fact in it has a date.

## First time at a company

Read `knowledge/hardware-sourcing.md`. If it has an "Our settings" section, skip this. If not, ask the owner these in
one numbered message, save the answers there with the date, and work on the defaults until they reply.

1. **Where will it be sold, and through whom?** Default: United States and European Union, sold direct online. Large
   retailers and marketplaces add their own requirements; name them if planned.
2. **What is inside?** A radio (Wi-Fi, Bluetooth, cellular), a battery and its chemistry, mains power, parts touching
   skin or food, anything for children. Default: read it from the BOM and the requirements, and ask only what they do
   not answer.
3. **How many, at what target cost, by when?** Default: the quantity and date on the task; quote three quantity tiers
   around it.
4. **What quality level?** Default: general inspection level II; critical defects none accepted, major AQL 2.5, minor
   AQL 4.0; 100% inspection instead of sampling for lots under 150 units.
5. **Any limits on suppliers?** Countries to avoid, a single-supplier tolerance, payment terms. Default: at least
   three quotes for any custom part or assembly; no supplier without a signed confidentiality agreement sees drawings;
   no more than 30% paid before a first article passes.
6. **Who signs?** Purchase orders above the budget, tooling orders, supply agreements, declarations of conformity.
   Default: the owner signs all four.

## Steps

### A. Source

1. **Freeze the package.** Take the pinned release package from the release gate: one revision, verdict Release or
   Release with conditions. Build the RFQ with `references/rfq-template.md`: what to make, the files, three quantity
   tiers, target dates, quality requirements, packaging, the terms you want quoted, the format of the answer and the
   deadline. If a supplier has no confidentiality agreement with the team, send that first (the `review-a-contract`
   skill) and only a summary until it is signed.
2. **Build the should-cost.** Before quotes arrive, follow `references/should-cost.md`: material from the drawing and
   a dated material price; process time and rate; overhead, scrap and margin as stated assumptions; tooling spread
   over the quantity; freight and duty. Every input has a source and a date, or is marked "assumption". The output is
   a range, not a point.
3. **Find and send.** Build a long list from the team's past suppliers (`tico doc search`, and `tico finance search`
   for vendors already paid), the designer's suggestions and directories. Cut to at least three that make this kind
   of part at this quantity. Send the identical RFQ to each on the same day with the same deadline
   (`tico email draft`, `tico email send`). When one supplier asks a question, send the answer to all.
4. **Normalise the quotes.** Put every quote in one table on the same basis: quantity tiers, currency and the exchange
   rate with its date, delivery terms, tooling and NRE, lead time for samples and for production, MOQ, payment terms,
   what the price includes, and how long the quote is valid. Write "not in quote" for anything missing and ask for
   it. Then compute landed cost per good unit at each tier. For duty, read the current rate for the product's tariff
   code on the official tariff schedule that day; record the code, rate, source and date; never reuse a rate more
   than 30 days old.
5. **Score the suppliers.** Follow `references/supplier-scorecard.md`. Apply the kill criteria first. Then score each
   remaining supplier on quality, landed cost, lead time and delivery, MOQ and flexibility, engineering and
   communication, and risk, each with its evidence. Prefer observed data: measured samples, timed deliveries, defect
   counts from earlier lots, references called. Mark each score "observed" or "stated".
6. **Compare with should-cost.** For each quote, set its lines against your estimate. A quote far above it: ask for a
   breakdown and name the line. A quote far below it is a risk, not a win: ask what is different in material, process
   or inspection. Use the gaps to negotiate on facts.
7. **Recommend and award.** Publish the comparison page with one recommendation, the runner-up, and what would change
   the choice. The order itself goes through `pay-bills-and-buy`. The purchase order must carry: drawing and BOM
   revision, the golden-sample clause, the inspection plan and AQL levels, who owns the tooling, delivery terms,
   payment milestones tied to a passed first article, and what happens to a rejected lot. Tell the suppliers not
   chosen, briefly.

### B. Qualify and receive

8. **First article.** Before production, the supplier builds first articles from the real process. Every dimension and
   note on the drawing is measured on each, by the supplier and again by the team or a third-party inspector. Record
   the results in a table against the drawing. Approve, approve with a listed deviation the engineer accepts, or
   reject with the dimensions that failed. An approved unit is signed and dated as the golden sample; the supplier
   keeps one and the team keeps one.
9. **Pilot run.** For a new supplier or a new tool, a small first lot inspected 100%. Count yield and sort failures by
   cause (the `sort-failures-by-cause` skill). Each cause gets a corrective action with an owner before volume.
10. **Incoming quality control, every lot.** Follow `references/incoming-qc-aql.md`: choose the sample size from the
    lot size and inspection level; draw the sample at random across cartons; inspect against the drawing, the golden
    sample and the written defect list; count critical, major and minor defects; compare with the accept and reject
    numbers; give the lot a verdict of accept, reject, or hold for the owner. Inspect before shipment at the factory
    where you can, through a third-party inspector, so a rejected lot is not already on the water. Record every lot.
11. **Keep the scorecard live.** After each lot, update the supplier's observed lead time, on-time record and defect
    rate. Two rejected lots in a row, or a critical defect, opens an issue and tightens inspection. A supplier's
    record, not its quote, decides the next award. A supplier whose record holds keeps the work: check each re-order
    against should-cost instead of re-bidding it, and go back to three quotes only when a trigger in the scorecard
    fires.

### C. Certify

12. **Map what applies.** Follow `references/certification-map.md`. From the markets and the contents of the product,
    list each rule that applies, the route to comply, who must be named (a manufacturer, an importer, a responsible
    person in the market), and what must be on the product, the box and in the manual. Then **verify each line at the
    regulator's own page that day** and write the date read. Save the result as `knowledge/hardware-compliance.md`.
    The reference file is a starting map dated 2026-11-18; it is not the law. Plan a safety listing (UL or another
    recognised lab's mark) for any product with mains power or a lithium battery, or one headed to a retailer or
    marketplace. For anything else, get the quote and let the owner decide.
13. **Cut the work by design.** Raise these with the designer before DVT: a radio module that already has its own
    approvals, used exactly within its conditions; an external power supply that already carries the safety marks; a
    battery cell or pack with its transport and safety test reports; ESD and emissions basics in the layout. Revisit
    the module choice only when real quotes at the planned volume show its extra unit cost exceeds the cost of
    certifying a radio designed onto the board.
14. **Pre-scan early.** A pre-compliance emissions scan on a prototype finds most failures cheaply. It is not a
    certification and its result is never quoted as one.
15. **Get lab quotes.** Ask at least two labs accredited for the tests needed, and for US radio certification, labs
    and certification bodies currently recognised by the FCC. Each quote lists the tests, the standards and their
    editions, the number of samples and their configuration, lead time, cost, and the cost of a retest. Never estimate
    lab costs; use the quotes.
16. **Schedule backwards from the ship date.** Samples that represent production, lab time, a retest allowance,
    paperwork, labels and packaging artwork. Put each date on a task. Certification testing normally runs on DVT
    units; a design change after testing may require retesting.
17. **Assemble the file and the declaration.** Test reports, the technical file the market requires, the declaration
    of conformity drafted for a person to sign, label and manual text, and the importer or responsible-person details.
    Keep the file for the period the rule sets.
18. **Watch for change.** Any change to a radio, an antenna, power circuitry, firmware that affects radio behaviour,
    or the enclosure near them goes back to step 12 before it ships. Put the known future dates from the map on the
    calendar as tasks, and re-read the whole map every quarter.

## Gates

- **No drawings without a confidentiality agreement.**
- **No award from fewer than three comparable quotes** for a custom part or assembly, unless the owner OKs a named
  exception with its reason.
- **No number without a source and a date.** Prices, exchange rates, duty rates, lead times and every regulatory fact.
  A duty rate older than 30 days is re-read.
- **Stated is not observed.** A supplier's claim is labelled as a claim. Scores built only on claims are marked.
- **No production before a passed first article** and a signed golden sample.
- **No lot accepted without its inspection record.** A lot that fails is not "accepted this once" by a bot.
- **A critical defect stops the lot.** One is enough. Critical means it could hurt someone or break a law.
- **Nothing is called certified until the certificate, grant or signed declaration exists.** A pre-scan, a module's
  approval or a supplier's word is not the product's approval.
- **No regulatory statement from memory.** Each one is read at the source and dated, or marked "not verified".
- **A person signs** declarations, agreements and tooling orders.
- **Tariff classification is confirmed by a customs broker** before it is used in a price that is promised to anyone.

## Autonomy

Do on your own: write the RFQ and the should-cost, find suppliers, send RFQs to suppliers under a confidentiality
agreement, answer their factual questions, normalise and score quotes, negotiate on facts inside the target, publish
the recommendation, place orders inside your budget through `pay-bills-and-buy`, book a third-party inspection inside
budget, run incoming inspection and accept a passing lot, reject a failing lot back to the supplier under the terms of
the order, update scorecards, build the compliance map, get lab quotes, and schedule the work.

Ask for an OK, once, with the page attached:

- any order above the budget, every tooling order, and any deposit;
- awarding to a single source, or from fewer than three quotes;
- accepting a lot that failed, a deviation, or a rework plan;
- booking a certification lab (it is spend, and the samples and dates commit the team);
- signing or submitting anything to a regulator or certification body;
- a supply agreement, after the lawyer's check.

## Output

- **The quote comparison page.** A table of suppliers by landed cost per good unit at each tier, a bar for each
  supplier's cost split (unit, tooling share, freight, duty, other), the scorecard with observed or stated marked, the
  should-cost range beside each quote, and one recommendation.
- **The supplier scorecard**, kept in `knowledge/suppliers/<supplier>.md` and updated per lot.
- **First-article and lot inspection records** in `reports/hardware/qc/`, each with its verdict.
- **The compliance map** in `knowledge/hardware-compliance.md`: market by rule, route, who signs, marks, date read,
  source link.
- **The certification plan page**: a timeline from now to ship date with tests, labs, samples and costs from quotes.
- **Tasks** for every dated step, and orders and bills through the finance skill.

## Checks before you finish

- Every supplier got the same package and deadline, and every answer to a question went to all.
- The quote table has no blank cell: a value, or "not in quote" and a question sent.
- Landed cost includes tooling share, freight, duty, inspection and scrap, with the duty rate read today.
- Each score names its evidence and says observed or stated.
- The should-cost shows its assumptions, and each large gap to a quote has a question or an answer.
- The order names the revision, the golden sample, the inspection plan and tooling ownership.
- Each lot has a record: lot size, sample size, defects by class, accept and reject numbers, verdict.
- Every line of the compliance map has a source link and a date read from this month.
- Nothing is described as certified, compliant or approved without the document that says so.

## Example

Invented for illustration. The suppliers, parts and numbers are made up.

**Quotes for the Trailhead sensor enclosure, revision C, 2,000 units.** Should-cost range: $2.10 to $2.60 a unit plus
tooling.

| | Alder Plastics | Brightway Moulding | Corran Tooling |
|-|----------------|--------------------|----------------|
| Unit price | $2.35 | $1.48 | $2.72 |
| Tooling | $9,800 | $6,500 | $11,200 |
| MOQ | 1,000 | 5,000 | 500 |
| Lead time, production | 5 weeks (stated) | 4 weeks (stated) | 6 weeks (observed, last two orders) |
| Landed cost per good unit | $7.94 | not comparable: MOQ 5,000 | $9.05 |
| Samples measured | 5 of 5 in tolerance | none sent | 5 of 5 in tolerance |

Brightway's unit price is 30% under the low end of should-cost; asked what resin grade was quoted, and the answer was a
different grade from the drawing. Removed on a kill criterion (does not quote to the drawing). Recommendation: **Alder
Plastics**, with Corran as runner-up for its observed delivery record. Tooling order needs the owner's OK (task 3410).

**First lot from Alder, 2,000 units.** Level II gives a sample of 125. Found: 0 critical, 4 major, 9 minor. Limits for
this sample at AQL 2.5 and 4.0: accept up to 7 major and 10 minor. **Verdict: accept.** The four majors were all short
shots at the same boss; corrective action asked of Alder, due before the next lot.
