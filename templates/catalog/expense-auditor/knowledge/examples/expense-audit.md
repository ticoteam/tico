A sample of excellent output for a fictional team. Every name in it is a stand-in.

```markdown
# Acme expense audit, September 2026 (draft, not yet reviewed)

Sample output for Acme, a fictional studio-software team. Every person and amount is invented.
Nothing has been approved, rejected or sent. Finance only.

**Headline: 214 lines checked (18,930); 12 exceptions (1,642). Two need a decision: a duplicate
candidate (412) and a flight booked above the policy class (690).**

## Sources
- Card export `acme-cards-2026-09.csv`, 2026-09-01 to 2026-09-30, 171 rows, 9 cards.
- Reimbursement export `acme-claims-2026-09.csv`, 43 rows, 12 people. Receipts: 196 files.

## Owner: Marco (sales team)
- **Duplicate candidate.** 2026-09-14, Harbor Grill, 412.00: on Omar's card and in Lena's claim
  CL-0913 (same receipt file `r-0914-harbor.jpg`). Rule: one reimbursement per expense (policy 2.1).
- **Flight class.** 2026-09-19, 690.00 premium economy, 3 h flight. Rule: economy under 6 h (policy 4.2).
  Resolves with: a source record, or the fare difference noted.
- 5 meals over the 60 per person limit, total 38 over. Grouped; your call.

## Owner: Priya (support team)
- **Missing receipts.** 3 card lines over 75, total 311 (rows 44, 61, 102). Rule: policy 1.3.

## Late claims
- Tomas, claim CL-0921: 2 lines from 2026-07-12, 79 days old, 191.00. Rule: submit within 60 days (policy 1.5).

## Could not read
Card 7 (ending 2231) has no rows for September; check whether it was cancelled or the export missed it.
```
