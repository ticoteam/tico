# Sources

All text in this skill is original. Ideas were drawn from the sources below. Read 2026-11-18 unless dated otherwise.

## Adapted ideas (Apache-2.0)

- Anthropic, `knowledge-work-plugins`, finance plugin: `close-management`, `reconciliation`, `journal-entry-prep`.
  https://github.com/anthropics/knowledge-work-plugins
  Taken: a close calendar in dependency levels with a critical path; a small set of status values; reconciling items
  split into timing, adjustment and investigate; aging bands; no close with an unreconciled difference; accruals
  reverse by default; approval keyed to amount and entry type.
- Anthropic, `knowledge-work-plugins`, small-business plugin, `close-month`.
  Taken: nothing downstream (forecast, report) runs before the close is signed.
- Anthropic, `financial-services`, month-end-closer plugin, `roll-forward`.
  https://github.com/anthropics/financial-services
  Taken: opening plus movements must foot to closing, each line cites its query, and a gap is surfaced, not plugged.

## Guides (ideas only)

- Marc Ursick, "6 tips for reconciliations", Journal of Accountancy (AICPA and CIMA), February 2016.
  https://www.journalofaccountancy.com/issues/2016/feb/reconciliation-best-practices/
  Taken: company-wide limits for what is too small to chase; one method for every account; rank accounts by risk to
  set how often each is reconciled.

## Standards named, not quoted

- FASB ASC 606, Revenue from Contracts with Customers (US GAAP), and IFRS 15. The five questions and the common cases
  in `deferred-revenue.md` restate the model in plain words from general knowledge. No paragraph of the standard was
  read for this file. The company's policy is checked by a CPA.

## Tico

- `docs/finance-tools.md`, `docs/bookkeeper.md` and the Finance Owner's `playbooks/close-month.md` and
  `playbooks/commands.md` on `origin/main`, 2026-11-18: the close checklist (`tico finance report close`),
  `close_period`, the fixed rule that closing or reopening asks the owner, statement uploads, the default chart, the
  default rule that a bot's entry over 5,000.00 asks a finance admin.
- Catalog skills folded in: `controller/reconcile-an-account`, `revenue-accountant/roll-forward-a-deferred-schedule`.
- Catalog skills used as sub-steps: `controller/review-the-books-for-unusual-entries`,
  `revenue-accountant/reconcile-billing-to-the-books`, `finance-owner/reconcile-a-cash-figure`,
  `finance-owner/categorize-a-feed-row`, `finance-owner/enter-a-bill-from-a-file`,
  `billing-specialist/find-unbilled-work`.

## Not found

- No trustworthy public skill or primary-source guide for deferred revenue was found in the research; vendor blogs
  only. `deferred-revenue.md` is therefore marked as a working method that an accountant confirms.
- Unclaimed property rules for uncashed checks vary by US state and were not researched.
