# Sources

All text in this skill is original. Ideas were drawn from the sources below. Read 2026-11-18 unless dated otherwise.

## Adapted ideas (Apache-2.0 and MIT)

- Anthropic, `knowledge-work-plugins`, small-business plugin, `invoice-chase` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins
  Taken: check the payment processor and the bank before chasing; one message per customer covering all open
  invoices; reminders are drafted for a person by default (this skill differs: see `DISAGREEMENTS.md` in the finance
  folder).
- `pm-claude-skills`, `collections-email` (MIT).
  Taken: a staged cadence with a tone per stage; never threaten what is not lawful or not intended.

## Ideas only

- `openaccountant`, `invoice-aging`: each aging bucket maps to an action. Description only; no text used.
- Dun & Bradstreet, accounts receivable guidance: rank the chase list by likelihood to pay, not by size alone; read
  DSO together with receivables turnover.

## Law (orientation only, not legal advice)

- Fair Debt Collection Practices Act, 15 U.S.C. 1692 and following: covers third-party collectors of consumer debt.
  Stated from general knowledge; the statute was not read for this file.
- Telephone Consumer Protection Act: consent for automated calls and texts to mobile phones. General knowledge.
- California SB 1286, extending the Rosenthal Fair Debt Collection Practices Act to covered commercial debt of
  500,000 or less owed or guaranteed by a natural person, for debt entered into, renewed, sold or assigned on or
  after 2025-07-01. From law-firm summaries read 2026-11-18; the bill text was not read. Check before relying on it.
- FASB ASC 326, expected credit losses: named, not quoted. The allowance method in `write-offs.md` is a common
  practical approach and is confirmed by the company's accountant.

## Tico

- `docs/finance-tools.md`, `docs/bookkeeper.md` and the Finance Owner's `playbooks/invoice-overdue.md` on
  `origin/main`, 2026-11-18: `finance.invoice.overdue` at 1, 7, 30, 60 and 90 days; `invoice_remind` (one per invoice
  and stage, fixed text, queued email); `invoice_delivery`; `invoice_record_payment`; `invoice_write_off`;
  `ar_aging`; the `ar_overdue` metric.
- Catalog skills folded in: `accounts-receivable-specialist/check-before-you-chase`,
  `accounts-receivable-specialist/read-an-aging-report`, `accounts-receivable-specialist/write-a-payment-reminder`.
- Catalog skills used as sub-steps: `billing-specialist/check-an-invoice-before-issue`,
  `billing-specialist/find-unbilled-work`.

## Not found, not verified

- No source measured which reminder cadence recovers the most cash. The 1, 7, 30, 60, 90 stages are Tico's own and a
  common convention, not a tested optimum.
- Card-network limits on retrying a declined charge were not verified; the skill defers to the processor's schedule.
- State statutes of limitation, late-fee and interest caps, and collection-agency licensing were not researched.
- Unclaimed-property rules for old customer credits vary by state and were not researched.
- The 180 days and four attempts in the "no response" write-off test are this skill's default, not a rule from a
  source.
