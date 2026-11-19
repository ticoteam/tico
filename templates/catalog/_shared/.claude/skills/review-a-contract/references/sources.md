# Sources

Read or searched on 2026-11-18. Ideas were adapted; no text was copied.

## Skills and guides drawn from

- Anthropic, knowledge-work-plugins, legal `review-contract` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins/tree/main/legal/skills/review-contract
  Ideas used: load the playbook first; read the whole document; the clause categories; green, yellow and red; the
  redline unit (current words, replacement, reason for the other side, priority, fallback); three negotiation tiers.
- Anthropic, knowledge-work-plugins, legal `triage-nda` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins/tree/main/legal/skills/triage-nda
  Ideas used: when no playbook is configured, go on with defaults and say so clearly in the output; a non-solicit,
  a non-compete or liquidated damages inside an NDA is a red flag.
- Anthropic, claude-for-legal, commercial-legal `nda-review` (Apache-2.0).
  https://github.com/anthropics/claude-for-legal/tree/main/commercial-legal/skills/nda-review
  Ideas used: the smallest edit first (word, phrase, subclause, sentence); replace a whole clause only when nothing
  smaller works; new drafting goes to counsel; obligations that do not belong in an NDA (standstill, licence,
  exclusivity, non-solicit) are always flagged. That skill ships no default positions and gives no green result
  without the team's own; this skill differs on that point: it reviews on
  labelled defaults and still needs the signer's OK to sign.
- evolsb, claude-legal-skill (MIT). https://github.com/evolsb/claude-legal-skill
  Idea used: a fixed list of expected clauses, checked for what is missing.
- borghei, Claude-Skills, legal `nda-review` (MIT with Commons Clause; ideas only, no wording).
  https://github.com/borghei/Claude-Skills/tree/main/legal/nda-review
  Idea used: an owner and a due date on every open point.

## Tico skills merged into this one

- `templates/catalog/contracts-manager/skills/flag-a-clause-against-the-playbook`: the four-part flag (clause,
  position with its date, what the contract says, the difference), fallback only from the playbook, the words to avoid.
- `templates/catalog/paralegal/skills/check-an-nda-clause-by-clause`: same, inside or outside; exact strike and insert
  text; the count check between the header and the rows.
- `templates/catalog/paralegal/playbooks/nda-desk.md`, step 2: the NDA clause groups (kept as lines N1 to N8).

## Tico skills used as sub-steps

- `templates/catalog/contracts-manager/skills/check-quotes-against-the-current-text`
- `templates/catalog/_shared/.claude/skills/count-a-notice-deadline`
- `templates/catalog/paralegal/skills/fill-an-outbound-nda-from-template`
- `templates/catalog/paralegal/skills/file-a-signed-agreement-in-the-index`
- `templates/catalog/privacy-manager/skills/review-a-dpa-against-the-position`
- `templates/catalog/_shared/.claude/skills/write-a-counsel-brief`

## Facts and figures

- The default playbook states positions, not facts. Its figures (12 months' fees, 30, 60 and 90 days, 3 years, 1.5%
  a month) are common starting points written from general practice. No survey or benchmark is cited for them and
  none should be implied in a review.
- NDA term data and the trade-secret whistleblower notice are covered, with sources, in the paralegal role's
  `knowledge/nda-practice.md`. They are not restated here.
- Automatic-renewal law is covered, with sources, in the contracts-manager role's `knowledge/contract-practice.md`.

## Gaps

- The default playbook was not reviewed by a lawyer. A team should adopt it only after its signer, and ideally its
  lawyer, has read it.
- United States commercial contracts only. No positions for employment, contractor, lease, loan, licensing-in of
  content, reseller or government contracts.
- No model clause wording is supplied. The bot changes numbers and short phrases and otherwise sends drafting to
  counsel. A team that wants the bot to insert full clauses must put its own approved wording in the playbook.
- Enforceability by state (limits on liability, indemnities, non-competes, automatic renewal) is not surveyed.
