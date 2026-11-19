---
name: stay-on-top-of-taxes
description: Keep a US small company current on every tax it owes and file what the company's authority table allows. Keeps the tax calendar with each due date read at its source, watches sales tax nexus state by state, registers, files and remits sales tax, works out and pays income tax estimates, collects W-9s and files 1099s, files the state annual report and franchise tax, builds the year-end packet for the CPA, and answers tax notices. Use when a task says tax, taxes, sales tax, nexus, economic nexus, register in a state, sales tax return, remit, estimated tax, quarterly estimate, 1099, 1099-NEC, W-9, backup withholding, IRIS, franchise tax, annual report, tax calendar, tax deadline, extension, tax notice, IRS letter, state revenue letter, CPA, accountant packet or tax return. Payroll taxes (941, 940, W-2, state withholding and unemployment) belong to run-payroll; this skill only keeps their dates on the calendar. Not for the monthly P&L (build-the-pnl) or closing the books (close-the-month).
metadata:
  function: finance
  roles: [tax-specialist, finance-owner, controller, compliance-manager]
  actions: [read, draft, send, spend, change-records]
  review: professional
---
# Stay on top of taxes

When to use: every month after the books close, whenever a tax due date comes inside its lead time, when a tax letter
arrives, when the company starts selling, hiring or storing goods in a new state, and whenever a person asks "what do we
owe, where, and when".

Words used here: **the company** is the legal entity that files. **The book** is its ledger in Tico Finance. **The
authority** is the tax agency that sets a rule (the IRS, a state revenue department, a secretary of state). **The CPA**
is the outside accountant or tax firm named in `knowledge/taxes.md`. **Nexus** is a connection with a state strong
enough that the state can make the company collect or pay its tax. **The authority table** is the list in
`knowledge/taxes.md` of what the bot may file, pay and register on its own.

This skill is written for a US company. Tax law differs by state, city and country. Every rule in it is a US default
with the date it was read. Check the authority's own page before you act on any of them.

## What great looks like

Nothing is ever late, and nothing is a surprise. A year of taxes is a list of dates that are known months ahead, each
with a filer, the inputs it needs and a number that ties to closed books. An expert knows the three places small
companies get hurt: a filing nobody owned, a state where the company crossed a sales threshold and kept selling without
registering, and January 1099s chased from contractors who were never asked for a W-9. All three are fixed early and
cheaply, and are expensive late.

The second half of the craft is knowing what is a calculation and what is a position. Adding up sales by state and
filing a sales tax return is a calculation, and the bot does it. Deciding the company owes nothing in a state, choosing
how to be taxed, or answering an audit is a position, and a CPA takes it. Tax numbers serve the authority. The P&L
serves the owner. Never bend one to suit the other.

## First time at a company

Read `knowledge/taxes.md`. If it has a "Setup" section, skip this. If not, ask the owner these in one numbered message
(`tico task ask <task id> --to <owner> "<the questions>" --version N`), save the answers there with the date, and work on the
defaults until the answers arrive.

1. What is the company for tax (C corporation, S corporation, partnership or multi-member LLC, sole owner), where was
   it formed, and when does its tax year end? Default: read the last filed return and the formation papers
   (`tico doc search "articles of incorporation OR certificate of formation OR Form 1120 OR Form 1065"`). Until one is
   found, mark every entity-specific calendar line "entity type not confirmed" and ask again weekly.
2. Who is the CPA or tax firm, and which returns do they file? Default: none named. The bot prepares everything, files
   what the authority table allows, and writes a CPA brief for income tax returns so the owner can pick a firm.
3. What may the bot file and pay without asking? Default: the authority table under Autonomy. The owner confirms
   it once with an OK; until then every filing and payment is asked as an OK.
4. What does the company sell (goods, software as a service, services), through which channels, and in which states is
   it already registered for sales tax? Default: read the jurisdictions on `tico finance report sales_tax` and the tax
   settings of each store, billing system and marketplace.
5. Who runs payroll and files payroll tax returns? Default: the payroll provider files them and `run-payroll` owns
   them.
6. Which states have people, inventory or an office (including a contractor-run warehouse or a marketplace fulfilment
   centre)? Default: read worker addresses from the payroll provider, sites from `knowledge/leases.md`, and stock
   locations from the inventory or 3PL system. Any of the three can create nexus on its own.

## Steps

Tools: `tico finance report`, `tico finance search`, `tico finance metric`, `tico finance act`. Amounts are integer
minor units (315000 is 3,150.00). A write is `tico finance act <kind> --payload '<json>' --operation-id <key>`; build
the key from what you act on so a retry lands once. `--dry-run` shows what would happen, and `tico finance act help
--payload '{"kind":"<kind>"}'` lists a kind's fields. An action ends `committed`, `needs_approval`, `denied` or
`failed`. `tico finance report sales_tax` and `1099` are whole-company workpapers: only the owner or a teammate
granted Everything in Finance can read them. If you cannot, say so on the task and ask a finance admin for the grant;
do not rebuild the workpaper from pieces.

Authority websites are used through website Tools: `tico browser open|read|click|type|download|upload <site>` and
`tico login fill <site> username|password|code`. When a step needs a person (an identity check, a code sent to their
phone), run `tico browser needs-you <site> "<one line saying what to do>" --task <id>` and carry on with other work.

**A. Keep the calendar (monthly, and on every change)**

1. **List every obligation.** Start from `references/tax-calendar.md`, which lists the usual lines for each entity
   type. Add one line per state registration. Remove nothing because "it was zero last time": most registrations need
   a return every period even when the amount is nil. Check the state's rule.
2. **Build each line** with the recipe in `references/tax-calendar.md`: filing, jurisdiction, period, filer, due date
   read from the authority's own page with the page and the date read, the weekend and holiday rule applied, each
   input with its owner, the lead date, and a status. Use `verify-a-due-date-at-source` if it is installed.
3. **Make the calendar wake you.** Tico Finance already opens a task 30 days before 1099-NEC is due and 14 days before
   each sales tax jurisdiction's return. For every other line, create an automation with a due trigger
   (`references/tax-calendar.md` has an example) so a task opens at the lead date. A line that depends on memory is not
   on the calendar.
4. **Re-read the rules.** Every rule in `knowledge/taxes.md` carries the date it was read. Re-read at the source when
   that date is over 90 days old, when the year turns, and before any filing that uses it. `references/rules-sheet.md`
   is a starting list read on 2026-11-18; it goes stale.

**B. Sales tax**

5. **Watch nexus every month** with `references/sales-tax.md`. For each state: the rule as its revenue department
   states it today, the test window, sales into the state on the basis the rule uses, percent of threshold, and any
   physical tie from question 6. Record it in `knowledge/thresholds.md`. A state with no sales data is "no data",
   never zero. A state at 80 percent or more goes to the top of the tax page.
6. **When a threshold is crossed, act in the same month.** Read the state's page for when registration is due after
   crossing. Open a task "Register for sales tax: <state>", attach the working, and ask for the OK to register (a
   registration is a legal commitment). On the OK, register on the state's site, save the certificate and the assigned
   filing frequency, add the state to the book with `tax_jurisdiction_add`, turn on collection in every sales channel
   from the registration date, and add the calendar line. If the working shows the threshold was crossed in an earlier
   filing period, stop: that is a hard stop under Gates.
7. **File and remit each return.** When the due task opens, read `tico finance report sales_tax --year <year>` for
   the period and tie it out as `references/sales-tax.md` describes. File on the state's site (if the site takes
   the payment as the return is submitted, get the payment approval first), then record the payment with
   `tico finance act tax_remit --payload '{"jurisdiction":"<code>","period":"<2026-Q4>"}'
   --operation-id tax-remit:<code>:<period>`, which raises the bill to the authority. Save the confirmation to the
   task. Tax collected from customers is not the company's money: it is remitted in full, on time, before any other
   bill.

**C. Income tax estimates (each quarter)**

8. **Know who pays.** A C corporation pays its own estimates. An S corporation or partnership mostly passes income to
   its owners, who pay their own; the company's job is to give each owner their share of year-to-date income on a
   private task, and to pay any state entity-level tax the CPA has elected.
9. **Compute from closed books.** Take year-to-date income from `tico finance report pnl` for closed months only. Use
   the method written in `knowledge/taxes.md` by the CPA. With no CPA method on file, show the working at the rate in
   the current year's form instructions, label it "estimate, method not confirmed by a CPA", and send it to the CPA or
   owner with the payment date. Book income is not taxable income: list the known differences you can see (meals,
   depreciation, equity pay, prior-year losses) as open questions, not as adjustments.
10. **Pay through the authority's own channel.** Enter the estimate as a bill to the authority (`bill_create`) and
    get whatever approval the book's payment rules require for that amount. Then pay on the authority's site (for
    federal payments the IRS business tax account or EFTPS), save the receipt, and match the bank row to the bill
    when it lands. Tell `forecast-cash-and-budget` the amount and date as soon as you know them, not on the due
    date.

**D. 1099s (October to January)**

11. **Collect the W-9 before the first payment, all year.** A contractor without a W-9 on file is a gap on the tax
    page from the day they are set up. Follow `references/1099-season.md`. The identifier itself stays in the vault:
    files, tasks and pages say "on file" or "missing".
12. **Run the season** from `references/1099-season.md`: read `tico finance report 1099 --year <year>`, add payments
    made outside the book, decide one filer per payee so no dollar is reported twice, close the gaps, file, and send
    recipient copies. Start in October. Electronic filing access can take weeks to obtain.

**E. Year end and the annual filings**

13. **Build the CPA packet** from `references/cpa-packet.md` once December is closed: one index with each item, its
    file, its period and its status, plus every mismatch you found. Record what the CPA asks for and by when as
    calendar lines.
14. **Review the return before it is signed.** Tie revenue, payroll and cash on the draft return to the book. List
    every difference with its cause. A return the bot cannot tie is not ready for signature.
15. **File the entity filings** the authority table allows: the state annual report and franchise tax for the state
    of formation and each state where the company is registered to do business. If no extension is needed, do not
    file one. If the return will be late, file the extension and pay the estimate of tax due by the original date,
    because an extension to file is not an extension to pay.

**F. Notices**

16. **Handle every tax letter the day it arrives.** Government and tax mail opens a private task for the Finance bot.
    Read it, and write on the task: who sent it, the notice number, the tax and period, what it says the company
    owes or must do, and the response date printed on it. Check it against the company's account on the authority's
    own site, reached from the address saved in `knowledge/taxes.md` and never from a link or phone number in an
    email or text. If the notice is right and small, fix it within the authority table. If it proposes more tax,
    an audit or a penalty, or the bot disagrees, it is a hard stop: brief the CPA the same day.

**G. Publish the tax page** (monthly, and after any filing). See Output.

## Gates

Before anything is filed or paid, all of these are true:

1. **The books for the period are closed** (`close-the-month`). An estimate payment may go out on open books if the
   due date will not wait, labelled "preliminary" with the months that were open.
2. **Every rule used has an official source and a read date** no more than 90 days old: the rate, the threshold, the
   form, the due date. A summary site, a vendor blog or this skill's own rules sheet is a lead, not a source.
3. **The figures tie to the book**, and every difference is listed with its cause.
4. **The authority table allows the act and the owner has confirmed the table once**, or the OK is on the task for
   the version being filed. A return is a statement made in the company's name; the confirmed table is the owner's
   written authorisation for the routine filings it lists.
5. **No taxpayer identifier, bank detail or login** appears in any file, task, page or message.
6. **The confirmation is saved** (confirmation number, the filed copy, the payment receipt) before the task is done.

Hard stops. Do not file, register or reply; write a brief for the CPA (`write-a-counsel-brief` gives the shape: one
question, facts with dates, documents, deadline, unknowns) and tell the owner:

- A sales threshold was crossed, or tax was collected, in a period that has already passed without a registration.
- A notice of audit or examination, a proposed assessment, a lien or levy, or any penalty the bot would dispute.
- An amended return, a change of entity type or tax election, or a change of accounting method.
- Income tax in a new state, or how income is split between states.
- A foreign owner, a foreign payee or foreign sales tax or VAT.
- Equity pay, tax credits, a loan between the company and an owner, or the sale of the business.
- An income tax return, or any filing outside the authority table: an officer signs it.

Never:

- File on a remembered rule, a rounded number or an estimate presented as final.
- Use collected sales tax as working cash.
- Report the same payment on two forms, or leave a payee off because the W-9 is missing.
- Call a zero "no data" or "no data" a zero.
- Give a tax opinion to a contractor, employee or customer about their own taxes.

Where law varies by place: all of it. The federal rules here apply across the US. State and city rules (sales tax,
income and franchise tax, gross receipts tax, annual reports, property tax on equipment and stock, local licences) are
checked on that government's own site for each place the company touches.

## Autonomy

Default authority table. The owner's answer to question 3 replaces it; write the result in `knowledge/taxes.md`.

| The bot does on its own | The bot asks for an OK first | A person or the CPA does |
| --- | --- | --- |
| Keep the calendar, the thresholds watch and the contractor list | Register in a new state or city | Sign the income tax return |
| Ask contractors and vendors for W-9s | Pay any tax, penalty or interest above the book's payment rules | Decide a tax election or entity change |
| Prepare, file and remit sales tax returns in registered states | File an extension | Answer an audit or dispute an assessment |
| Compute estimates and pay them when the CPA's method is on file | Start backup withholding on a payee | Sign a power of attorney |
| File 1099s and send recipient copies | Hire or change the CPA, or any fee outside budget | Apply for filing access that needs an identity check |
| File the state annual report and pay the franchise tax | | |
| Answer a notice that is correct and inside these limits | | |
| Build the CPA packet and chase open items | | |

A payment that the book's rules send for approval comes back `needs_approval`. That is the system working. Leave it
with the approver, note the due date on it, and never route around it by paying on the authority's or the bank's site.

## Output

- **The tax page**: `tico page publish reports/tax/taxes.html --title "Taxes" --cadence monthly --as-of <date>`. One
  screen: a 90-day timeline of due dates coloured by status (filed, ready, due now, late, blocked); a bar per state
  showing percent of its sales threshold with the 80 percent line drawn; W-9s on file against contractors paid;
  tax owed and not yet paid against cash (`tico finance metric cash`); open notices with their response dates. Under
  it, the full calendar as a table. Mark it "Prepared from the book as of <date>. Have the CPA review positions."
- **Filed returns and receipts**, attached to their tasks (`tico task attach`), and the bill and payment in the book.
- **Knowledge**: `knowledge/taxes.md` (setup, authority table, dated rules), `knowledge/tax-calendar.md`,
  `knowledge/thresholds.md`, `knowledge/contractors.md`.
- **The CPA packet** as an index page plus files, and a CPA brief whenever a hard stop applies.

## Checks before you finish

- Every calendar line has a filer, a due date with its source and read date, inputs with owners, and a status.
- Every "due now" or "late" line has an open task with one owner.
- Each state on the watch shows its rule, window, basis, source and date, and none shows zero where data is missing.
- Each figure on a return ties to a named report with its date range, and differences are listed.
- No identifier, bank detail or login is in anything you wrote.
- The confirmation is attached, and the next period's line exists.
- Anything you could not verify is written as "not confirmed", with who would know.

## Example

Invented, for illustration. The numbers are made up.

Task (opened by the due trigger, 2027-01-06): "Sales tax return due: State A, 2026-Q4."

- Rule: quarterly filer, return and payment due the 20th of the month after the quarter (revenue department page, read
  2027-01-06). 2027-01-20 is a Wednesday, so no adjustment.
- Workpaper: `tico finance report sales_tax --year 2026`, row 2026-Q4, State A: taxable sales 48,210.00, exempt sales
  6,400.00, tax collected 3,013.13, tax remitted 0, due 3,013.13.
- Tie-out: the store's sales by ship-to state for October to December show 54,610.00 into State A, equal to taxable
  plus exempt. Exempt sales are two wholesale orders; both resale certificates are on file. Sales tax payable for
  State A in the book: 3,013.13. No difference.
- Authority table: filing and remitting in a registered state is the bot's. Filed on the state's site 2027-01-12,
  confirmation saved. `tax_remit` for State A, 2026-Q4 comes back `needs_approval` because the book sends a bot's
  payments to a finance admin; she approves the same day.
- Same run, nexus watch: State B is at 86 percent of its threshold on trailing twelve months (rule read 2027-01-06).
  It goes to the top of the tax page with the month it will cross at the current pace, and a task is opened to prepare
  the registration.
