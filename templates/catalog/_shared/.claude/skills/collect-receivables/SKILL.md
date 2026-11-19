---
name: collect-receivables
description: "Get the company paid. Covers the whole receivables loop: invoice correctly and on time, apply incoming cash to the right invoices, read the aging report, check before chasing, run the reminder stages from a pre-due heads-up to a final notice, handle promises, disputes, short payments and payment plans, and write off what cannot be collected under set rules. Use when a task says invoice, bill a customer, accounts receivable, AR, aging, overdue, late payment, payment reminder, dunning, chase, collections, unapplied cash, cash application, short pay, remittance, DSO, credit hold, bad debt, write-off, or when Tico opens an 'invoice overdue' task."
metadata:
  function: finance
  roles: [accounts-receivable-specialist, billing-specialist, finance-owner, revenue-accountant, controller, account-manager, success-manager]
  actions: [read, draft, send, change-records]
  review: none
---
# Collect receivables

When to use: every time work is delivered and needs an invoice, every time money arrives, once a week for the whole
list, and whenever Tico opens an "invoice overdue" task (`finance.invoice.overdue`, at 1, 7, 30, 60 and 90 days late).

Words used here: **the company** is the business owed the money. **The book** is its ledger in Tico Finance. **Aging**
is the list of unpaid invoices sorted by how late they are. **Cash application** is matching a payment that arrived to
the invoice it pays. **Unapplied cash** is money received and not yet matched. **A short pay** is a payment for less
than the invoice. **Dunning** is the sequence of reminders. **DSO** is days sales outstanding: how many days of sales
are sitting unpaid.

## What great looks like

The best collections work looks like good customer service. The customer gets a correct invoice the day the work is
done, sent to the person who pays, with the purchase order number they need and a way to pay in one tap. If it goes
late, they get a short, true, friendly note that assumes an oversight. They are never chased for something they
already paid or already disputed.

An expert does four things an amateur skips.

- **Applies cash before chasing.** Most "late" invoices on a messy list are paid invoices nobody matched.
- **Chases by who will pay, not by size.** A good payer who slipped once pays on the first note. A chronic late payer
  needs a call and a date. A customer in dispute needs the dispute fixed, not a reminder.
- **Gets a date, not a promise.** "When will it be paid?" and then follows up the day after that date.
- **Calls a loss a loss.** An invoice nobody will pay is written off under a rule, with the trail kept, so the
  receivables number means something.

Speed matters: an invoice is easiest to collect in its first 30 days, and gets harder every month after.

## First time at a company

Read `knowledge/collections.md`. If it has answers, skip this. If not, ask the owner these in one numbered message,
save the answers there with the date, and work on the defaults until they reply.

1. **Terms, and how customers pay.** Default: net 30 from the invoice date, by the payment link on the invoice. Read
   the three newest contracts to confirm. (Subscriptions on a saved card fail differently; see
   `references/dunning-ladder.md`, "Failed card payments".)
2. **Who do reminders come from?** Default: this bot, by name, from the company's billing mailbox, in a plain friendly
   voice. Run `tico email mailboxes` and `tico email policy --bot <your slug>` to see what you may send.
3. **May reminders go out without a person's OK on each one?** Out of the box the 1, 7 and 30 day reminders need no
   Finance OK; they go out as the company's sending policy allows (a reminder counts as a money email). The 60 and 90
   day reminders, any reminder once the invoice is 60 days late, and the final notice wait for an OK. Recommend: set
   the sending policy so the early reminders and the pre-due heads-up send on their own.
4. **What consequences exist in your contracts?** Late fee, interest, pausing service, collections. Default: none. A
   reminder never mentions a consequence that is not written in the customer's contract and decided by the owner.
5. **Who is handled by hand?** Default: no do-not-chase list. Key accounts (the five largest by revenue in the last 12
   months) still get reminders, and their account owner is told on the task at each stage.
6. **Write-offs.** Default: a leftover of 25.00 or less on an invoice (a bank fee, a rounding difference) is written
   off without asking, up to 250.00 a month in total. Anything else is an OK. Tune both numbers to the company's
   invoice size.

Keep per-customer facts in `knowledge/customers.md`: the payables contact, their portal or PO rules, disputes,
promises with dates, payment history notes, do-not-chase.

## Steps

Tools: `tico finance report ar_aging`, `tico finance search --kind invoice` (filters: `status`, `contact_id`,
`aging`), `tico finance search --kind feed`, `tico finance metric ar_overdue`, `tico api GET finance/invoices/<id>`
(total, paid, remaining, due date, days late, payment link), and `tico finance act` for writes. Amounts are integer
minor units: 115000 is 1,150.00. Every write carries an `--operation-id` built from what it touches, so a retry lands
once. `tico finance act help --payload '{"kind":"<kind>"}'` lists a kind's fields.

**A. Invoice right, the day the work is done**

1. **Find what should be billed.** Run `find-unbilled-work` each cycle: usage over plan, approved hours, signed-off
   milestones, add-ons agreed in tasks.
2. **Check each invoice before it goes** with `check-an-invoice-before-issue`: amount against the source, rate against
   the contract, legal entity and payables contact, PO and tax, terms and number sequence, no duplicate.
3. **Create and send**: `invoice_create`, then `invoice_send`. Send it to the customer's payables contact, not only
   the buyer. The invoice shows the PO number, the due date as a date, and the payment link. If the customer uses a
   supplier portal, the invoice is not "sent" until it is in the portal; note the portal reference.
4. **For an invoice above the key-account size, or a customer with a late history,** send a pre-due heads-up 3 to 5
   days before the due date: one line confirming they have the invoice and asking if anything is missing.

**B. Apply cash first, every day**

5. **Match what arrived.** For each incoming bank or processor row not yet matched
   (`tico finance search --kind feed --q "<customer>"`), find its invoice and record it:
   `invoice_record_payment` with the `invoice_id`, the amount and the row's `feed_id`. Follow
   `references/cash-application.md` for the hard cases: no remittance, one payment for several invoices, short pays,
   overpayments, processor payouts net of fees, a payer whose name differs from the customer.
6. **Never apply on a guess.** A payment you cannot place stays unapplied and visible, and you ask the customer which
   invoices it covers. If Tico refuses a row because it may already be recorded (it says `feed_match`), do not retry:
   ask the invoice's owner.

**C. Read the list, weekly**

7. **Pull and tie the aging.** `tico finance report ar_aging`, with its as-of time. The total equals Accounts
   receivable on `tico finance report balance_sheet --end <today>`. If it does not, find the gap before trusting any
   row.
8. **Clean it.** Net unapplied cash and credit notes against the invoices they belong to. A customer covered by an
   unmatched payment is not late. An open credit with no invoice is a finding. The same invoice number twice, or the
   same customer, amount and date twice, is a duplicate until checked.
9. **Bucket and measure.** Not yet due, 1 to 30, 31 to 60, 61 to 90, over 90: count, amount and change on last week.
   DSO = receivables at the as-of date / credit sales over the last 90 days x 90, with the sales figure and its source
   written out. Also the share of receivables over 60 days.
10. **Score each late customer from their own history**: pays on time, sometimes late, usually late. Use their last
    six invoices: average days from due date to payment. A new customer has no score; say so.

**D. Check before you chase**

11. Run these five checks, in order, on every invoice before any reminder. Any hit means no reminder, with the reason
    recorded.
    1. **Paid?** The invoice's `remaining`, a matching feed row, the payment processor. A row that matches an invoice
       already showing some `paid` may be the same money: ask, do not record twice.
    2. **Disputed or promised?** `knowledge/customers.md`. An open dispute or query stops the chase on the disputed
       amount. A promise with a date not yet passed stops it until the day after.
    3. **Do-not-chase?** A name on the list is skipped and named as skipped.
    4. **Recent thread?** `tico email search <mailbox> "<customer address>"`. An unanswered reply from the customer is
       answered first. A payment question from them is a dispute until resolved.
    5. **Past the ladder, or on a hold?** Past 90 days, or with a decision pending from the owner, no automatic
       reminder goes.

**E. Chase, in the right order**

12. **Order the work**: broken promises first; then on-time payers who just went late (one note usually does it); then
    the largest balances among sometimes-late customers; then the usually-late, who get the firm stage and a request
    for a date. Size alone never sets the order.
13. **Send the stage's reminder** with `references/dunning-ladder.md`. Tico's own reminder is
    `invoice_remind` with the `invoice_id` and `stage` (1, 7, 30, 60 or 90), keyed
    `invoice-overdue:<invoice id>:<stage>`. Tico writes its fixed text and puts the email in its send queue; do not
    redraft it. When one customer has several invoices late, or the situation needs words of its own (a reply to a
    query, a payment plan, a final notice), write one note for the customer covering every open invoice, with
    `references/reminder-writing.md`, and send it with `tico email` under your sending policy.
14. **Report delivery** with `invoice_delivery` (`delivered` true, or false with the reason), so the record shows what
    the customer actually received. A bounced address is fixed the same day: find the right payables contact.
15. **Ask for a date and write it down.** Every reply that says "we'll pay" becomes a line in
    `knowledge/customers.md`: amount, date promised, who said it, where. The day after a missed date, the promise is
    broken: move to the next stage at once and say that the date passed.

**F. Disputes, plans and holds**

16. **A dispute** stops reminders on the disputed amount only; the rest is still owed and still chased. File a task
    for the teammate who owns the customer or the delivery, with the customer's words, the contract clause and the
    evidence of delivery, due in five business days. Resolve it one of three ways: the customer is right (credit note),
    the company is right (evidence sent, chase resumes), or split (credit for part).
17. **A payment plan** is offered when a customer cannot pay in full and says so: equal parts, the first now, dates in
    writing. A plan whose last part is due within 90 days is yours to agree; a longer one is an OK.
18. **A hold on new work or service** is the strongest lever and it affects the customer, so it is the owner's call.
    Recommend it at 60 days with no promise, with what the contract allows.

**G. Call a loss a loss**

19. **Write off under the rules** in `references/write-offs.md`: small leftovers inside the limit from question 6 on
    your own; anything else with the evidence trail and an OK. Use `invoice_write_off`. A written-off invoice stays on
    a "written off, still owed" list; if it is paid later, record the recovery.
20. **At month end, give the close a doubtful-accounts figure** by aging bucket, from the company's own loss history
    (`references/write-offs.md`).

**H. Report**

21. Publish the weekly receivables page (see Output) and finish each overdue task with one line: what was recorded,
    and whether the email was delivered, failed or waits.

## Gates

- **No reminder before the five checks.** A reminder to a customer who paid costs more goodwill than a week's delay.
- **Every fact in a reminder comes from the invoice**: number, amount, due date, payment route. No amount is
  rephrased or rounded.
- **No consequence is named unless it is in that customer's contract and the owner has decided to use it.** No late
  fee, interest, suspension, collections or legal wording by default.
- **Nothing false, nothing that shames, no guess about the customer's finances, no debt mentioned to anyone outside
  the customer's own payables contacts.**
- **A dispute stops the chase on the disputed amount** until it is resolved.
- **One reminder per invoice and stage.** Tico refuses a second; never change the text or remake a stage to get it
  through.
- **Never apply cash by guess**, and never record a payment that may already be recorded.
- **A stale list is not chased.** If the aging was read more than a day ago, read it again first.
- **Consumers and some small businesses have legal protections.** See `references/dunning-ladder.md`, "Law", before
  any final notice, any phone call or text, or any hand-off to a collections agency.

## Autonomy

Do on your own:
- Create, check and send invoices for delivered work under a signed contract or a standard price.
- Record payments, apply cash, net credits, fix a wrong contact.
- Send the pre-due heads-up and the 1, 7 and 30 day reminders, and answer customers' payment questions, wherever the
  company's sending policy allows it. Where it does not, the email waits for a person's OK on its preview; carry on
  with the rest and note what waits.
- Record promises, open dispute tasks, agree a payment plan inside the limits in step 17.
- Write off small leftovers inside the limit in `knowledge/collections.md`.
- Issue a credit note that corrects a plain billing error (wrong quantity, duplicate line), with the evidence.

Needs an OK (`tico task ask <id> --to <person> --kind ok "<line>" --version N`, with the page or text shown):
- **The final notice** and any message that names a consequence.
- **A hold on service or new work**, a hand-off to a collections agency or a lawyer, a small-claims filing.
- **A write-off above the small-leftover limit**, and any credit note that is a concession and not a correction.
- **A payment plan** longer than 90 days, or one that forgives any part of the balance.
- **Charging a late fee or interest**, even where the contract allows it.

Never: refund, reverse or move money to fix an account; threaten what the company will not or may not do.

## Output

1. **Invoices** sent and recorded, each with its source reference.
2. **Payments applied**, with unapplied cash listed by customer and day received.
3. **Reminders** recorded per invoice and stage, with delivery reported.
4. **The weekly receivables page**: `tico page publish reports/receivables-<YYYY-MM-DD>.html --title "Receivables"
   --cadence weekly --as-of <date>`. On it:
   - Four figures: total receivable, overdue, DSO, share over 60 days, each with last week's.
   - A stacked bar of the aging buckets for the last 12 weeks.
   - The chase list: customer, invoices, amount, days late, score, stage sent, next step and date.
   - Promises (kept, due, broken), disputes (owner, age), unapplied cash, write-offs this month.
   - What needs an OK, with the decision asked for.
5. **`knowledge/customers.md`** updated: promises, disputes, contacts, history notes.

## Checks before you finish

- The aging's total ties to Accounts receivable, and the as-of time is on the page.
- No customer is called late on an amount an unapplied payment or credit covers.
- Every late invoice shows either a reminder for its stage or the check that stopped it.
- Each reminder's number, amount and due date match the invoice.
- No reminder names a term the contract and the owner did not give.
- Every promise has a date, and every passed date was acted on.
- Each dispute has an owner and a due date.
- Each write-off has its evidence and is inside the limit or has its OK.
- DSO is shown with its formula, or the reason it was not computed.

## Example (invented, for illustration)

Weekly run, as of 2026-11-16. Aging total 60,310.00; Accounts receivable 60,310.00; tied.

- **Harbour Pilates**, invoice 2057, 1,150.00, 6 days late. Check 1: a bank row of 1,150.00 on 2026-11-13 from "HP
  Studios LLC", their paying entity per `knowledge/customers.md`. Recorded against 2057. Not chased.
- **Birch Cafe**, invoice 2060, 400.00, 8 days late, pays on time (average 2 days early over six invoices). Checks
  pass. Stage 7 reminder recorded; sent under the sending policy; delivered.
- **Lakeside Yoga**, invoice 2052, 850.00, 22 days late. Check 2: disputed on 2026-10-30 (email from their owner: two
  sessions billed that were cancelled). 170.00 is disputed; 680.00 is not. Task filed for the delivery lead, due
  2026-11-23. One note sent asking for the undisputed 680.00 and saying the two sessions are being checked.
- **Northside Movement**, invoices 2031 and 2044, 5,400.00 in total, 61 and 33 days late, usually late. Promise of
  2026-11-10 passed. One note for both invoices drafted as the final notice. The contract (clause 7) allows pausing
  service at 60 days. OK asked of the owner, with the note and the clause shown.
- **Cedar Barre**, invoice 2040, paid 1,988.00 against 2,000.00; their remittance shows a 12.00 wire fee. Inside the
  limit: 12.00 written off, 37.00 used of 250.00 this month.

DSO 42 days (60,310 / 129,000 credit sales over 90 days x 90), down from 45.
