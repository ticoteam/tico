# Evidence method

The standing method behind the monthly page and the access reviews. `controls.md` holds this team's controls and their
cadences. This file holds how evidence is judged and what the frameworks say, so each run does not re-derive it. Framework
facts name their source and the year read. Confirm each one at its publisher before it goes into an auditor's hands.

## Frameworks

- **SOC 2** (AICPA Trust Services Criteria). The criteria do not set how often an access review happens. The cadence is the
  one the team committed to, and the evidence must match it. Vendor guides (read 2026-11-01, not verified) describe quarterly
  reviews of privileged access and semi-annual reviews of standard access as common, and some auditors expect a review that
  removes nothing to be questioned. Read the criteria text before you cite a criterion number. The criteria were not read for
  this file.
- **ISO/IEC 27001:2022, Annex A.** 93 controls in four themes: organisational (A.5.1 to A.5.37, 37 controls), people (A.6.1 to
  A.6.8, 8 controls), physical (A.7.1 to A.7.14, 14 controls) and technological (A.8.1 to A.8.34, 34 controls). Vendor summaries
  agree on these counts (read 2026-11-01); check the standard before you quote one. Access rights is A.5.18 in the control list.
- **NIST Cybersecurity Framework 2.0.** Published 26 February 2024, with six functions: Govern (new in 2.0), Identify, Protect,
  Detect, Respond and Recover. Secondary sources give 22 categories and 106 subcategories (read 2026-11-01). Check nist.gov
  before quoting the counts. The framework is voluntary; an auditor or customer may ask for it by name.

Each control in `controls.md` names the reference it answers. A control with no reference is marked, not guessed.

## What counts as evidence

- **Dated inside the period.** A file dated before the period, or after it with no note, is not evidence for that period.
- **Shows the thing.** A screenshot needs a visible date and the account or system name. A count needs its export and the
  filter used.
- **Complete for the population.** When a sample is needed, keep the full population list and say how the sample was picked.
  Auditors ask for the list, not only the sample.
- **Clean.** No credentials, passwords, keys or personal data, with no exception. Replace a screenshot that shows
  one, and say that it was replaced and why.
- **Filed where the log says.** One logged entry per control per period, with the file link. A file that is not logged is not
  found in an audit.
- **Owner's sign-off** on a review: a name and a date next to each decision.

## Access reviews

- The roster to compare against comes from the team's own list (`hub team show`). A person marked "left" who still has
  access is the first finding.
- The dormant threshold is the team's policy and is written in `controls.md`. The playbook default is 45 days without a login
  where the tool shows logins. Pick one value, write it down, and use it every quarter.
- Shared and service accounts each need a named owner. A service account with no owner is a finding.
- A review is complete only when a fresh export shows the removals made. The before and after exports are both filed.

## Policy acknowledgements

- Acceptance is due on joining and yearly, as the team's policy says. The playbook's flag is a person who joined more than 14
  days ago without accepting, which is a team choice, not a framework rule.
- A missing acceptance is a finding before an auditor finds it.

## Sources

- AICPA Trust Services Criteria: not read for this file. Vendor guides on access-review cadence, read 2026-11-01, not verified.
- ISO/IEC 27001:2022 Annex A counts and identifiers: vendor and reference summaries, read 2026-11-01. Check against the standard.
- NIST CSF 2.0 release date and structure: secondary summaries, read 2026-11-01. Check nist.gov.
