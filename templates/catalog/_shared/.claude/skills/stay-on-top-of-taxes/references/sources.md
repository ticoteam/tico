# Sources

All read on 2026-11-18 unless a line says otherwise.

## Authorities (primary)

- IRS, Instructions for Forms 1099-MISC and 1099-NEC (Rev. 12/2026): thresholds, due dates, exceptions, e-filing.
  https://www.irs.gov/instructions/i1099mec
- IRS, Topic no. 307, Backup withholding (updated 2026-09-24). https://www.irs.gov/taxtopics/tc307
- IRS, Understanding your Form 1099-K (updated 2026-11-17).
  https://www.irs.gov/businesses/understanding-your-form-1099-k
- IRS, E-file information returns with IRIS (updated 2026-10-26).
  https://www.irs.gov/filing/e-file-information-returns-with-iris
- IRS, Publication 509, Tax Calendars (2026). https://www.irs.gov/publications/p509
- IRS, Estimated taxes (updated 2026-09-25).
  https://www.irs.gov/businesses/small-businesses-self-employed/estimated-taxes
- Delaware Division of Corporations, Annual Report and Tax Instructions. https://corp.delaware.gov/paytaxes/

## Secondary (leads only; confirm at the authority)

- Sales Tax Institute, Economic Nexus State Guide chart, as of 2026-08-01: thresholds, windows, transaction tests.
  https://www.salestaxinstitute.com/resources/economic-nexus
- The date the FIRE system stops taking submissions (reported as 2026-11-19) came from a search summary, not from an
  IRS page.

## Public agent skills (ideas adapted, text original)

- Anthropic, knowledge-work-plugins (Apache-2.0), `small-business/skills/tax-prep` and `tax-season-organizer`: close
  the books before any tax number, state every assumption, total contractor payments by payee across every payment
  source, do not auto-merge similar payees, never reproduce an identifier. Note: on the date read its
  tax-season-organizer still used a 600 dollar 1099 threshold and 2025 figures; the 2026 figure is 2,000 dollars. Those
  skills never file anything. This skill files what the authority table allows, by the owner's decision.
  https://github.com/anthropics/knowledge-work-plugins
- Anthropic, financial-services (Apache-2.0): read in full for tax skills; it has none.
  https://github.com/anthropics/financial-services

## Tico (rules folded in)

- `templates/catalog/tax-specialist/skills/build-a-filing-calendar-line`, `check-a-sales-tax-threshold`,
  `gather-contractor-tax-forms`, and `playbooks/accountant-request.md`: the calendar line recipe, the threshold
  method, the contractor list and the accountant request log.
- `templates/catalog/compliance-manager/skills/verify-a-due-date-at-source` and `build-a-filing-pack`.
- `docs/finance-tools.md`: the `sales_tax` and `1099` workpapers, `tax_jurisdiction_add`, `tax_remit`, due dates.

## Gaps (not verified; the skill words these as "check")

- Corporate estimated tax safe harbours (Form 2220) were not read.
- The Streamlined Sales Tax state page returned an error; no claim about member states is made.
- State 1099 filing rules, state rules on software and digital goods, and city taxes were not read.
- That an IRIS application needs a person's identity check is from general knowledge of IRS e-Services, not from a
  page read today.
- "January 31, 2027 is a Sunday, so the working date is February 1" is arithmetic from the weekend rule, not a date
  printed by the IRS.
