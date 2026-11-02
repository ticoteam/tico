# Bookkeeper

The Bookkeeper is a built-in helper bot that keeps the books: categorizing bank and card rows, entering bills,
scheduling payments, chasing overdue invoices, matching receipts, reconciling, the month's close and a monthly report.
Like the Task Manager it sits outside the org chart and is listed under Settings → Bots.

## When it appears

Finance is set up on every install. The Bookkeeper is built once onboarding is done and a computer is enrolled; an
install from before it gets it at the next start. Pausing or archiving it is respected: it is not rebuilt.

## What it may do

It comes with finance.book, ap, ar, pay, cards and rules, granted once. Revoking one in Finance → Access sticks. An
install from before rules was a default gets that one right at its next start. It may propose a posting rule, and a
person approves each one. It is never a finance admin and never changes views, rights, payment settings or bank
connections.

It sees only what its Finance view covers. The owner gets one task, "Let the Bookkeeper see the books", to give it
Everything in Finance → Access. Payroll's private lines stay private either way.

It touches the books only through the [Finance tools](finance-tools.md), as itself. Approvals follow the company's
rules; it never approves its own action. The owner approves a month's close and each import of a spreadsheet's history.

## How work reaches it

| What | How | Task |
|---|---|---|
| Rows to categorize, draft bills, receipts and emailed bills to match, missing receipts, failed syncs, budgets near their limit, books not reconciled for over 35 days | **Books check**, hourly: plain code reads its Finance work queue | One "Books check (n)" task, only when something is new |
| A bill due in 7 days, 1 day or today | `finance.bill.due_soon` routine | One per bill and stage |
| An invoice 1, 7, 30, 60 or 90 days overdue | `finance.invoice.overdue` routine | One per invoice and stage |
| A month ready to close | `finance.period.ready` routine | One per month |
| Monthly report into Docs | Routine on the 3rd at 09:00 | One a month |
| Runway under six months, a month's spend well above its usual, a duplicate charge, a recurring charge that rose, an unused subscription | **Watch**, daily: plain code reads the books | One private "Watch (n)" task, only for something new |
| Posting rules made in the last seven days | **Rules digest**, weekly: plain code | One "Rules digest (n)" task, only when there are rules |

**Books check** (system routine `bookkeeper_check`) runs hourly by default; change it under
[System routines](system-routines.md). With nothing new it is a quiet run: no task and no model call. An item listed
in the last day is not news unless its facts changed. A still-open Books check task takes the next check's list
instead of a second task opening. The task is private to the Bookkeeper. It opens nothing while the Bookkeeper is
paused.

**Watch** (system routine `bookkeeper_watch`) runs daily and **Rules digest** (`bookkeeper_rules_digest`) weekly, each on its own schedule. Neither is a switch in [System routines](system-routines.md), and the Bookkeeper's pause stops both from opening tasks. Watch reads runway under six months of average burn, a month at least twice the median of the three before it (and $100 more), two charges of one amount to one payee within three days, and a recurring charge that rose 10% (and $10 more). An unused subscription is a monthly charge seen three months running (the three calendar months before this one) on a card whose bot is archived or ran no turn in 30 days. Nothing new is a quiet run. A Watch finding is news once: a reviewed one is not listed again after a day, and comes back only when its facts change. The Watch task is private to the Bookkeeper. The digest task is private too: its readers are the company owner and the Bookkeeper, so teammates who are not the owner do not see the rule names it lists.

## The bills address

The bills address is one email address per install that vendors send bills to. The owner or an admin sets it:

```
PUT /api/v2/finance/bills-address   {"address": "bills@acme.example"}    # null clears it
→ {"bills_email": "bills@acme.example", "inbox_id": "ibx_1"}
```

`GET /api/v2/finance/bills` returns it as `bills_email`.

Mail reaches Tico through an Email & Phone inbox. When the bills address is one of those inboxes (or is exactly one of
an email's recipient addresses; a display name or a look-alike address does not count), each PDF or image
attached to an email that reaches it is filed once in the Bookkeeper's Library and handed to Finance as an unmatched
receipt. The next Books check lists it with the sender and subject. The Bookkeeper reads the file, finds or adds the
vendor, and enters the bill with `bill_create` (origin `email`, the file attached). Finance raises
`finance.bill.received` and the file leaves the unmatched receipts. A re-delivered email adds nothing.

A file Finance did not take is listed in each Books check until Finance holds it (attached anywhere, or kept as an
unmatched receipt). A file Tico could not fetch is tried again after 1 minute, 10 minutes, 1 hour and 6 hours; after
five failed tries it is listed in one Books check and not again.

When `inbox_id` is null, no inbox receives the address and mail to it never reaches Tico. Uploading a bill still
works either way.
