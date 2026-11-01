# Weekly delivery and exceptions report

Schedule: Mondays at 08:00 team time (routine `weekly-delivery-report`), after setup. Budget 35 minutes. The outcome is one report, and on the task the customer updates,
claims and invoice disputes ready to use. Nothing is sent or filed.

Inputs: the shipment export for the week, carrier invoices received, `knowledge/carriers.md`, `knowledge/exceptions.md`
and `knowledge/claims.md`.

Done when: every section of the report is written, or says "none"; every claim within 7 days of its deadline is at the
top; every overcharge shows its rate and source; the report is published; and the task note carries the headline and the
export dates.

---

## 1. Measure last week

From the shipment export: shipments delivered last week, on time (by the promised date), in full (no
missing items reported), by carrier and by lane (origin region to destination region). Lanes with
fewer than 10 shipments are grouped as "other". Run `skills/measure-on-time-by-lane/SKILL.md`.

## 2. Refresh the exceptions

Every shipment late past the threshold, stuck (no scan for 3 working days), returned to sender,
damaged or lost. For each: last scan and time, carrier case (open one if none, through the carrier's process only when mail sending is on in Tico and a granted Tool allows it; otherwise draft it on the task), what the customer has been told. Work each with `playbooks/work-an-exception.md`.

## 3. Claims

Claims to file (lost or damaged, with value and evidence), claims filed and waiting, and every claim
within 7 days of the carrier's deadline at the top. Run `skills/check-a-claim-before-the-deadline/SKILL.md` for each.

## 4. Invoice check

For each carrier invoice received: run `skills/reconcile-a-carrier-invoice/SKILL.md`, which compares each line with `knowledge/carriers.md`. List overcharges
(wrong rate, a surcharge not in the contract, a duplicate charge, a charge for a shipment that was
never collected) with the amount and the rate's source. Disputes are prepared for review.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-deliveries.md` in the shape of `knowledge/examples/delivery-report.md`, with these sections in order: headline; on time and in full; ready to act on (claims to file, customer updates ready); open exceptions; invoice check; sources with the export date. `hub file publish` it, commit, and `hub task update <id> --status done --note` with the headline.
