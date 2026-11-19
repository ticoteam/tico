# The reminder stages

One ladder for invoices paid on terms. Tico opens a task at each numbered stage (`finance.invoice.overdue`). Days are
counted from the due date. The days in `knowledge/collections.md` win over these defaults.

| Stage | When | Aim | Tone | Sent by |
|---|---|---|---|---|
| Heads-up | 3 to 5 days before due, for large invoices and customers with a late history | Confirm they have it and nothing is missing | Helpful | The bot, by email |
| 1 | 1 day late | Catch the oversight | Light; assumes it slipped | `invoice_remind`, stage 1 |
| 7 | 7 days late | Find out what is holding it | Plain; asks for a payment date | `invoice_remind`, stage 7 |
| 30 | 30 days late | Get a date in writing; reach a second contact | Firm; names the earlier reminders by date | `invoice_remind`, stage 30, plus a written note to the buyer and the payables contact |
| 60 | 60 days late | Final notice | Formal; says exactly what happens next and when | A written note. **Needs an OK** |
| 90 | 90 days late | Decide | None goes to the customer by default | The owner decides: plan, hold, agency, small claims, write-off |

Between stage 7 and stage 30, a sometimes-late or usually-late customer with a large balance gets one phone call or a
personal note around day 14 to 21, from the account owner or from the bot if it has a phone Tool. The point is a date.

## Rules for every stage

- **Run the five checks first**, every time. A customer who paid yesterday should never hear from you today.
- **One message per customer.** If three invoices are late, one note lists all three with a total. Use the stage of
  the oldest invoice.
- **One ask.** Pay by the link, or tell us the date, or tell us what is wrong. Not all three as separate demands.
- **The crossed-payment line** at every stage: "If this crossed with your payment, please ignore it."
- **Do not skip a stage up** because the amount is big. Do move up when a promised date is missed: a broken promise
  goes straight to the next stage and says the date passed.
- **Do not restart the ladder** after a part payment. The remaining balance keeps the invoice's original age.
- **A reply always gets an answer** before the next reminder. Silence from the company after a customer's question is
  the fastest way to turn a late payer into a non-payer.
- **Send in the customer's working hours**, Tuesday to Thursday morning where you can. Not on their holidays.

## How `invoice_remind` works in Tico

- One reminder per invoice and stage; a second is refused. Stages are 1, 7, 30, 60, 90.
- Tico writes fixed text and puts the email in its send queue. It is not edited. Stages 1, 7 and 30 need no Finance
  OK: they send as the company's sending policy allows, as a money email. Stages 60 and 90, and any stage once the
  invoice is 60 days late, wait for a finance admin's OK on the preview. A Finance rule can make any stage ask.
- It is refused for a paid or draft invoice, a customer with no email on file, or a bot with no mailbox that can send.
  The refusal gives the reason; fix the cause (add the contact), do not work around it.
- Report what happened to each email with `invoice_delivery`.
- At stage 60, do not use the fixed reminder as the final notice. Write the notice
  (`reminder-writing.md`) and get its OK.

## The final notice

It goes once, at about 60 days, only after the OK. It says:
- every open invoice, its date, amount and days late, and the total;
- the reminders already sent, by date;
- one deadline, as a date at least 7 days out;
- exactly what the company will do on that date, which must be something the contract allows and the owner has
  decided to do: pause the service under clause N, stop new work, charge the late fee in clause N, refer the account
  for collection;
- how to pay today, and who to call to agree a plan instead.

It says nothing the company will not do. An empty threat teaches the customer the deadlines are not real.

## After 90 days: the options, for the owner

Show these on the task with your recommendation and the numbers.

| Option | Good when | Cost |
|---|---|---|
| Payment plan | The customer is in touch and short of cash | Slower cash; needs follow-up on each date |
| Hold service or new work | The contract allows it and the customer still uses the product | May end the relationship |
| Settle for less | A real dispute sits under the delay | The discount; write off the rest |
| Collections agency | Larger balance, customer silent, relationship already lost | Commonly a share of what is recovered; agree the rate in writing first |
| Small claims or a lawyer's letter | Clear contract, clear delivery, customer can pay | Filing fees and time; limits vary by state |
| Write off | The cost to collect is more than the balance, or the customer is gone | The balance |

## Failed card payments

Subscriptions charged to a saved card do not go "late"; the charge fails. This is its own track.

1. **Before it fails.** Tell customers whose card expires next month. Turn on the processor's card updater if it has
   one.
2. **Use the processor's retry schedule**, not your own. Card networks limit how often a declined charge may be
   retried, and processors time retries for when they are likeliest to succeed.
3. **Tell the customer on the first failure**, with a link to update the card. Say when the next try is. Two or three
   notes over the retry window, each shorter.
4. **Give a grace period** before any downgrade or pause, and state its last day in every note. The length is the
   owner's decision in `knowledge/collections.md`; default 14 days.
5. **A hard decline** (card closed, reported stolen) is not retried. Ask for a new card at once.
6. **When the grace period ends**, do what the terms of service say, no more. Keep the customer's data for the period
   the terms promise so a late payment restores them.

Count recovered and lost failed payments each month on the receivables page: for many subscription businesses this is
a large part of churn.

## Law (US default; check locally)

This is orientation, not legal advice. Read 2026-11-18.

- **Business customers.** The federal Fair Debt Collection Practices Act covers third-party collectors of consumer
  debt. A company collecting its own invoices from another business is generally outside it. Contract terms and
  ordinary rules against deception and harassment still apply.
- **Consumers.** If customers are individuals buying for personal use, state laws often cover the original creditor
  too. Keep to the truth, reasonable hours, no contact with third parties about the debt, and stop contact through a
  channel the person asks you to stop.
- **California.** The Rosenthal Act covers original creditors collecting consumer debt. Since 2025-07-01 (SB 1286) its
  core conduct rules also reach "covered commercial debt": commercial debt of 500,000 or less owed or guaranteed by a
  natural person, entered into, renewed, sold or assigned on or after that date. A sole proprietor or a
  personally-guaranteed small business in California may be covered.
- **Calls and texts.** Automated or prerecorded calls and texts to a mobile phone need the person's consent under the
  federal TCPA. Email reminders are the default for that reason.
- **Late fees and interest.** Only if the signed contract says so, and only up to what the customer's state allows.
  Limits differ by state and by whether the customer is a consumer.
- **Agencies.** A third-party agency collecting consumer debt is covered by the federal Act and by state licensing
  rules. Hand over only accurate, documented balances.
- **Bankruptcy.** When a customer files, stop all collection contact at once and tell the owner. A lawyer handles the
  claim.
- **Time limits.** Each state sets how long a debt can be sued on. Do not threaten suit on an old debt without a
  lawyer's view.
- **Outside the US**, rules differ again (for example statutory late-payment interest in the UK and EU). Ask counsel.
