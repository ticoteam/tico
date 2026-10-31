# Finance tools

Seven hub tools reach Finance. Each is an MCP tool (`hub_finance_*`) and a `hub finance` command with the same
inputs. Every call runs as its caller, a person or a bot, with that caller's own Finance rights and view: a bot
without finance.pay cannot pay, and a bot sees only what its view covers. Payroll's private lines stay private for
everyone. The tools call the Finance API under `/api/v2/finance/`; nothing else reads or writes the books.

## The tools

| Tool | Command | Input | Output |
|---|---|---|---|
| `hub_finance_search` | `hub finance search` | `kind` (entry, feed, bill, invoice, contact, rule, card; leave it out to search all), `q`, filters (`status`, `contact_id`, `vendor`, `account_id`, `money_account_id`, `start`, `end`, `aging`, `contact_kind`, `active`, `rule_kind`), `cursor`, `limit` (up to 200) | `{results, next_cursor, view_version}` |
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

`hub finance search --kind card --vendor "Acme Cloud"` answers "which of my cards works at this vendor". `card_unsave`
records that a card was removed from a vendor's account. Both are gated: a bot's `card_save` or `card_unsave` waits for
a finance admin.

## Card fill

`hub_finance_card_fill` is listed so agents can find it. It returns "Card fill is not available yet" and never takes
or returns card details.

See also [Bookkeeper](bookkeeper.md).
