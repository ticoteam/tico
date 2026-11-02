# Commands for the Bookkeeper

Use these commands. For anything else, run `hub <group> --help` first.

Money is integer minor units in every payload and report: 45000 is 450.00. Dates are YYYY-MM-DD. Take each id from the
work item, a search or a read, never from memory.

Writes are `hub finance act <kind> --payload '<json>' --operation-id <key>`. The key is built from what the action
touches, so a retry lands once. `--dry-run` writes nothing and returns `"dry_run": true` with the outcome and any
approver. Use it when unsure, then send the same command without it. A kind's fields:
`hub finance act help --payload '{"kind":"<kind>"}'`. A field the kind does not take is refused.

## Work queue and search
- `hub finance inbox` — your work queue, with counts per kind. `--kind <kind> --cursor <cursor>` pages one kind: uncategorized, bill_to_enter, unmatched_receipt, missing_receipt, bill_due, invoice_overdue, budget_near_limit, sync_failed or period_ready. `bill_email` and `unreconciled` are not kinds: those items are in the Books check task, with the file id in the label (`hub task show <task id>`).
- `hub finance search --kind feed --q "<words>"` — bank and card rows. `lines` shows the account a row is posted to.
- `hub finance search --kind rule --q "<counterparty>"` — posting rules that match it.
- `hub finance search --kind bill --q "<vendor or bill number>"` — bills: id, number, vendor, status, total, remaining.
- `hub finance search --kind contact --q "<name>"` — vendors and customers: id, kind, default account.
- `hub finance search --kind entry --q "<words>"` — posted entries.
- `hub finance search --kind invoice --q "<number or customer>"` — invoices.

## Files and documents
- `hub file read <file id>` — one file as text, in full. Empty or garbled text is a scan: ask a person, and key no figures from a guess.
- `hub api GET finance/bills/<bill id>` — a bill: lines (account, amount), attachments, status, due date, remaining.
- `hub api GET finance/invoices/<invoice id>` — an invoice: total, paid, remaining, due date, days late, payment link.
- `hub api GET finance/pay-from` — the bank accounts payments can come from, with balances.

## Posting, attaching, rules
- `hub finance act categorize --payload '{"feed_id":"<feed id>","account_id":"<account id>"}' --operation-id categorize:<feed id>` — posts a bank or card row to a line.
- `hub finance act attach --payload '{"subject_kind":"<bill|feed|entry|invoice>","subject_id":"<id>","file_ids":["<file id>"]}' --operation-id attach:<file id>` — adds a file to an existing document. For a receipt with no match, `subject_kind` is `unmatched` and there is no `subject_id`.
- `hub finance act rule_create --payload '<JSON with the fields from help>' --operation-id rule:<counterparty>` — proposes a posting rule; the owner approves it.
- `hub finance act contact_create --payload '<JSON with the fields from help>' --operation-id contact:<name>` — a new vendor, only when no search finds one.
- `hub finance act reconcile --payload '<JSON with the fields from help>' --operation-id reconcile:<money account id>:<statement end>` — matches a statement to the feed.

## Bills
- `hub finance act bill_create --payload '{"vendor_id":"<vendor id>","number":"<bill number>","bill_date":"<YYYY-MM-DD>","due_date":"<YYYY-MM-DD>","origin":"email","attachments":["<file id>"],"lines":[{"description":"<text>","amount":45000,"account_id":"<account id>"}]}' --operation-id bill-entry:<file id>` — a new draft. Use `terms_days` instead of `due_date` when the file gives terms. `origin` is `email`, `upload` or `manual`. The draft's `bill_id` comes back in `result`.
- `hub finance act bill_update --payload '{"bill_id":"<bill id>","number":"<bill number>"}' --operation-id bill-update:<bill id>` — a draft only. Send only the fields that are wrong. `lines` replaces every line.
- `hub finance act bill_approve --payload '{"bill_id":"<bill id>"}' --operation-id bill-approve:<bill id>` — enters a draft, by its own id.
- `hub finance act bill_schedule --payload '{"bill_id":"<bill id>","pay_on":"<YYYY-MM-DD>","money_account_id":"<money account id>"}' --operation-id bill-due:<bill id>:<stage>` — requests the payment on `pay_on`. The team's Finance rules decide what needs approval; the action's `status` says.
- `hub finance act payment_create` — the other way in bill-due.md. Fields: `help`.

## Invoices
- `hub finance act invoice_record_payment --payload '{"invoice_id":"<invoice id>","amount":90000,"feed_id":"<feed id>"}' --operation-id invoice-payment:<invoice id>:<feed id>` — records money received on an invoice. `amount` may be part of what is owed.

## Month close and reports
- `hub finance report close --month <YYYY-MM>` — the close checklist: each check with `ok` and `count`, then `ready` and `as_of`.
- `hub finance report budget_vs_actual --start <first day> --end <last day>` — per budget: `budget` and `actual` by month, `excluded`, `snapshot` (null for an open month; for a closed one its `month` and `restatements` count). The as-of time is when you read it.
- `hub finance report spend_by --start <first day> --end <last day> --by vendor` — the biggest vendors: `label`, `amount`, `total`.
- `hub finance act close_period --payload '{"month":"<YYYY-MM>"}' --operation-id close:<YYYY-MM>` — asks to close the month. `month` is its only field; the owner approves.
- `hub finance report pnl --start <first day> --end <last day> --compare prior` — the P&L with the change from the month before.
- `hub finance report balance_sheet --end <last day>` — balances as of the month's end.
- `hub finance report cash_flow --start <first day> --end <last day>` — cash in and out.
- `hub finance metric runway_months` — one number, with its as-of time.

## Actions and tasks
- `hub finance action <action id>` — an action's status: committed, needs_approval, denied or failed.
- `hub task show <task id>` — the task, its body and its comments.
- `hub task comment <task id> "<text>"` — a comment on the record. Notes go here, including the close's variance note.
- `hub task question "<question>" --on <person> --why "<why it matters>" --option "<label> -> <what happens>" --option "<label> -> <what happens>"` — one decision for a person; the task waits on them. `--on` is the approver a dry run names, or the person the task names. Two to six options. For yes or no, leave out `--option`.
- `hub task done <task id> --note "<one line>"` — marks the task done with its one line.
- `hub doc write finance/reports/<YYYY-MM> --title "<title>" --body-file <file>` — the monthly report, in Docs.

## Common mistakes
- The top level has no `help` command: use `hub --help`. Tasks have no `get`: read one with `hub task show`.
- `close_period` refuses a `note` field. The variance note is a task comment.
- `bill_create` refuses `date` and `currency`. The fields are `bill_date` and the ones `help` lists.
- `hub task show` takes ids only: it has no `--json`.
- Quote only the `--q` value: `hub finance search --kind bill --q "Summit Leasing"`. A flag list in one quoted string is one argument, and the hub refuses it.
- An action's status is `hub finance action <action id>`. `finance act action` does not exist.
- `hub finance inbox --kind` takes only the kinds listed above. `bill_email` is not one.
