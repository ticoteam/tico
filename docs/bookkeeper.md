# Finance Owner

The Finance Owner is a built-in helper bot that keeps the books: categorizing bank and card rows, entering bills,
scheduling payments, chasing overdue invoices, matching receipts, reconciling, the month's close and a monthly report.
It reports to the owner (a Corporate Manager hired later takes it along) and is listed under Settings → Bots.

## When it appears

Finance is set up on every install. The Finance Owner is built once onboarding is done and a computer is enrolled; an
install from before it gets it at the next start. Pausing or archiving it is respected: it is not rebuilt.

## What it may do

It comes with finance.book, ap, ar, pay, cards and rules, granted once. Revoking one in Finance → Access sticks. An
install from before rules was a default gets that one right at its next start. It may propose a posting rule, and a
person approves each one. It is never a finance admin and never changes views, rights, payment settings or bank
connections.

It sees the books by default: it gets one Finance view of Everything, once per install, so its Books check and Watch
have the whole queue to work. Payroll's private lines stay private: Everything gives it no full detail on a private
line. Revoking that view in Finance → Access sticks; no later start gives it back. An install from before gets the
view at its next start, unless an Everything view for the Finance Owner was revoked before, and the owner's old
"Let the Finance Owner see the books" to-do is closed for them. A paused or archived Finance Owner gets no view.

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
instead of a second task opening. The task is private to the Finance Owner. It opens nothing while the Finance Owner is
paused. From that private task it may only categorize rows and attach receipts (its Finance writes). It may not
ignore a row: ignoring takes the row out of the books (in books mode it reverses the row's entry). A bill approval, a
payment, a card or a close is refused there too, and the task stays private. A write Finance refuses is one plain
sentence on the task; its row stays for the next check, and no person is asked to reissue or publish it.

**Watch** (system routine `bookkeeper_watch`) runs daily and **Rules digest** (`bookkeeper_rules_digest`) weekly, each on its own schedule. Neither is a switch in [System routines](system-routines.md), and the Finance Owner's pause stops both from opening tasks. Watch reads runway under six months of average burn, a month at least twice the median of the three before it (and $100 more), two charges of one amount to one payee within three days, and a recurring charge that rose 10% (and $10 more). An unused subscription is a monthly charge seen three months running (the three calendar months before this one) on a card whose bot is archived or ran no turn in 30 days. Nothing new is a quiet run. A Watch finding is news once: a reviewed one is not listed again after a day, and comes back only when its facts change. The Watch task is private to the Finance Owner. The digest task is private too: its readers are the company owner and the Finance Owner, so teammates who are not the owner do not see the rule names it lists.

## The weekly cash summary and Usage

Every Monday at 08:30 team time (routine `weekly-cash-summary`), and when the owner asks, the Finance Owner writes one page for the owner: cash now and the 13-week cash outlook rolled forward, what is late or due, the Usage line and the decisions needed. It reads the bank balances, the cash-flow report, the overdue and due items and the usage figures, and sends nothing outside the team. It sets no cash minimum, Usage limit or Finance budget; where none is set, the page says so and shows the lowest week without a comparison.

The Usage page estimates what the bots' model runs cost against the limits the owner sets there. A bot's own credentials may see nothing on that page. When they do not, the page says the owner reads it on the Usage page, and the Finance Owner goes on with the Finance budgets.

## The bills address

The bills address is one email address per install that vendors send bills to. The owner or an admin sets it:

```
PUT /api/v2/finance/bills-address   {"address": "bills@acme.example"}    # null clears it
→ {"bills_email": "bills@acme.example", "inbox_id": "ibx_1"}
```

`GET /api/v2/finance/bills` returns it as `bills_email`.

Mail reaches Tico through a Tico address. When the bills address is one of those inboxes (or is exactly one of
an email's recipient addresses; a display name or a look-alike address does not count), each PDF or image
attached to an email that reaches it is filed once in the Finance Owner's Library and handed to Finance as an unmatched
receipt. The next Books check lists it with the sender and subject. The Finance Owner reads the file, finds or adds the
vendor, and enters the bill with `bill_create` (origin `email`, the file attached). Finance raises
`finance.bill.received` and the file leaves the unmatched receipts. A re-delivered email adds nothing.

A file Finance did not take is listed in each Books check until Finance holds it (attached anywhere, or kept as an
unmatched receipt). A file Tico could not fetch is tried again after 1 minute, 10 minutes, 1 hour and 6 hours; after
five failed tries it is listed in one Books check and not again.

When `inbox_id` is null, no inbox receives the address and mail to it never reaches Tico. Uploading a bill still
works either way.
