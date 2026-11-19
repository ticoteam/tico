# Quarter end and year end

The provider files. The bot proves it filed the right thing, and gets the inputs right before the last run, because
after December 31 every fix is a corrected form.

## Each quarter (in the month after it ends)

1. Once the provider shows them as filed, download: the federal quarterly return (Form 941), each state's
   withholding and unemployment returns, and the deposit confirmations. If nothing shows as filed five business
   days before a due date, ask the provider why that day.
2. Tie total wages and tax on the returns to the sum of the quarter's registers, and to wages and payroll tax in the
   book. List any difference with both figures.
3. Confirm each return shows as accepted, not only submitted. Providers have rules for filings that keep failing;
   Gusto's help pages say a filing that fails three times must be filed outside Gusto (search summary read
   2026-11-18; check the provider's own page). A failed filing becomes a task for the bot the same day.
4. Mark the lines filed on the tax calendar with the confirmation attached.
5. Read every agency letter that arrived in the quarter.

Federal dates as read on 2026-11-18 (`rules-sheet.md` has sources): Form 941 is due April 30, July 31, October 31
and January 31. Form 940 and Forms W-2 are due January 31. A date on a weekend or holiday moves to the next business
day; January 31, 2027 is a Sunday.

## November

1. **Which forms the provider files.** Read the provider's list for federal forms and for each state the company is
   in. Write what it does not file; those are the bot's or the CPA's.
2. **Addresses, names and identifiers.** Ask every employee and contractor to confirm their details in the provider.
   A name that does not match the Social Security card causes a mismatch when the W-2 is filed.
3. **Taxable benefits and odd items** go in before the last run of the year, so tax is withheld on them: bonuses,
   gift cards, taxable fringe benefits, health insurance for S corporation owners, anything that failed the
   reimbursement plan. Ask the CPA in November which apply; write the list in `knowledge/payroll.md`.
4. **Contractors.** Run the 1099 steps in `stay-on-top-of-taxes` (`references/1099-season.md`): totals by payee,
   missing W-9s chased, one filer per payee. Payments made outside the provider must reach whoever files.
5. **Last run dates.** A pay date in January belongs to next year's W-2 even if the work was in December. Put the
   last pay date of the year and the provider's deadline for it on the calendar.
6. **Next year's inputs.** New wage bases and rates apply from the first run of January: the Social Security wage
   base, state unemployment wage bases and rates, state minimum wages and exempt salary floors. The provider updates
   federal figures itself; state unemployment rates come from the state's notice and the bot enters them.

## December

7. Run the last regular payroll. Any bonus run goes through the same gates.
8. After the final run, compare year-to-date totals in the provider with the book, by wages, employee taxes and
   employer taxes.

## January

9. **Preview the W-2s** before the provider files. For each employee: name and address, state, wages against the
   sum of their year's registers, and benefits in the right boxes. Person-level checks stay in a file scoped to the
   task.
10. **Tie the W-3 total** to the four Forms 941 and to the book. A difference is found now or by the agencies later.
11. **Preview the 1099-NECs**: payee, total, and that card payments are left out.
12. **Delivery.** Employees and contractors get their forms by the due date, electronically only where they
    consented. Save the provider's filing confirmations for W-2, W-3, 1099-NEC, Form 940 and each state.
13. **Corrections.** A wrong W-2 or 1099 already issued is a hard stop for the first one of a kind: brief the CPA,
    then correct through the provider.

## Keep

Employment tax records are kept at least four years (IRS Publication 15 (2026)). Forms I-9 have their own retention
rule; read the USCIS page. State rules can be longer. Records stay in the provider and the vault, not in knowledge
files.
