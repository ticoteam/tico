# Finance tools

Seven hub tools reach Finance. Each is an MCP tool (`hub_finance_*`) and a `hub finance` command with the same
inputs. Every call runs as its caller, a person or a bot, with that caller's own Finance rights and view: a bot
without finance.pay cannot pay, and a bot sees only what its view covers. Payroll's private lines stay private for
everyone. The tools call the Finance API under `/api/v2/finance/`; nothing else reads or writes the books.

## The tools

| Tool | Command | Input | Output |
|---|---|---|---|
| `hub_finance_search` | `hub finance search` | `kind` (entry, feed, bill, invoice, contact, rule; leave it out to search all), `q`, filters (`status`, `contact_id`, `account_id`, `money_account_id`, `start`, `end`, `aging`, `contact_kind`, `active`, `rule_kind`), `cursor`, `limit` (up to 200) | `{results, next_cursor, view_version}` |
| `hub_finance_report` | `hub finance report <kind>` | `kind` (pnl, balance_sheet, cash_flow, budget_vs_actual, ap_aging, ar_aging, spend_by, close), `start`, `end`, `period`, `basis`, `compare`, `top_n`, `by`; `month` for close | the report, rows past `top_n` folded into "Other · n" |
| `hub_finance_metric` | `hub finance metric <name>` | `name` (cash, burn, runway_months, revenue, spend, budget_remaining, budget_pct_used, ap_due, ar_overdue, uncategorized_count, unreconciled_days) and its filters | `{value, as_of, ...}` |
| `hub_finance_act` | `hub finance act <kind>` | `kind`, `payload` (JSON), `dry_run`, `operation_id` | `{status, action_id, code, reason, result?, impact?}` |
| `hub_finance_action` | `hub finance action <id>` | `action_id` | the action's current status |
| `hub_finance_inbox` | `hub finance inbox` | `kind`, `cursor`, `limit` | `{items, counts, next_cursor}`: the caller's Finance work queue; with `kind`, that kind alone, paged past its first 200 |
| `hub_finance_card_fill` | `hub finance card-fill` | `card_id`, `merchant`, `amount` | not available yet |

## Acting

`hub finance act help` lists every action kind with the right it needs; `hub finance act help --payload
'{"kind":"bill_create"}'` gives one kind's fields. An action is committed, sent for approval (`needs_approval`),
`denied` or `failed`, with a reason in words.

`operation_id` is the action's key, so a retry never acts twice. Use one made from what you act on, for example
`categorize:<feed id>`. A call with a key Finance has seen returns only that action's id and status, read fresh, never
the first call's result. A denied or failed key stays that way; a new attempt needs a new key. `--dry-run` says what
would happen and changes nothing; it needs no key.

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

## Card fill

`hub_finance_card_fill` is listed so agents can find it. It returns "Card fill is not available yet" and never takes
or returns card details.

See also [Bookkeeper](bookkeeper.md).
