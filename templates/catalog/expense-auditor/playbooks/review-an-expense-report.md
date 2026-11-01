# Review an expense report

Triggered by a task with one expense report to check before its owner signs it. Budget 10 minutes.

Inputs: the report and its receipts, `knowledge/policy-rules.md`, and `knowledge/approvers.md` for who signs.

Outcome: a review note with the verdict "Pass" or the count of questions, each question with its rule. Nothing is approved.

Done when the note is on the task for the owner and the task is closed with the verdict.

---

## 1. Read the report

    hub task show <id>

Note who submitted it, who approves it, the period, each line and each receipt.

## 2. Check

Run the seven checks from `playbooks/monthly-expense-audit.md` on each line. Check 5, duplicates, always runs across this report's own lines first, then against any card export that covers the same period. Then two report-level checks: the report
total equals the sum of its lines, and each currency conversion uses the rate on the card statement or the receipt date.

## 3. Write the note

Use `skills/write-an-owner-review-note/SKILL.md`. Keep it under 150 words.

## 4. Hand over

Put the note on the task for the owner, then `hub task update <id> --status done --note "..."` with "Pass" or the count of
questions. If the owner grants an exception, record it in `knowledge/exceptions.md` with who granted it and when.
