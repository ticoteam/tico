---
name: review-a-dpa-against-the-position
description: Review a customer's data processing agreement term by term against the team's position in knowledge/dpa-position.md, check its subprocessor annex against the team's list, and write the differences with clause numbers and the questions for counsel.
---
# Review a DPA against the position

When to use: when a customer sends a data processing agreement for the team to sign or answer.

Steps:
1. Read the DPA, its annexes (processing description, security measures, subprocessors, transfers) and any main agreement it attaches to. Name any annex that is missing.
2. Go through the processor checklist in `playbooks/review-a-dpa.md` step 2. Note each term as present, missing or unclear, with its clause number.
3. For each term, compare the DPA's text with `knowledge/dpa-position.md`. Write it as same, different (with the gap in one line) or "no team position".
4. Check the DPA's subprocessor annex against `knowledge/subprocessors.md`. A vendor missing from either list is a flag.
5. Check whether liability for data breaches sits inside or outside the main agreement's cap, and say which clause sets it.
6. Write the questions for counsel: only the terms with no team position, or where the gap changes the risk.
7. Write `reports/dpas/<party>.md` with three lines first, then the table, then questions, then the summary line.

Checks before you finish:
- Every difference quotes the DPA's words and gives its clause number.
- No term is judged without a position. "No team position" is written instead.
- Every subprocessor in the DPA is on the team list or flagged.
- The transfer mechanism is named as the DPA names it. Nothing is said to be adequate.

Example (invented, for illustration): "Breach notice, clause 6.3: 'without undue delay and in any event within 24 hours'. Position: 72 hours. Different: 48 hours shorter than the team's position."
