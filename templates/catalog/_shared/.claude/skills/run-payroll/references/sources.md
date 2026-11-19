# Sources

All read on 2026-11-18 unless a line says otherwise.

## Authorities (primary)

- IRS, Employment tax due dates (updated 2026-04-21).
  https://www.irs.gov/businesses/small-businesses-self-employed/employment-tax-due-dates
- IRS, Publication 15, Employer's Tax Guide (2026). https://www.irs.gov/publications/p15
- IRS, Topic no. 757 (updated 2026-11-10) and Topic no. 759 (updated 2026-11-09).
  https://www.irs.gov/taxtopics/tc757 and https://www.irs.gov/taxtopics/tc759
- IRS, Independent contractor (self-employed) or employee? (updated 2026-05-19).
  https://www.irs.gov/businesses/small-businesses-self-employed/independent-contractor-self-employed-or-employee
- US Department of Labor, Wage and Hour Division, misclassification rulemaking page.
  https://www.dol.gov/agencies/whd/flsa/misclassification/rulemaking
- IRS, Standard mileage rates (updated 2026-07-28). https://www.irs.gov/tax-professionals/standard-mileage-rates
- SBA, Hire and manage employees. https://www.sba.gov/business-guide/manage-your-business/hire-manage-employees
- Triple-I, workers' compensation chapter of its small business guide (uninsured contractors, new states); full
  citation in `manage-insurance/references/sources.md`.

## Secondary (leads only)

- 2027 Social Security wage base: Journal of Accountancy and payroll.org reports of the SSA announcement.
- Federal exempt salary floor and the state floors: search summaries of DOL and state pages.
- Field Assistance Bulletin 2025-1 (investigators told not to apply the 2024 contractor rule): search summary.
- What Gusto files, and its three-failures rule: search summary of gusto.com; its help page refused the fetch.
- Registering in a state before a remote employee's first payroll: vendor guides.

## Public agent skills (ideas adapted, text original)

- Anthropic, knowledge-work-plugins (Apache-2.0), `small-business/skills/payroll-prep` and `plan-payroll`: fix the
  period first; prove the source returns data; flag every anomaly and never silently correct; compare with the prior
  period and explain a move over 10 percent; check cash before the run (covered, tight, short); the Gusto connector's
  call names. Those skills never submit a run. This skill submits regular runs that pass every gate, by the owner's
  decision. https://github.com/anthropics/knowledge-work-plugins
- Anthropic, claude-for-legal (Apache-2.0), `employment-legal/skills/worker-classification`: check new arrangements
  only and send existing ones to counsel; one test per purpose; the state where the work is done decides; the intake
  headings; never state a test that was not read. https://github.com/anthropics/claude-for-legal
- Anthropic, financial-services (Apache-2.0): read for payroll skills; it has none.

## Tico (rules folded in)

- `templates/catalog/payroll-specialist/skills/check-a-pay-change-against-its-source`,
  `compare-the-register-after-a-run`, `reconcile-headcount-and-gross`, its `knowledge/payroll-practice.md` and
  `AGENTS.md` (pay confidentiality).
- `templates/catalog/tax-specialist/skills/gather-contractor-tax-forms`.
- `docs/finance-tools.md`: metrics, payments, the rule that payroll's private lines stay private.

## Gaps (not verified; the skill words these as "check")

- IRS Publication 463's accountable plan chapter and the Form I-9 timing were not re-read.
- The federal new-hire reporting page refused the fetch; the 20-day figure is the SBA's.
- No state page was read. Every state rule in this skill is an instruction to read the state's page.
