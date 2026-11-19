# Sources

All text in this skill is original. Ideas were drawn from the sources below. Read 2026-11-18 unless dated otherwise.

## Adapted ideas (Apache-2.0)

- Anthropic, `knowledge-work-plugins`, small-business plugin, `ap-processor`.
  https://github.com/anthropics/knowledge-work-plugins
  Taken: look for the duplicate before anything else; code from the vendor's past coding; three-way match with
  clean, price, quantity and no-PO outcomes; a missing PO is not an error where none is required; a bank-detail or
  urgent-payment change goes to phone verification on the number already on file. The source names match tolerances
  without stating them; the 2% or 100.00 default here is this skill's own.
- Anthropic, same plugin, `pay-the-bills`.
  Taken: show the cash position before a payment run is staged; after it is shown the run may shrink but never
  widen. The source keeps a person's yes on coding and on spend for every run; this skill does not (see
  `DISAGREEMENTS.md` in the finance folder).

## Guides (ideas only)

- Association for Financial Professionals, "4 Ways to Reduce Payments Fraud", April 2024.
  https://www.financialprofessionals.org/training-resources/resources/articles/Details/4-ways-to-reduce-payments-fraud
  Taken: a callback goes to a verified contact on a trusted number, never one supplied in the change request; use a
  second channel.
- Corpay, "Procurement Policy: What to Put in It", updated 2026-09-30. Vendor-authored; it sells the tools it
  recommends. https://www.corpay.com/resources/blog/procurement-policy
  Taken: an approval ladder by amount with more quotes at each step, and bands set from last year's real invoices
  and reviewed yearly. Its dollar figures were not used.

## Facts that change (checked 2026-11-18)

- 1099-NEC reporting threshold of 2,000.00 for payments made from 2026-01-01, indexed for inflation from 2027 (the
  2025 federal tax act). Tico's own 1099 workpaper uses the same threshold. Some states have not conformed.
- Nacha fraud-monitoring rules for ACH: phase 1 from 2026-03-20, phase 2 from 2026-06-19 (compliance expected by
  2026-06-22) covering all originators regardless of volume. https://www.nacha.org
- Association for Financial Professionals, 2026 Payments Fraud and Control Survey (January 2026, 465 practitioners):
  about 76% of organizations reported attempted or actual payments fraud in 2025, and about 74% reported business
  email compromise. From secondary coverage; the full report is for members and was not read.
- FBI Internet Crime Complaint Center (ic3.gov) as the US reporting route for payment fraud: general knowledge, not
  re-read for this file.

## Tico

- `docs/finance-tools.md`, `docs/bookkeeper.md` and the Finance Owner's `playbooks/bill-due.md` on `origin/main`,
  2026-11-18: `finance.bill.due_soon`, `finance.vendor.renewal_due`, `bill_create`, `bill_approve`, `bill_schedule`,
  `payment_create`, `recipient_add`, `po_*`, `contact_update` (`seats`, `renewal_on`), `vendor_merge`, `card_save`,
  bot cards tied to budgets, and the default rules (a bot's payment asks a finance admin; payments and POs over
  1,000.00 ask the owner; a new or changed payee asks a finance admin).
- Catalog skills folded in: `accounts-payable-specialist/find-a-duplicate-bill` (number normalizing, the 60-day
  and 12-month windows), `accounts-payable-specialist/match-a-bill-to-its-order`,
  `accounts-payable-specialist/check-a-bank-detail-change`.
- Catalog skills used as sub-steps: `finance-owner/enter-a-bill-from-a-file`,
  `procurement-manager/price-the-whole-term`, `procurement-manager/read-a-vendor-security-answer`,
  `count-a-notice-deadline` (shared), `vendor-manager/price-a-renewal-increase`,
  `fpa-analyst/check-a-cloud-resource-for-waste`.

## This skill's own defaults (not from a source)

- Bands at the 80%, 95% and 99% points of the year's purchases. The idea of setting bands from real spend is
  Corpay's; the three cut points are a working choice that the owner confirms.
- Match tolerance of 2% or 100.00, whichever is smaller.
- The scorecard weights, the "more than double" build test, the 10% renewal cap, the five-business-day escalation.

## Not found, not verified

- No source in the research had a purchase-decision method (buy or build, one-source justification, vendor
  selection) beyond policy checklists. Sections 2 to 5 of `buying.md` are a working method.
- State 1099 thresholds, foreign-vendor withholding and capitalization limits were not researched: they go to the
  tax preparer.
- Bank recall success rates and time limits were not verified; "minutes matter" is the working rule.
