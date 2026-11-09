---
name: find-unbilled-work
description: Find delivered work with no invoice by comparing this cycle's sources (usage above plan, approved hours, signed-off milestones, add-ons agreed in tasks) against the invoices issued, and record each finding with its source and value.
---
# Find unbilled work

When to use: once per cycle, after the invoice run is built, and when a delivery owner says work was done for a customer. Unbilled work is a leak that
no one notices until it is too late to bill, so it is looked for on every run.

Steps:
1. List the sources for the period: usage above the plan in the contract, approved timesheets, milestones signed off, and add-ons agreed in tasks or
   messages. Each source has a date.
2. For each customer, match each delivered item to an invoice line in this run or a prior one. An item with no matching line is a candidate.
3. Confirm each candidate: the contract says the item is billable (a rate, a clause, a signed order), and the delivery is evidenced (the timesheet
   approved, the milestone signed, the usage final). A candidate with no billing basis goes to the owner as a question, not to the run.
4. Value each confirmed item from its source: hours times the contract rate, usage over the plan times the overage rate, milestone fee as stated.
5. Record each finding in `knowledge/unbilled.md`: customer, item, period, value, source, and the date it was found. Add it to the run as a proposed line,
   held until its owner approves.
6. Check the age. An item unbilled for more than one cycle is named as aged, and the owner is told the same day when it passes 1,000 in value.

Checks before you finish:
- Every finding has a billing basis and an evidence source, or it is a question.
- No finding is valued from memory or an estimate.
- Each finding that is billed later is closed in `knowledge/unbilled.md` with the invoice number.

Example (invented, for illustration): "Cedar Barre, 6 onboarding hours, 2026-09-10 to 2026-09-18. Timesheets approved by the delivery lead on 2026-09-19. Contract rate 150 an hour (clause 2). Value 900. Proposed as a line on the 2026-10-01 invoice, held until the owner approves."
