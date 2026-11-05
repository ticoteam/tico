# Review a DPA

Triggered by a task that attaches a data processing agreement, usually from a business customer. Budget 30
minutes. The outcome is a list of differences from the team's position and the questions for counsel. A
summary for a human, not legal advice. Nothing is signed or sent.

---

## 1. Read it whole

    hub task show <id>

Read the DPA, its annexes (processing description, security measures, subprocessor list, transfer clauses) and
whatever main agreement it attaches to. Name any annex that is missing.

## 2. Check the terms a processor contract must cover

Subject matter and duration; nature and purpose; types of personal data and of people; acting only on documented
instructions; confidentiality of staff; security measures; subprocessors (prior authorisation, notice of changes,
objection right, the same duties flowing down); help with data subject requests and with security and impact
assessments; deletion or return at the end; audits and information; breach notice and its time limit; transfers
outside the country and their mechanism; liability and whether it sits outside the main agreement's cap.

## 3. Compare with the team position

For each term: `knowledge/dpa-position.md`, the DPA's text with its clause number, and the difference. A term with
no team position is "no team position" and a question for the decision-maker. Check the DPA's subprocessor
annex against `knowledge/subprocessors.md`: a vendor missing from either side is a flag.

## 4. Hand over

Write `reports/dpas/<party>.md`: three lines first (what it is, how many differences, the one that matters most),
then the table, then questions for counsel, then **Summary for a human, not legal advice.** `hub file publish`
it and put it on the task. The reply to the customer is a human's.

## Done when
- `reports/dpas/<party>.md` lists every term of the processor checklist as "same", "different" or "no team position", each with its clause number.
- Every subprocessor in the DPA is on `knowledge/subprocessors.md`, or flagged.
- The review is published and on the task, and the summary line closes it.
