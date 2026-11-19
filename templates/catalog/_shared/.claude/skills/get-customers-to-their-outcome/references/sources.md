# Sources

Read or searched on 2026-11-18 unless a line says otherwise. Ideas were adapted; no text was copied.

## Skills and guides drawn from

- Anthropic, knowledge-work-plugins, customer-success and sales skills, including `customer-health` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins
  Ideas used: several signal families read together; a signal with no data is reported as not visible rather than
  guessed. Changed: outcome progress added as its own family and placed first; weights are calibrated against the
  team's own decided accounts rather than fixed.
- Lincoln Murphy's writing on customer success (the "desired outcome": the result the customer needs, reached
  through an experience that suits them). Idea used: the two halves of the outcome plan. Wording is original.
- Gainsight's and other vendors' public writing on health scores and success plans. Ideas used in general form:
  milestone-based onboarding, time to first value, reviewing score accuracy against renewals. Vendor material;
  no figures taken from it.
- For Dean Peters' and lenny-skills material noted in this library's inventory: ideas only, no wording.
- Tico catalog skills in `templates/catalog/`: success-owner, customer-onboarding-specialist, account-manager,
  retention-specialist, contracts-manager. Rules folded in: the four original signal families and the hard signals
  (stated intent to leave, champion gone, failed payment unrecovered for 14 days); milestones as states the customer
  can show; the six handoff items; stall states; renewal work dated from the notice date and started 120 days out;
  one save offer; saves measured at 30 and 90 days; counts not rates below about five cancellations a week.
- Tico docs: `docs/growth.md` (segments, customer facts, cohorts), `docs/mail.md`, `docs/pages.md`,
  `docs/goals-and-kpis.md`, `docs/tasks.md`.

## On the calibration method

The method in `health-score.md` (look back 90 days before each decision, count how each signal split leavers from
stayers, weight by lift, cap, test on a held-back quarter) is a plain version of standard practice for checking a
predictive score. It is written for a bot working with a spreadsheet-sized history, not as a statistical model.

- The thresholds in it (about 30 decided accounts including about 10 bad outcomes; cells under 5 are too few to
  tell; a cap of 40 on one family) are rules of thumb chosen for this library. They are not derived from a study.
  With more history, a proper model (for example a logistic regression checked on held-back data) will do better;
  a data-capable teammate should take over at that point.
- The starting weights (30, 30, 15, 15, 10) are an uncalibrated guess and are labelled so wherever they appear.

## Law to check locally (subscriptions and renewals)

Checked 2026-11-18. These change; read the current text.

- United States, federal: the FTC's amended Negative Option Rule ("click to cancel", 2024) was vacated by the Eighth
  Circuit on 8 July 2025. The FTC restarted rulemaking in 2026. The Restore Online Shoppers' Confidence Act (ROSCA)
  and Section 5 of the FTC Act continue to be enforced against hard-to-cancel subscriptions.
- United States, states: many have automatic-renewal laws with their own notice and cancellation rules. California's
  (Business and Professions Code section 17600 and following, as amended by AB 2863, effective 1 July 2025) is the
  one most often used as the high-water mark.
- European Union: a withdrawal function ("withdrawal button") for distance contracts concluded online applies from
  19 June 2026 under amendments to the Consumer Rights Directive. Taken from the Tico catalog's note and secondary
  summaries; confirm against the directive and the national law.
- Business-to-business contracts are mostly governed by the contract itself; read the renewal and notice clauses.

## Numbers in this skill

The 120-day renewal start, the 90-day look-back, the 14-day failed-payment line, the 100-word touch limit, the
30 and 90 day save checks and every band rule are starting defaults for this library. None is a benchmark. The
team's own values in `knowledge/success.md` and `knowledge/health-rules.md` replace them.
