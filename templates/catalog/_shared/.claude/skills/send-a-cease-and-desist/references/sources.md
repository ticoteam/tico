# Sources

Read or searched on 2026-11-18 unless a line says otherwise. Ideas were adapted; no text was copied.

## Skills and guides drawn from

- Anthropic, claude-for-legal, ip-legal `cease-desist` (Apache-2.0).
  https://github.com/anthropics/claude-for-legal/tree/main/ip-legal/skills/cease-desist
  Ideas used: the pre-send gate (right valid, claim holds up, demand proportionate, named approver), counterparty
  diligence before drafting, rating the risk that the recipient sues first, the platform report as a parallel path,
  never inventing a citation.
- mohitagw15856, pm-claude-skills `demand-letter` (MIT).
  https://skills.sh/mohitagw15856/pm-claude-skills/demand-letter
  Idea used: the fixed skeleton (dated facts, basis, specific demand with a deadline, stated consequence). Changed:
  a missing fact blocks the letter instead of becoming a placeholder.
- Mandour & Associates, trademark cease-and-desist checklist (law-firm guide).
  https://mandourlaw.com/trademark-cease-and-desist-letter
  Idea used: the required-parts list as a validation step.
- The Sedona Conference, Commentary on Legal Holds, 2nd ed. (2019). https://thesedonaconference.org/node/8207
  Idea used: sending a letter means a dispute is expected, so the sender keeps its own records too.

## Tico skills folded in as sub-steps

- `templates/catalog/contracts-manager/skills/check-quotes-against-the-current-text`
- `templates/catalog/_shared/.claude/skills/count-a-notice-deadline`
- `templates/catalog/ip-paralegal/skills/verify-an-ip-assignment`
- `templates/catalog/ip-paralegal/skills/check-a-trademark-deadline`
- `templates/catalog/ip-paralegal/skills/run-a-clearance-search`
- `templates/catalog/_shared/.claude/skills/write-a-counsel-brief`

## Law and facts checked

Each of these can change. Re-read at the source before relying on one in a letter.

- Declaratory-judgment risk from a letter: courts apply the "all the circumstances" test of MedImmune, Inc. v.
  Genentech, Inc., 549 U.S. 118 (2007) to trademark disputes; whether one letter is enough is fact-specific.
  Law-firm summaries: https://www.pillsburylaw.com/publications/how-trade-mark-defendants-are-winning-from-medimmune
  and https://www.theiplawblog.com/2010/05/articles/trademark-law/patent-minefield-now-a-risk-for-trademark-owners/
  (older articles; opinion not re-read).
- Copyright registration before suit: 17 U.S.C. section 411(a); Fourth Estate Public Benefit Corp. v.
  Wall-Street.com, LLC, 586 U.S. 296 (2019). Statutory damages and fees depend on registration timing: 17 U.S.C.
  section 412. Takedown notices: 17 U.S.C. section 512(c)(3) and (f); Lenz v. Universal Music Corp. (9th Cir. 2016)
  on considering fair use first. Stated from general knowledge of settled law; not re-read in this run.
- Trademark infringement and false designation: 15 U.S.C. sections 1114 and 1125(a). Not re-read in this run.
- Anti-SLAPP laws: about 39 to 40 states plus the District of Columbia as of spring 2026; seventeen states had
  adopted the uniform act by July 2026. Counts differ by source.
  https://www.rcfp.org/anti-slapp-legal-guide/ and
  https://www.beneschlaw.com/insight/anti-slapp-laws-on-the-rise-key-developments-since-2025/
- Consumer Review Fairness Act of 2016, 15 U.S.C. section 45b. Not re-read in this run.
- Platform immunity for third-party content: 47 U.S.C. section 230. Not re-read in this run.
- Federal non-compete rule: set aside in 2024, appeal withdrawn September 2025, removed from the Code of Federal
  Regulations February 2026; the agency continues case-by-case enforcement.
  https://www.mondaq.com/unitedstates/employee-rights-labour-relations/1706482/ftc-officially-abandons-prior-non-compete-rule-but-its-work-on-this-issue-isnt-over-yet
  and
  https://antitrust.weil.com/client-alert-ftc-walks-away-from-rulemaking-but-not-the-fight-on-non-competes
- California: Business and Professions Code sections 16600, 16600.1 and 16600.5 (void restraints, notice duty,
  and enforcement attempts unlawful regardless of where the contract was signed; in force from 1 January 2024).
  Stated from general knowledge; not re-read in this run.
- Defamation limitation periods (often one year, up to three) and retraction statutes vary by state. No single
  source; check the state.

## Gaps

- No public skill was found that covers defamation, contract-breach or non-solicit letters with verified elements.
  Those sections of `elements-by-claim.md` are written from general legal practice and should be reviewed by a
  lawyer before the skill is relied on for a large matter.
- No source gives safe wording that removes sue-first risk. The skill treats the risk as a reason to choose a
  different tool, not as a wording problem.
