# Sources

Read or searched on 2026-11-18 unless a line says otherwise. Ideas were adapted; no text was copied.

## Research

- Abeler, Calaki, Andree and Basek, "The power of apology", Economics Letters 107(2), 2010.
  A field experiment with a seller on eBay Germany: customers who had left a neutral or negative evaluation were
  far more likely to withdraw it after a plain apology (about 45%) than after an offer of 2.50 or 5 euros (about 21
  to 23%, depending on the summary read). One seller, one platform, 2010. Used for: apology before compensation.
- de Matos, Henrique and Rossi, "Service Recovery Paradox: A Meta-Analysis", Journal of Service Research 10(1), 2007.
  Across the studies pooled, a strong recovery raised satisfaction above the no-failure level, but the effect was
  not significant for repurchase intention, word of mouth or company image. Used for: recovery is a repair, not a
  growth tactic; the cause must be removed.
- Dixon, Freeman and Toman, "Stop Trying to Delight Your Customers", Harvard Business Review, July-August 2010.
  Idea used: lowering customer effort matters more to loyalty than extras. Used to keep gestures small and the fix
  effortless. No figure from the article is quoted in this skill.
- Bain & Company, Net Promoter System, "Closing the loop" and related articles on the inner and outer loop.
  https://www.netpromotersystem.com
  Ideas used: an inner loop of personal follow-up with each unhappy customer, described as within 24 hours at
  leading companies, and an outer loop that fixes causes. Bain is the originator of the method, so treat it as the
  advocate's view.

## Practitioners

- Jan Carlzon, "Moments of Truth" (1987). Ideas used: each customer contact is a moment that decides the
  relationship; the person at the front may solve the problem without asking upward.
- Will Guidara, "Unreasonable Hospitality" (2022), and interviews about it. Ideas used: apologise quickly and about
  the person's experience before sorting out fault; a gesture specific to the person beats a generic one; endings
  are remembered.
- Ritz-Carlton's employee discretion (up to $2,000 per guest to resolve a problem without approval) is widely
  repeated in business writing and attributed to interviews with former president Simon Cooper. Secondary sources
  differ on whether it is per incident or per day. Not verified against a company document; quoted in this skill
  only as a comparison and labelled as reported.

## Skills and docs drawn from

- Anthropic, knowledge-work-plugins, customer-support `customer-escalation` and `draft-response` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins
  Ideas used: one owner per escalation; say what is known, what is not and when the next update comes.
- Tico catalog skills in `templates/catalog/`: escalations-manager, reputation-manager, returns-and-refunds-specialist,
  retention-specialist, support-owner. Rules folded in: severity with one owner, customer updates that state only
  confirmed facts, review replies with no private details, refund amounts worked from the order.
- Tico docs: `docs/mail.md`, `docs/tasks.md`, `docs/finance-tools.md`, `docs/pages.md`.

## Law and platform rules to check locally

Checked 2026-11-18. These change; read the current text before relying on any of them.

- FTC Trade Regulation Rule on the Use of Consumer Reviews and Testimonials, 16 CFR Part 465, in force from
  21 October 2024: among other things it bars giving compensation or other incentives conditioned on a review
  expressing a particular sentiment.
- Review platforms have their own bans. Google's policy on fake engagement prohibits offering incentives for
  reviews, or for revising or removing a negative one. Check each platform the team is listed on.
- FTC Endorsement Guides, 16 CFR Part 255: a material connection between a reviewer and the business must be
  disclosed.
- Apology and liability: whether saying sorry can be used as evidence of fault varies by US state and by subject
  (many apology statutes cover only medical settings and only expressions of sympathy). Outside routine service
  failures, get a legal read.
- Refunds and remedies the law owes (consumer guarantees, cooling-off periods, chargebacks) differ by country and
  state. The team's written policy is the floor only where it is at least as generous as the law.

## Numbers in this skill

The yes limit ($100 per customer per incident, capped at 12 months of what they paid; a monthly total of the
recovery budget line, else 1% of last month's revenue, else $500), the one-hour apology, the three-day follow-up and
the 20-response floor for reporting a percentage are starting defaults chosen for this library on 2026-11-18. They
are not benchmarks. The team's setup answers replace them.
