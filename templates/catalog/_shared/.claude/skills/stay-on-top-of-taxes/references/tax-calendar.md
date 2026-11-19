# The tax calendar

The calendar lives in `knowledge/tax-calendar.md`. One line per filing or payment per period.

## The recipe for one line

1. **Name it.** The filing or payment, the jurisdiction, the period it covers, and the filer: the bot, the payroll
   provider, the CPA, or a named person.
2. **Take the due date from the authority's own page** or from the notice itself. Fetch the page
   (`tico doc fetch <url>`), and write the page and the date you read it. Do not copy a date from last year's line.
3. **Apply the weekend and holiday rule** the authority states, and write the arithmetic: "January 31, 2027 is a
   Sunday; next business day is Monday February 1, 2027".
4. **List the inputs** the filer needs and name an owner for each: closed books for the period, sales by state, the
   payroll provider's reports, the contractor list.
5. **Set the lead date**: the due date minus the lead time. Default lead time is 15 days; 30 days for anything that
   needs a person, a CPA or a new login.
6. **Give it a status**: ready (all inputs in), due now (inside the lead time with an input open), filed (confirmation
   saved), late, or blocked (with who).
7. **Ask each owner of an open input** on their own task, one question per task.
8. **Say what happens next period**, so the line recreates itself.

If two sources give different dates, show both with their pages and write "date not confirmed". Work to the earlier
one.

## The line format

```
| Filing | Where | Period | Filer | Due | Source, read on | Inputs (owner) | Lead date | Status | Next |
```

## Lines most US small companies need

Dates below are the federal defaults read on 2026-11-18; `rules-sheet.md` has the sources. Read each one again before
you rely on it, and shift every date for a fiscal year that is not the calendar year.

| Line | Who has it | Federal default | Filer by default |
| --- | --- | --- | --- |
| Corporate income tax return (Form 1120) | C corporations | 15th day of the 4th month after year end | CPA |
| S corporation return (Form 1120-S), partnership return (Form 1065) | S corporations, partnerships, multi-member LLCs | 15th day of the 3rd month after year end | CPA |
| Extension (Form 7004) | Any of the above, when needed | Same date as the return; automatic 6 months to file, not to pay | CPA or bot with an OK |
| Corporate estimated tax | C corporations expecting to owe 500 dollars or more | 15th day of the 4th, 6th, 9th and 12th months of the tax year | Bot |
| Owners' estimated tax (Form 1040-ES) | Owners of pass-through companies | April 15, June 15, September 15, January 15 | The owner; the bot supplies the numbers |
| Form 1099-NEC to recipients and the IRS | Anyone who paid reportable contractors | January 31 | Bot or payroll provider |
| Form 1099-MISC | Anyone with reportable rents, royalties and similar | Recipients January 31; IRS February 28 on paper, March 31 e-filed | Bot |
| Payroll returns and deposits (941, 940, W-2, state) | Employers | See `run-payroll` | Payroll provider |
| State income or franchise tax return and estimates | Each state where the company is taxed | Read the state's page | CPA |
| State annual report and franchise tax | State of formation and each state of registration | Read the state's page (Delaware corporations: March 1) | Bot |
| Sales tax return | Each state of registration | The frequency and day the state assigned | Bot |
| Local business licence, gross receipts or property tax on equipment and stock | Depends on the city and county | Read the local page | Bot |

Also ask the CPA once a year which lines apply that this list does not carry: foreign ownership or accounts, tax
credits, and state entity-level taxes on pass-through companies.

## Making a line wake you

Tico Finance seeds two kinds of due dates itself: 1099-NEC (a task opens 30 days before) and each sales tax
jurisdiction's return (14 days before, from the `due_day` given to `tax_jurisdiction_add`). For every other line,
create an automation whose trigger is a due date and whose step is a turn for this bot. `tico automation guide`
prints the full format. Save it with `tico automation create --file -`, sending the JSON on stdin. An example:

```json
{"name": "Annual report and franchise tax: Delaware", "owner": "bot:<this bot's slug>", "enabled": true,
 "trigger": {"type": "due", "first_due": "2027-03-01", "repeats": "yearly", "open_days": 30,
             "tz": "America/New_York"},
 "steps": [{"id": "file", "type": "bot_turn", "enabled": true, "bot": "<this bot's slug>",
            "prompt": "Delaware annual report and franchise tax. First re-read the due date at the source and apply the weekend rule. Then follow stay-on-top-of-taxes, step 15."}]}
```

`repeats` is `once`, `weekly`, `monthly`, `quarterly` or `yearly`. `open_days` is how many days before the due date the
task opens; set it to the line's lead time. A repeating trigger copies the same day each time, which is why the
prompt starts by re-reading the date.

## Rules for the calendar

- A due date without a source and a read date is not a due date.
- The filer receives the inputs by the lead date, not on the due date.
- Only "due now", "late" and "blocked" lines go to the top of the tax page.
- A line is removed only when the registration behind it has been closed with the authority, and the closing
  confirmation is saved.
