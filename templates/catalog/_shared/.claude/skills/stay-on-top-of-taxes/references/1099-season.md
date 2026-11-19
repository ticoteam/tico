# W-9s and 1099s

The federal figures here were read on the IRS pages listed in `sources.md` on 2026-11-18 and apply to payments made in
2026, reported in early 2027. Read the current Instructions for Forms 1099-MISC and 1099-NEC before each season.

## All year: the W-9

1. Ask every new contractor and every vendor that is not plainly a large corporation for a Form W-9 before the first
   payment. Send the request from the task; the form comes back through the vault or the payroll provider's own
   onboarding, never as an attachment in chat.
2. Record in `knowledge/contractors.md`: payee, set up on, W-9 "on file" with its date or "missing", what the W-9
   says the payee is (individual, partnership, corporation, LLC and how it is taxed), and who holds the contact.
   Never write the identifier.
3. A payee who will not give an identifier may have to be paid less 24 percent backup withholding (IRS Topic 307,
   updated 2026-09-24). Starting it is the owner's OK; it means deposits and a return the company otherwise does not
   file. Until the OK, hold the payment and say why on the task.
4. Include contractors set up this year who have not been paid yet. They never appear in a payment export.

## The season

**October**

1. Read `tico finance report 1099 --year <year>`. Columns: `year`, `vendor`, `tin_on_file`, `cash_paid`,
   `card_paid_excluded`, `threshold`, `reportable`. The threshold in the workpaper is 2,000 dollars for 2026 and
   later and 600 dollars before. Check it against the IRS instructions for the year and write the source.
2. Add what the book does not hold: contractors paid through the payroll provider, and payments through a payment app
   or another bank. Total by payee across all of them, for the calendar year, by the date paid.
3. Do not merge two payees because the names look alike. List the pair and ask the teammate who holds the contact.
4. Decide what is reportable. US defaults for 2026 payments:
   - Payments for services to a non-employee totalling 2,000 dollars or more go on Form 1099-NEC.
   - Rents and similar totalling 2,000 dollars or more, royalties of 10 dollars or more, and gross proceeds paid to an
     attorney of 600 dollars or more go on Form 1099-MISC.
   - Payments by credit card, debit card or a third-party payment network are left off. The card company or network
     reports them on Form 1099-K.
   - Payments to a corporation are generally left off, except fees for legal services. Whether a payee is a
     corporation comes from its W-9, not from its name.
   A case that does not fit (a foreign payee, a prize, a settlement, a reimbursement paid with the fee) is a question
   for the CPA, listed by payee.
5. Decide **one filer per payee**. The payroll provider files for contractors paid through it. If a payee was paid
   both ways, either give the provider the outside payments or file the outside payments yourself. No dollar is
   reported twice and none is left out.
6. Check filing access. The IRS takes information returns electronically through IRIS and requires e-filing at 10 or
   more information returns of all kinds combined. Using IRIS needs a Transmitter Control Code, and the application
   can take up to 45 days, so for forms due on February 1, 2027 it must be in by December 18, 2026. Applying needs a
   person's identity check: open a sub-task for the owner now. The older FIRE system is being retired, and IRIS is
   the only intake system for the 2027 filing season (IRS e-file page, updated 2026-10-26).

**November and December (weekly)**

7. Chase each missing W-9 on its own task with the teammate who holds the contact. Recompute totals after each
   payment run. A payee near the threshold is watched, not ignored, and the list is not "clear" while any payee's
   total is incomplete. A contractor set up this year has their form checked whatever their total.
8. Check which states want their own copy. Some take it through the IRS, some want it filed with them directly, and
   their thresholds may differ from the federal one. Read each state's page for every state where a payee lives.

**January**

9. After the last payment of the year has cleared, freeze the totals: note the export's date range and the date
   pulled. Prepare each form. Have a second look at every payee whose total changed since December.
10. Furnish the recipient copy and file with the IRS by the due date: January 31 for Form 1099-NEC, or the next
    business day when that is a weekend or holiday. In 2027, January 31 is a Sunday, so the working date is Monday
    February 1; confirm on the IRS page.
11. Save the filing confirmation, the list of forms sent and how each recipient copy was delivered. Put the season's
    summary in `knowledge/contractors.md`: year, payees reportable, forms filed, gaps left and why.

**After filing**

12. A wrong amount or identifier is fixed with a corrected form as soon as it is found. Write what changed and why,
    file the correction, send the recipient the corrected copy.

## Never

- Write an identifier, a bank detail or a home address in a task, a page or a report.
- Leave a reportable payee off because the W-9 never came. File with what is known and tell the CPA.
- Use last year's threshold. It changed for 2026 and may be adjusted for inflation from 2027.
- Tell a contractor how to report the income on their own return.
