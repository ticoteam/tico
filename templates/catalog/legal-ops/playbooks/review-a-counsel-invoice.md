# Review a counsel invoice

Triggered by a task attaching a law firm invoice. Budget 20 minutes. The outcome is a review the owner can act
on in two minutes: the total, the lines to question and why. An invoice action waits unless the task asks for it and a granted Tool allows it.

---

## 1. Match it

    hub task show <id>

Find the firm in `knowledge/firms.md` and the matter in `knowledge/matters.md`. No engagement letter on record: say
so at the top and review only against `knowledge/billing-rules.md`. An unknown matter: flag it first.

## 2. Read every line

For each time entry: date, timekeeper and role, hours, rate, amount, description. Check:
- **Rate** against the letter for that role and date.
- **Units**: time in the agreed increments (default 0.1 hour).
- **Block billing**: several tasks in one entry with one total.
- **Scope**: the work belongs to this matter and its agreed scope.
- **Non-billable**: admin, filing, training, fixing the firm's own errors, internal conferences beyond the rule.
- **Duplicates**: the same work on two dates or by two people without reason.
For each expense: allowed by the rules, receipt shown, within the limit.
Check the arithmetic of every line and the total.

## 3. Write the review

`reports/invoices/<firm>-<invoice>.md`: two lines first (billed, in question, the main reason), the lines to question
in a table with the rule each breaks, budget position after this invoice, then a draft query to the firm in plain
words. It stays a draft unless a person has turned mail sending on in Tico and the task asks for it. Then **Summary for a human, not legal advice.** `hub file publish` it.

## 4. Hand over

Put it on the task for the owner named at setup. Payments need the task to ask for them and a granted Tool to allow them; otherwise they wait for a person. Messages to the firm are drafts on the task and go out only when a person has turned mail sending on in Tico and the task asks for it. Update the
matter's spend to date in `knowledge/matters.md`, commit, and `hub task update <id> --status done --note`.

## Done when
- The review opens with the amount billed and the amount in question, and each questioned line has its date, timekeeper, hours, rate, amount and rule.
- Every line and the total have been recalculated, and the letter and its date are named, or the review says at the top that there is no engagement letter on record.
- The review is published, the matter's spend to date is updated, and the task is closed with the amount in question first.
