# Purchasing practice

The standing method behind `playbooks/compare-vendors.md`. `criteria.md` holds this team's must-haves and weights. This
file holds how the method works and what the sources say, so each run does not re-derive it. Figures name their source
and the year it was read. Check a figure at its source before you quote it in a comparison.

## Weighted scoring

- Weighted scoring suits service purchases, where the criteria differ in importance. Public procurement guidance (UN and
  Victorian government evaluation guides, excerpts read 2026-11-01) describes each evaluator scoring each offer against
  the weighted criteria and being able to justify each score before the group agrees.
- Weights follow business impact and risk. A must-have security or workflow need should outweigh a cosmetic preference.
- Commercial templates give different weights. One supply-chain article suggests price at about 20 to 30 percent of the
  total; another template puts quality and delivery at 50 percent or more. These are rules of thumb, not standards. Use
  the team's own weights and record them before any vendor is scored.
- A weighted cost method folds price into the score (for example 30 percent cost and 70 percent requirements). It is
  useful when price and fit trade off, and it hides a failed must-have unless knockouts are checked first.

## Whole-term cost

Line items that most often differ between quotes (commercial guidance, read 2026-11-01):
- subscription or unit price, and how it scales past the quoted seat band;
- setup, onboarding and data migration;
- support tier, and what the tier you need costs;
- renewal uplift, in the contract clause and its percentage;
- integration and admin time the team carries itself, which no quote includes;
- minimum commitment, payment timing (annual prepay changes cash, not total), and currency;
- cost of leaving: data export format, whether it is free, deletion on exit, notice days.

## Security questionnaires

- CAIQ (Cloud Security Alliance): free; for cloud providers. CAIQ v4 has about 260 questions mapped to the Cloud Controls
  Matrix v4 across 17 domains (described by vendor blogs, read 2026-11-01; check the CSA page before you quote the count).
- SIG (Shared Assessments): licensed. SIG Lite is the shorter version for lower-risk vendors. Sources disagree on its
  question count; check the Shared Assessments page before quoting one.
- The team's own starter list is in `knowledge/security-questions.md`. Use a standard questionnaire when the vendor already
  returns one; the starter list covers what the team needs in either case.
- A vendor's answer is read with `skills/read-a-vendor-security-answer/SKILL.md`.

## Common mistakes

- Changing weights after the scores are in. Lock them first and record the date.
- Judging on the quoted unit price alone. It leaves out setup, tiers, uplift and exit.
- A high composite score hiding a failed must-have. Check knockouts separately.
- Too many low-value criteria, which dilute the ones that matter.
- Underweighting integration effort and support responsiveness, which the team pays for after signing.

## Sources

- UN and Victorian government evaluation guides (weighted scoring for services), excerpts read 2026-11-01.
- Commercial TCO and weighting guidance (supply-chain and procurement blogs, 2025 to 2026): used for line items only, not
  for weights or percentages.
- CSA CAIQ v4 and Shared Assessments SIG descriptions: from vendor blogs, read 2026-11-01. Confirm counts at the publishers.
