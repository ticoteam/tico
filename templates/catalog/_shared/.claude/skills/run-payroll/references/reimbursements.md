# Reimbursements

A reimbursement paid under an **accountable plan** is not pay: no tax is withheld and it does not go on the W-2. One
that fails the plan is wages, taxed like any other pay. The plan is three tests, from the IRS rules (Publication 463
and Treasury Regulation 1.62-2; the publication's chapter was not re-read on 2026-11-18, so confirm the day counts
there):

1. **Business connection.** The expense was for the company's business.
2. **Substantiation within a reasonable time.** Amount, date, place and business purpose, with the receipt. The
   commonly cited safe harbour is 60 days after the expense.
3. **Return of any excess within a reasonable time.** An advance not spent comes back. The commonly cited safe
   harbours are advances no more than 30 days ahead and excess returned within 120 days.

Write the company's own limits in `knowledge/payroll.md`. Defaults: claims within 60 days, receipt for every item,
no advances.

## Handling a claim

1. **Read the claim**: who, each item's date, amount, vendor, purpose, and the receipt.
2. **Check each item.**
   - Receipt present and readable; its amount and date match the line.
   - Business purpose stated in the claimant's words. "Lunch" is not a purpose; "lunch with <customer> about the
     renewal" is.
   - Inside the plan's time limit.
   - Not already paid: search the book for the same vendor, amount and date on a company card or an earlier claim
     (`find-duplicate-expense-candidates` if installed).
   - Allowed by the company's expense policy. No policy on file means the owner's OK for anything unusual.
3. **Mileage.** Miles, date, from, to, purpose. Pay at the IRS business rate for the date driven: 72.5 cents a mile
   from 2026-01-01 to 2026-06-30 and 76 cents from 2026-07-01 to 2026-12-31 (IRS standard mileage rates page, updated
   2026-07-28). Read the page for the current year. A higher rate than the IRS one makes the excess wages.
4. **Decide each item**: pay; return to the claimant with the one thing missing; or treat as wages (fails a test and
   the company still chooses to pay it).
5. **Pay.** Employees: as a non-taxable reimbursement line in the next run, or as a bill. Contractors: reimbursed
   expenses follow their contract, usually on the invoice; ask the CPA once whether the company's contractor
   reimbursements belong in the 1099 total, and write the answer down.
6. **Book it** to the expense's own category with the receipt attached, not to wages.

## What needs an OK

- An item outside the policy, or with no receipt.
- A claim from the owner or from the person who approves claims: someone else approves it.
- Anything treated as wages, since it changes the person's pay.

## Never

- Reimburse from a card statement line alone.
- Pay a recurring allowance (phone, home office, car) as a "reimbursement" without substantiation. A flat allowance
  with no receipts is wages unless the CPA says otherwise.
- Hold a valid claim past the next run. Some states require prompt repayment of work expenses; read the work state's
  labour page.
