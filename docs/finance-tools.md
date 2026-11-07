# Finance tools

Six hub tools reach Finance. Each is an MCP tool (`hub_finance_*`) and a `hub finance` command with the same
inputs. Every call runs as its caller, a person or a bot, with that caller's own Finance rights and view: a bot
without finance.pay cannot pay, and a bot sees only what its view covers. Payroll's private lines stay private for
everyone. The tools call the Finance API under `/api/v2/finance/`; nothing else reads or writes the books.

## The tools

| Tool | Command | Input | Output |
|---|---|---|---|
| `hub_finance_search` | `hub finance search` | `kind` (entry, feed, bill, invoice, contact, rule, card, account; leave it out to search all but account), `q`, filters (`status`, `contact_id`, `vendor`, `account_id`, `money_account_id`, `start`, `end`, `aging`, `contact_kind`, `active`, `rule_kind`), `cursor`, `limit` (up to 200) | `{results, next_cursor, view_version}` |
| `hub_finance_report` | `hub finance report <kind>` | `kind` (pnl, pnl_by, balance_sheet, cash_flow, budget_vs_actual, ap_aging, ar_aging, spend_by, close, sales_tax, 1099), `start`, `end`, `period`, `basis`, `compare`, `top_n`, `by` (`segment` for pnl_by); `month` for close; `year` for sales_tax and 1099 | the report, rows past `top_n` folded into "Other · n"; sales_tax and 1099 are whole or an error, and only for someone who sees everything |
| `hub_finance_metric` | `hub finance metric <name>` | `name` (cash, burn, runway_months, revenue, spend, budget_remaining, budget_pct_used, ap_due, ar_overdue, uncategorized_count, unreconciled_days) and its filters | `{value, as_of, ...}`; the budget metrics add `pace_month_end`, `spent_from` (books, platform or mixed: an ad budget reads its platform's daily spend in each month a healthy import covers, in place of that platform's charges), `platform` and `platform_as_of` |
| `hub_finance_act` | `hub finance act <kind>` | `kind`, `payload` (JSON), `dry_run`, `operation_id` | `{status, action_id, code, reason, result?, impact?}` |
| `hub_finance_action` | `hub finance action <id>` | `action_id` | the action's current status |
| `hub_finance_inbox` | `hub finance inbox` | `kind`, `cursor`, `limit` | `{items, counts, next_cursor}`: the caller's Finance work queue; with `kind`, that kind alone, paged past its first 200 |
| `hub_finance_segment_review` | `hub finance segment-review` | `cursor`, `limit` | `{contacts, next_cursor}`: the customers no person has set a segment for |
| `hub_finance_contact_segment` | `hub finance contact-segment <contact-id> <segment-id>` | `contact_id`, `segment_id` (empty clears it), `operation_id` | the action's result, as `hub_finance_act` returns it |

The Finance transactions list (its feed read) leaves out an `ignored` row that the bank has ended: its `provider_status`
is then Mercury's own word, `cancelled`, `failed`, `blocked` or `reversed` (the list shows `cancelled` as "canceled" and
`reversed` as "returned"). Setting `status` to `failed` lists those alone. A row the bank voids or reverses after a finished
reconciliation cleared it keeps its booked status: it stays in the list, does not appear under `failed`, and its
`provider_status` holds the bank's word. A row a person ignored stays in the list as `ignored` until the bank ends it; then
it leaves the list like any other row the bank ended.

## Acting

`hub finance act help` lists every action kind with the right it needs; `hub finance act help --payload
'{"kind":"bill_create"}'` gives one kind's fields. An action is committed, sent for approval (`needs_approval`),
`denied` or `failed`, with a reason in words.

`operation_id` is the action's key, so a retry never acts twice. Use one made from what you act on, for example
`categorize:<feed id>`. A call with a key Finance has seen returns only that action's id and status, read fresh, never
the first call's result. A denied or failed key stays that way; a new attempt needs a new key. `--dry-run` says what
would happen and changes nothing; it needs no key.

A vendor takes `seats` (a whole number) and `renewal_on` (`YYYY-MM-DD`; empty clears it) on `contact_update`. Its renewal date raises `finance.vendor.renewal_due` 30 and 7 days before it, once per date and stage. Customers refuse both.

An internal transfer is `payment_create` with `method: "internal"`, `from_account` and `to_account` (the company's own bank accounts on one Mercury connection) and `amount` (cents), with an optional `memo`. The requester must see both accounts; otherwise the action is denied and the refusal names neither account. It takes the payment gates: over $1,000 asks the owner (§6.4 rule 2), and a company rule can ask for more. Rules on `accounts` match a transfer by its two ledger accounts. The new-recipient rule never applies, since an own account is never a new recipient. There is no budget hold: a move between our accounts is not an expense. Right before Mercury is called, the send re-checks the requester's payment right and view, the payments switch for the connection, and the gates (with their counters); a rule that now denies cancels the transfer, and one that now asks holds it until approved. Until its call is claimed, a queued transfer can be canceled with `payment_cancel` and `{"transfer_id": ...}`; once it is claimed or sent, cancel is refused and the transfer is checked in Mercury. Mercury gets it once, under its own key (POST /transfer). An unknown result is never sent again: it is looked for by its key (in the note) in Mercury's transactions, and that transaction's created time is its submit day until then the claim time stands. A refused result is not sent again either, and a finance admin is asked to check Mercury. Both legs post as one transfer with no P&L effect. A leg settles the transfer only when it is dated from the transfer's submit day through five days on, and a Mercury id the leg carries for the transfer is preferred over an older match. The transfer shows `posted` once both legs are in the feed.

```
hub finance act categorize --payload '{"feed_id":"fd_1","account_id":"acc_6100"}' --operation-id categorize:fd_1
hub finance action fa_123
```

## Sizes

A result is kept to about 4,000 tokens. `limit` (search, inbox) or `top_n` (reports) raises that to about 16,000. A
longer list keeps its first rows and ends with one row labelled "Other · n" that counts, and for reports adds up, the
rest; a paged list's `next_cursor` carries on from the last row shown. Period columns are never folded. A row too
big on its own keeps its ids and amounts and has its long text and nested lists cut; a result that still does not fit
comes back as `{"error": "too_big", "chars", "cap"}`.

## Segments

A company's segments are its market segments (`segment` entities the company operates in, backend/segments.py). Finance
puts money by segment in three places: budgets, customers and lines. The Finance API is `/api/v2/finance/`; the hub
tools and the `hub finance` commands take the same inputs.

- **Budgets** take `segment_id` on `budget_create` and `budget_update`: one of the company's segment ids, or empty for
  none. A child with no segment of its own reports under its nearest parent's. Each budget row says its own
  `segment_id` and its `effective_segment_id` (the segment its spend reports under; null for Unallocated). The catch-all
  budget takes no segment: shared costs stay Unallocated. `GET /api/v2/finance/budgets?segment=<id>` keeps the budgets
  whose spend reports under that segment (`segment=unallocated` for none).
- **Customers** take a segment with `contact_segment` (`contact_id`, `segment_id`; empty clears it), or with
  `POST /api/v2/finance/contacts/{contact_id}/segment` and the body `{"segment_id": "<id or empty>"}`, which runs the same
  action (its `Idempotency-Key` header works as for `POST /actions`). Only customers take one. A person's choice is final
  (`segment_sure` true). A bot's choice is a guess (`segment_sure` false), and that is decided by the real caller: a bot
  acting for a person still writes a guess. A guess can replace another guess, never a person's choice.
  `GET /api/v2/finance/contacts/segment-review` lists the customers no person has set (`segment_sure` 0), by name, with
  `cursor` and `limit`. A person's explicit choice, even none, leaves the list.
- **Lines** take a `segment` tag through the existing line-tag path (`categorize`, and the banking and document tags).
  The tag must be one of the company's segments.

A line counts toward the segment named by its explicit tag; else the segment of the budget its spend counts against (the
deepest budget that matches it, its parent's segment when it has none); else, for revenue, the segment of its customer;
else Unallocated. Shared costs (payroll, engineering, AI seats) have no budget and stay Unallocated. There are no split
rules in v1.

A segment the company no longer operates in counts as Unallocated wherever it was set. A filter on such an id is not an
error: it matches nothing, so the result is empty.

The segment is read when a P&L is read, not stored on the rollups: a budget's or a customer's segment can change, and
a stored one would need every past rollup rewritten. Each read finds the company's segments once, the book's customers
that have a segment once, and skips the budget step when no budget names a segment. Changing a budget's segment, or a
customer's, bumps the view version (`view_version`), so anything cached by it is read again.

**P&L by segment** is `GET /api/v2/finance/reports/pnl_by?by=segment` (with `start`, `end`, `basis`). Its `columns` are
the company's segments in rank, then name order (an unranked segment last), each with `key`, `label`, `color`, `who` and
`rank` (null when unranked), then `unallocated` and `total`. Its `rows` are `income`, `expense` and `contribution`
(revenue minus that column's own costs); each row's `values` is keyed by column key. Total equals the P&L's income,
expense and net for the same window. Budgets and projections are not split by segment in this read.
`GET /api/v2/finance/pnl?segment=<id|unallocated>` keeps the lines that count toward one segment; its budgets are cut to
it, and its projections are left out. A viewer without the whole P&L may name only a segment they lead; any other segment, Unallocated included, is refused. A segment filter also narrows the lines enough that private payroll shows none of
its cells, so a filtered P&L never shows what the rest of a pay run was paid. The expenses view (`/finance/expenses`)
does not group by segment.

In the app, **Finance › P&L** has a **By segment** view (shown only when the segment read is open to you, probed once a
visit): a column per segment, then Unallocated and Total, with revenue, costs and contribution. A segment's column, or the
Segment filter on the company P&L, opens that segment's P&L; a line on it opens its page filtered the same way. A pay run
left out of the view is noted. **Finance › Budgets** has a Segment filter, a Segment select when adding a budget, and a
Segment row in the rail that edits the budget's segment. A segment's P&L is read-only (no budget is edited from it). The
customer review list (`segment-review`) has no screen in the app yet.

Who sees what:

- The owner, and anyone with the whole P&L (a company-wide `pnl` grant), sees every column, Unallocated and Total.
- A segment's lead sees that segment's column only, with no Unallocated and no Total, and `pnl?segment=` names only that segment. A lead is a teammate tagged with
  the segment (not with All) whose manager is not tagged with it too. The view default `segment_lead` (`lead`, or
  `none` to turn the lead view off for everyone) decides whether leads see their column.
- A dedicated teammate who leads nothing gets no segment P&L, but reads the segment's budgets, read-only.
- Anyone else is refused the segment P&L and sees none of the segment's budgets.
- A bot follows the same rules by its ref (`bot:<id>`).

Who is tagged with a segment comes from `segment_members`, which the teammates track owns; Finance reads it through
`backend/segments.py` (`leads`, `member_segments`). Until those tags exist, nobody is tagged, so only the owner and full
P&L viewers see segment columns.

**Hub tools and commands.** `hub_finance_report` takes `kind=pnl_by` with `by=segment`. `hub_finance_segment_review`
(`cursor`, `limit`) reads the review list, and `hub_finance_contact_segment` (`contact_id`, `segment_id`, `operation_id`)
sets a customer's segment. The CLI has the same: `hub finance report pnl_by --by segment`,
`hub finance segment-review [--cursor C] [--limit N]`, and `hub finance contact-segment <contact-id> <segment-id|"">`.

## Tax

Sales tax and 1099 workpapers (`/api/v2/finance/tax/`) are filed as a whole, so only someone who sees everything (the owner,
or an Everything grant) can read them, CSV included. Anyone else is refused rather than shown partial totals. So is an
Everything viewer when a line behind the workpaper sits on a private account they lack full detail for: a sales tax
remittance line, or a 1099 vendor's expense or bill line. Payroll that feeds neither workpaper does not block them. The
Bookkeeper reads the workpapers once it is granted Everything, which the owner is asked to do at setup. A sales tax
jurisdiction is added with `tax_jurisdiction_add` (`code`, `name`, a vendor contact as
`authority_id`, `due_day` 1 to 28), and a quarter's tax is remitted with `tax_remit` (`jurisdiction`, `period` such as
`2026-Q3`, optional `amount`): a bill to that authority, tagged with the quarter, counted as remitted by what is paid on it (partial payments count). Without `amount` the owed figure is read off the workpaper, so that needs full view.

- **Sales tax** (`sales-tax`, columns `period, jurisdiction, taxable_sales, exempt_sales, tax_collected, tax_remitted,
  due`): by calendar quarter from invoices dated in it. Each invoice line has a `taxable` flag (`lines[].taxable` on
  `invoice_create`), true by default for income lines on an invoice that carries a sales tax line and false otherwise.
  Taxable sales are the taxable income lines on invoices carrying that jurisdiction's tax line; exempt sales are the
  non-taxable income lines on those invoices. Due is collected less remitted, where remitted is what is paid on the
  quarter's remittance bills, matched to the quarter they are tagged for. `due_on` is the return's due day in the month
  after the quarter.
- **1099** (`1099`, columns `year, vendor, tin_on_file, cash_paid, card_paid_excluded, threshold, reportable`): per
  flagged vendor per calendar year, counting each payment by the day it was paid: the bank or card row's own date, even
  when the row was posted or matched to a bill after its month closed (the entry itself posts on the first open day). A
  reversal counts with the row it reverses. Cash paid counts bills settled from a bank or the bank's cash line and
  expenses a bank row posted to the vendor. Card money is left out and reported as `card_paid_excluded`, and a card
  refund lowers it: a card program's charge, a credit card account, a card-source entry, any feed row with a card id, a
  debit or credit card charge, refund or fee, and a card expense later matched to a bill. Gateway credit is neither.
  Vendor lines are matched through the contact and its name aliases, so a payment posted under a name before the contact
  existed counts. The threshold is $2,000 for 2026 and later, $600 before. Only whether a TIN is on file shows; the TIN
  stays in the vault.

Due dates are seeded once, when Finance is set up and the Bookkeeper exists, and again when a jurisdiction is added:
the 1099-NEC (January 31) and each jurisdiction's quarterly return. Each is an external rule owned by the Bookkeeper,
with a routine that opens its task 30 days (1099) or 14 days (sales tax) before it.

## Testing Finance without Mercury

`TICO_MERCURY_BASE` points the bank worker at another Mercury API base, such as the sandbox
(`https://api-sandbox.mercury.com/api/v1`) or a local fake. Unset, the worker calls Mercury's own API. For a local run
with no token, `scripts/finance_e2e.sh` starts `scripts/fake_mercury.py` and the demo that reads it.

## Bot cards

A bot's card is a standing card linked to its budget. Mercury limits it to its allocation, so the card can't spend
past the budget. Mercury has no single-use cards and no merchant locks, so a bot buys through a card a person has
saved on the vendor's account (`card_save`, once per vendor): a card is seen by whoever sees its holder's spend on its
budget line, so a bot sees its own cards and a manager sees its reports' cards. Each card row lists the vendor
accounts it is `saved_on` (vendor, label, who saved it, when): the first ten, oldest first; the vendor filter finds
all.

A card's cutoff freeze (its budget reached its decline or hold-payment amount) is recorded as `frozen_by: budget` on the
card row, with the budget it came from. It lifts by itself once every budget over the card has room again: a new period,
a raise that leaves room, or a hold or reserve released (checked at each hourly look and at each raise). Only decline and
hold-payment budgets at their cutoff keep a card frozen; a warn or ask budget at zero does not, nor an ad budget (its
vendor is Google Ads or Meta), which only warns. A manual unfreeze is
refused while any decline or hold-payment budget over the card is at its cutoff ("the Tools budget is used up; raise it
before unfreezing"). A freeze a person made (`frozen_by: person`), or one a person claims by freezing the card, stays
until a person unfreezes it. A pause (the budget set to 0, which takes effect now) freezes every active card on the
budget and under it, also as `frozen_by: budget`, and keeps their allocations; a raise that leaves room lifts them the
same way. A budget can't be set to a non-zero amount below what its cards reserve (each card's allocation, or what it
has used if more) plus its child budgets on any day from the change onward, including days a child's later version
raises it; the refusal names the cards to lower first. A child's amount, and a child coming back
active, is held to the same rule on its parent.

Migration 67 backfills `frozen_by: budget` on cards the action log shows a cutoff froze before the column existed (only
cards that still have an allocation; a card whose allocation is zero, or one a person froze, stays unset). An install
that already recorded migration 67 does not re-run the backfill.

`hub finance search --kind card --vendor "Acme Cloud"` answers "which of my cards works at this vendor". `card_unsave`
records that a card was removed from a vendor's account. A save is found under the vendor's contact id, its aliases and
its legacy names, so a save made before the vendor became a contact still unsaves; `card_save` refuses a second save
under any of those keys. Both are gated: a bot's `card_save` or `card_unsave` waits for a finance admin.

## Card fill

Card fill is not offered: `hub_finance_card_fill` is in no tool list (`hubtools.UNLISTED`) and has no `hub finance`
command. A call by its name still runs and returns "Card fill is not available yet"; it never takes or returns card
details.

See also [Bookkeeper](bookkeeper.md).
