# The RFQ package

A request for quotation that every supplier can price the same way. Send the identical package to each, on the same
day, with the same deadline.

## Before sending

- The design passed its release gate, and the package is one pinned revision.
- A confidentiality agreement is signed with each supplier that will see drawings. Until then, send only the summary
  section and ask whether they want to quote.
- You know which of the terms below are fixed and which you are asking suppliers to propose.

## The RFQ

```text
RFQ <number> · <product or part name> · revision <X> · issued <date> · answers due <date, time, time zone>
From: <company>, <contact name>, <email>. Questions by <date>; answers go to all suppliers.

1. Summary
   What it is, what it does, and which part of it you are asked to quote
   (bare board / assembled board / moulded part / machined part / full product assembly and test / packaging).

2. Files (all revision <X>)
   <list every file by name>. The drawing governs if the model and drawing differ.
   Critical dimensions and cosmetic surfaces are marked on the drawing.

3. Quantities
   Quote each tier: <tier 1> / <tier 2> / <tier 3> units.
   First order expected: <quantity>. Expected yearly quantity: <quantity or "not yet known">.
   State your minimum order quantity and any price break above the tiers.

4. Dates
   Samples or first articles wanted by <date>. First production delivery wanted by <date>.
   State your lead time for tooling, for first articles after tooling, and for production after first-article approval.

5. Materials and parts
   Material grades, finishes and colours as on the drawings. No substitution without written approval.
   For assemblies: which parts we supply and which you buy. Parts you buy come from the manufacturer or its
   authorised distributors; list any alternates you propose.

6. Quality
   First article: full dimensional report on <n> pieces against the drawing.
   Golden sample: signed units kept by both sides.
   Inspection: outgoing inspection by you; we inspect each lot to ANSI/ASQ Z1.4 (ISO 2859-1), general level II,
   critical 0 accepted, major AQL <2.5>, minor AQL <4.0>, before shipment or on receipt.
   Defect definitions: attached. A rejected lot is sorted, reworked or replaced at your cost.
   Tell us your quality certifications and send your most recent audit or certificate.

7. Compliance
   Markets: <US, EU, ...>. Send materials declarations for these markets with first articles
   (for the EU: RoHS and REACH). Tell us the country of origin of the goods.
   State any test reports or certifications you already hold for similar products.

8. Test (assemblies)
   Our test procedure and limits: attached. Quote the test time and any fixture cost.
   Results recorded by serial number and sent with each lot.

9. Packaging and labelling
   <unit packaging, carton quantity, labels, barcodes, pallet rules>.

10. Commercial terms to quote
   Currency: <USD>. Price valid until: <date, at least 60 days>.
   Delivery terms (Incoterms 2020): quote <FOB named port / EXW / DDP to our address> and say which.
   Payment terms: state yours. Ours: <for example 30% with order, balance after passed inspection>.
   Tooling: quote separately. We own tooling we pay for; state where it is kept, its expected life in shots or
   units, and the cost of maintenance and of a modification.
   Other one-time charges: list each.
   Warranty on workmanship: state the period.

11. How to answer
   Fill in the attached quote table. Do not change its rows. Add notes below it.
   List every exception to this RFQ and every assumption you made.
   Send your design-for-manufacture comments on the files with the quote.

12. How we choose
   Total landed cost, quality evidence, lead time, flexibility on quantity, and the quality of your answers.
   We may ask for references and samples of similar work.
```

## The quote table to attach

Suppliers fill the same rows, so quotes line up.

| Row | Tier 1 | Tier 2 | Tier 3 |
|-----|--------|--------|--------|
| Quantity | | | |
| Unit price | | | |
| Of which material | | | |
| Of which labour and machine time | | | |
| Of which purchased parts (assemblies) | | | |
| Of which overhead and margin | | | |
| Tooling, one time | | | |
| Other one-time charges (list) | | | |
| Minimum order quantity | | | |
| Lead time: tooling | | | |
| Lead time: first articles | | | |
| Lead time: production | | | |
| Delivery terms and named place | | | |
| Payment terms | | | |
| Packaging included? | | | |
| Test included? Time per unit | | | |
| Quote valid until | | | |

A supplier may decline to split the unit price. Note that, and lean more on your should-cost for that quote.

## Rules while the RFQ is open

1. **Same information to all.** Every answer to one supplier's question goes to all, without naming who asked.
2. **Never share one supplier's price or design comments with another.**
3. **No change to the package mid-RFQ.** If the design changes, issue a new revision to all with a new deadline.
4. **Log everything** on the task: sent date, questions, answers, quote received date.
5. **A late or partial quote** is recorded as late or partial. Decide once whether to wait; give the same extra time
   to all.
6. **Read the exceptions first** when a quote arrives. A low price with an exception to the material, the tolerance or
   the inspection is a quote for a different part.

## Choosing who to ask

A supplier belongs on the short list when it:

- makes this kind of part by this process as its normal work, at about this quantity;
- can show a similar part it made;
- answers the first message clearly and within a few working days;
- is willing to sign the confidentiality agreement and to be inspected;
- ships to your market, and is not excluded by the settings.

Ask at least three. For a critical or costly part ask five, so three good quotes come back. Keep one supplier the team
already has a record with on the list where one exists: its observed data anchors the comparison.

## Delivery terms in one table

The term sets who pays for and bears the risk of each leg. Quote and compare on the same one. These are the commonly
used Incoterms 2020 rules; read the rule's own text before relying on it.

| Term | Supplier's price covers | You arrange and pay |
|------|-------------------------|---------------------|
| EXW (ex works) | Goods at their door | Everything from their door: export clearance, freight, insurance, import, duty |
| FOB (free on board, sea freight) | Goods loaded on the ship at the named port, export cleared | Sea freight, insurance, import, duty, delivery |
| CIF (cost, insurance, freight) | To the destination port with basic insurance | Import, duty, delivery from the port |
| DAP (delivered at place) | To your address, not import cleared | Import clearance and duty |
| DDP (delivered duty paid) | To your address, duty paid | Unloading |

DDP looks simple but hides the freight and duty inside the price and can leave the question of who is the importer of
record unclear. Ask for the duty and freight to be shown as separate lines, and confirm who the importer is.
