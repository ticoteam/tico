---
name: gather-contractor-tax-forms
description: Build the year-end list of contractors paid over the reporting amount, check that each one has its tax form on file, and list the gaps with their owners before the information-return deadline. Run monthly, and weekly in the last two months of the year.
---
# Gather contractor tax forms

When to use: monthly, weekly in the last two months of the year and in January, and when a new contractor is set up.

Steps:
1. Pull the payments to contractors for the year from the books or the payment export. Total by payee, not by invoice, and note the
   export's date range. Also pull the contractors set up this year, paid or not, from the vendor records, because an unpaid new contractor never appears in the payment export.
2. Compare each payee's total with the reporting amount for the year. Take the amount from the authority's current page and write
   its source and date. Do not use a remembered figure.
3. For each payee over the amount, check that the team holds the form the authority requires, with its date. A missing form is a
   gap, named with the contractor and the owner who has the contact.
4. For each payee under the amount, note its total. Check its form anyway if it was set up as a contractor this year, whatever the total. Do not count the list as clear while a payee's total is incomplete.
5. Ask the owner of each gap for the form on their task, one request per task. Never write a taxpayer identifier in the report;
   write "on file" or "missing".
6. Put the list in `knowledge/contractors.md` with the year, the totals and the gap count.

Checks before you finish:
- Each payee appears once, with a total from the same export, and every contractor set up this year is in the check, paid or not.
- Each form is "on file" or "missing", never assumed.
- No taxpayer identifier, bank detail or address appears in the report.
- The reporting amount and its source are written at the top of the list.

Example (invented, for illustration): "Contractor list 2026. Reporting amount from the authority page (read 2026-10-29). Payees
over the amount: 6. Forms on file: 5. Missing: Studio Ravi Co, paid 4,200 (owner: Odile). Payees under the amount: 14, not reviewed for forms, except 2 set up this year, whose forms are on file."
