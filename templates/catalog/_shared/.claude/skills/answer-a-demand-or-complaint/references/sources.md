# Sources

Read or searched on 2026-11-18. Ideas were adapted; no text was copied.

## Skills and guides drawn from

- Anthropic, claude-for-legal, litigation-legal `demand-received` (Apache-2.0).
  https://github.com/anthropics/claude-for-legal/tree/main/litigation-legal/skills/demand-received
  Ideas used: the fields to extract, the four merit ratings as a routing aid, the response options, three tiers of
  dates, the hold check even when ignoring, the insurance check, the caution that a settlement label is not
  protection by itself. That skill stops at triage; this one drafts the reply.
- Anthropic, knowledge-work-plugins, legal `legal-response` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins/tree/main/legal/skills/legal-response
  Idea used: a stop list checked before any reply is generated.
- Anthropic, claude-for-legal, ip-legal `cease-desist`, receive mode (Apache-2.0).
  https://github.com/anthropics/claude-for-legal/tree/main/ip-legal/skills/cease-desist
  Idea used: the options for a cease-and-desist received, including that asking a court to rule first is a
  lawyer's step.
- terms.law, guide to responding to a demand letter (a California attorney's site; human guide).
  https://www.terms.law/Demand-Letters/respond/demand-letter-received/
  Ideas used: reply in writing by the date or paper an extension; facts only; concede nothing in a first reply.

## Tico skills used as sub-steps

- `templates/catalog/_shared/.claude/skills/check-a-legal-deadline`
- `templates/catalog/_shared/.claude/skills/write-a-counsel-brief`
- `templates/catalog/contracts-manager/skills/check-quotes-against-the-current-text`
- `customers/answer-a-customer` (ordinary tickets stay there; it replaces `support-owner/skills/write-a-reply-with-a-marked-gap`)

## Facts checked

Each can change. Re-read at the source before relying on one.

- Better Business Bureau: 14 calendar days to respond, a follow-up notice if there is none, most complaints closed
  in about 30 days, unanswered complaints count against the rating. https://www.bbb.org/file-a-complaint and
  https://www.bbb.org/bbb/great-west-pacific/articles/complaint-lifecycle ; rating effect from a secondary source,
  https://www.nerdwallet.com/business/learn/keep-an-a-rating-with-the-better-business-bureau . Local offices vary.
- Consumer Financial Protection Bureau company response times (15 calendar days, final within 60).
  https://www.consumerfinance.gov/compliance/consumer-complaint-program/company-process/
- California Civil Code section 1782 (30-day correction window; damages action barred by an appropriate
  correction). Text as reproduced at https://california.public.law/codes/ca_civ_code_section_1782 ; Valdez v.
  Seidner-Miller, Inc., 33 Cal.App.5th 600 (2019) on offers tied to a release, summarized at
  https://www.uclpractitioner.com/uclpractitioner/2019/04/29/recent-opinion-addresses-clras-pre-lawsuit-demand-and-correction-procedures-valdez-v-seidner-miller
  Official text not re-read.
- Massachusetts General Laws chapter 93A, section 9(3).
  https://malegislature.gov/Laws/GeneralLaws/PartI/TitleXV/Chapter93A/Section9 ; the attorney general's page,
  https://www.mass.gov/service-details/30-day-demand-letter ; Kohl v. Silver Lake Motors, Inc., 369 Mass. 795 (1976).
- Texas Business and Commerce Code sections 17.505 and 17.5052.
  https://texas.public.law/statutes/tex._bus._&_com._code_section_17.5052 (secondary copy; official text not
  re-read).
- State attorney general mediation timelines: no single source; the date in the agency's letter governs. The "two
  to four weeks" range in the channels file is general practice, not a rule.
- Federal Rule of Evidence 408 (compromise offers and negotiations). Stated from general knowledge of the rule; not
  re-read in this run. State rules differ.
- United States v. Heppner, No. 1:25-cr-00503 (S.D.N.Y., bench ruling 10 February 2026, opinion 17 February 2026):
  documents a defendant made with an AI tool on his own were not privileged or work product. Law-firm summaries:
  https://www.proskauer.com/alert/sdny-addresses-privilege-and-work-product-implications-of-using-unsecured-public-AI-tools
  and https://www.hsfkramer.com/insights/2026-02/new-york-court-finds-that-client-chats-with-claude-are-not-privileged .
  A district court ruling; later developments not checked.

## Gaps

- No public skill was found that drafts the reply itself. The ten properties, the templates and the admission-control
  file are written from general practice and should be reviewed by a lawyer before the skill is relied on for a
  large matter.
- Consumer-protection notice rules exist in more states than the three listed. The skill tells the bot to read the
  cited statute on day one; it does not carry a fifty-state table.
- Apology and sympathy laws vary by state and were not surveyed.
