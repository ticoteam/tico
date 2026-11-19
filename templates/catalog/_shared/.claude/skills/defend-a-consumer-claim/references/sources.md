# Sources

Read or searched on 2026-11-18. Ideas were adapted; no text was copied. No public skill was found that covers
defending a consumer claim from the business side, so most of this skill is built from primary pages (courts,
arbitration providers, card-network and processor guides) and general practice.

## Skills and guides drawn from

- Anthropic, claude-for-legal, litigation-legal `legal-hold` (Apache-2.0).
  https://github.com/anthropics/claude-for-legal/tree/main/litigation-legal/skills/legal-hold
  Ideas used: trigger list, scope by people, dates and systems, written notice with acknowledgment, periodic
  refresh, release only on written approval, departed teammates' accounts.
- Anthropic, claude-for-legal, litigation-legal `demand-received` (Apache-2.0).
  https://github.com/anthropics/claude-for-legal/tree/main/litigation-legal/skills/demand-received
  Ideas used: three tiers of dates, the insurance check.
- The Sedona Conference, Commentary on Legal Holds (the trigger: litigation reasonably anticipated, a credible
  probability rather than a bare possibility). https://thesedonaconference.org/node/8207
- National Consumer Law Center, arbitration litigation cheat sheet (written for consumer advocates; used in reverse
  as the red-team list for the company's own clause). https://library.nclc.org/article/arbitration-litigation-cheat-sheet
- `in-consumer-debt` (a court-documents skill for consumers defending debt suits). Only the pattern of sorting by
  forum and deadline first was used.
  https://www.claudepluginhub.com/skills/codearranger-in-court-docs-plugins-in-court-docs-2/in-consumer-debt

## Tico skills used as sub-steps

- `templates/catalog/_shared/.claude/skills/check-a-legal-deadline`
- `templates/catalog/_shared/.claude/skills/write-a-counsel-brief`
- `templates/catalog/privacy-manager/skills/run-a-data-request-to-deadline`
- `templates/catalog/privacy-manager/skills/log-breach-facts-for-counsel`

## Facts checked

Each can change. Re-read at the source before relying on one.

Chargebacks
- Visa Compelling Evidence 3.0 (two earlier undisputed transactions 120 to 365 days old, two matching data
  elements, one being IP address or device identifier). Visa merchant readiness guide,
  https://usa.review.visa.com/content/dam/VCOM/regional/na/us/support-legal/documents/compelling-evidence-3.0-merchant-readiness-mar2023.pdf ;
  https://support.checkout.com/hc/en-us/articles/14323750143634-Visa-Compelling-Evidence-3-0-Overview ;
  https://help.adyen.com/knowledge/risk/dispute-management/faqs-visa-compelling-evidence-3
- Visa monitoring program thresholds (1.5% from 1 April 2026, with a 1,500-event floor). Vendor guides only:
  https://justt.ai/blog/visa-vamp-threshold-changes-april-2026/ ;
  https://chargebackhelp.com/visa-vamp-thresholds-just-got-stricter-in-2026/ . Visa's own rules were not read.
- Mastercard excessive chargeback program. Vendor guide only, and guides disagree on the ratio:
  https://redo.com/resources/articles/chargebacks/mastercard-excessive-chargeback-program
- Processor timing, single submission and fees (Stripe as the example). Third-party guides only:
  https://redo.com/resources/articles/chargebacks/stripe-dispute-response ;
  https://chargebacks911.com/chargeback-types/stripe-chargebacks/stripe-chargeback-timeline/ ;
  https://redo.com/resources/articles/chargebacks/chargeback-time-limit . The processor's own documentation and the
  dispute record are the authority.
- Whether to contest every dispute or only some: vendors disagree.
  https://justt.ai/blog/chargeback-win-rates-kpi/ (contest nearly all) against
  https://www.chargebackgurus.com/blog/when-to-accept-a-chargeback and
  https://www.chargebackstop.com/blog/fight-or-refund-when-to-dispute-vs-resolve-automatically (sort by claim,
  evidence and cost). The owner chose the first view on 2026-11-18: contest every chargeback, and refund a customer
  who deserves it only after the dispute closes.

Small claims
- California: https://courts.ca.gov/selfhelp-smallclaims.htm and county court pages, for example
  https://sanbernardino.courts.ca.gov/self-help/self-help-small-claims/small-claims-general-information and
  https://saccourt.ca.gov/small-claims/process.aspx (limits of $12,500 and $6,250; no lawyers at the hearing; the
  Defendant's Claim timing; appeal by the party sued, within 30 days of the clerk's notice).
- Who appears for a corporation in California (Code of Civil Procedure section 116.540): stated from general
  knowledge; not re-read in this run.
- Other states' limits: only unofficial tables were found and they conflict. The skill gives no table and sends the
  bot to the court's page.
- Answer deadlines: Federal Rule of Civil Procedure 12(a) (21 days) and California Code of Civil Procedure section
  412.20 (30 days). Stated from general knowledge; the summons states the date that applies.
- A corporation must appear through a lawyer in federal court (Rowland v. California Men's Colony, 506 U.S. 194
  (1993)). From general knowledge; state rules outside small claims are generally the same but not surveyed.

Arbitration
- American Arbitration Association consumer rules, revised effective 1 May 2025. Law-firm summary:
  https://www.mayerbrown.com/ja/insights/publications/2025/05/american-arbitration-association-announces-new-consumer-and-employment-arbitration-rules .
  The rules and current fee schedule on adr.org were not read in full; no fee figure is stated in the skill.
- Mass-arbitration rules and fees:
  https://www.adr.org/media/lvgavfty/2026_consumer-mass-arbitration-feeschedule.pdf ;
  https://www.troutman.com/insights/managing-mass-arbitration-risk-looking-beyond-provider-mass-arbitration-rules/ ;
  https://www.omm.com/insights/alerts-publications/mass-arbitrations-in-2025-key-legal-shifts-every-company-should-know/
- Hohenshelt v. Superior Court (California Supreme Court, 11 August 2025) on late arbitration fees:
  https://www.gibsondunn.com/california-supreme-court-narrowly-construes-statute-governing-the-failure-to-timely-pay-arbitration-fees-to-avoid-federal-preemption/ ;
  https://www.adr.org/news-and-insights/from-rigid-to-reasonable-supreme-court-clarifies-arbitration-fee-payment-rules-in-california/
- Morgan v. Sundance, Inc., 596 U.S. 411 (2022); Berman v. Freedom Financial Network, LLC, 30 F.4th 849 (9th Cir.
  2022); Heckman v. Live Nation Entertainment, Inc., 120 F.4th 670 (9th Cir. 2024). From general knowledge; the
  opinions were not re-read and the citations should be checked before any is quoted.
- Whether to keep a consumer arbitration clause:
  https://www.goodwinlaw.com/en/insights/publications/2025/12/insights-technology-cldr-mass-arbitration-the-risk-lurking-in-consumer-agreements ;
  https://www.wnj.com/updates/spring-cleaning-your-dispute-resolution-terms-and-conditions-tips-for-avoiding-mass-arbitrations/ ;
  https://ppc.land/amazon-forces-shoppers-into-arbitration-five-years-after-dropping-it/ (search summary only).

Consumer-protection statutes
- Notice windows (California, Massachusetts, Texas): sources are listed in
  `answer-a-demand-or-complaint/references/sources.md`.
- California automatic-renewal amendments in force 1 July 2025:
  https://www.cooley.com/news/insight/2025/2025-06-04-california-automatic-renewal-law-amendments-take-effect-on-july-1-2025
- Federal and state fee-disclosure rules: not re-read in this run. The skill names no rule and flags for a lawyer.

## Gaps

- Written without a litigator's review. A lawyer should read this skill and its references before a team relies on
  them for anything beyond a routine chargeback.
- Card-network rules were read through processors' and vendors' pages, which sell dispute services and sometimes
  conflict. Every number is dated and the bot is told to read the processor's record.
- Small-claims procedure is given in detail for California only.
- JAMS consumer rules were not read; the arbitration steps name the American Arbitration Association as the example.
- No state-by-state survey of consumer-protection remedies, limitation periods or automatic-renewal laws.
