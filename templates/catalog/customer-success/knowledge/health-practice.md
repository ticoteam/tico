# Health practice

Standing reference for account health and adoption. It holds what is known about how health is read and where the signals
come from. The team's own rules live in `health-rules.md`. Where the two disagree, the team's rules win and the
disagreement is written in the account note.

## Four signal families
- **Adoption.** Does the customer use what they bought? Active users against seats, the key action the product exists for,
  and the trend over the last 30 days against the prior 90. A trend tells more than a level.
- **Support.** Open tickets, their age and severity, and repeat contacts about the same problem. Several tickets on one
  unresolved issue are a stronger signal than many tickets on different questions.
- **Relationship.** The named champion and the economic buyer: when each last spoke with us, and whether either has changed.
  A champion who left is a red signal until a successor is named.
- **Commercial.** Payment failures, downgrades, seat reductions, and procurement questions about the renewal.

## Starting rules (the team tunes them)
These are starting thresholds, not industry benchmarks. Set them in `health-rules.md` after two quarters of readings.
- Red: two families red, or one hard signal: a stated intent to leave, a champion gone with no successor named, or a failed
  payment unrecovered after 14 days.
- Yellow: one family red, or two families yellow.
- Green: no family red and at most one yellow.
- Waiting on customer: the account is blocked on a step the customer owns, with a dated request. Show the date and review
  the account on that date.

## Retention context (dated benchmark)
- The 2025 KeyBanc Capital Markets and Sapphire Ventures SaaS survey summary page (Sapphire Ventures, read 2026-11-01) says
  SaaS companies are "maintaining gross retention near 90% and net retention above 100%". The page gives no medians. Read the
  full report before quoting a band.
- Use it as context for an account plan. It is not a target for one customer.

## Churn sources
- Failed payments (involuntary churn) are part of total churn. Vendor reports put their share in a wide range; most figures
  sit between roughly a fifth and a third of churn. These come from vendor blogs that cite Recurly and Stripe (read 2026-11-01)
  and were not checked at source.
- Route a failed payment to billing recovery. It is not a health argument with the customer.

## Sources
- Sapphire Ventures, "2025 KeyBanc Capital Markets & Sapphire Ventures SaaS Survey" summary page, read 2026-11-01.
- Vendor churn figures (Recurly, Stripe, Churnkey) via secondary write-ups, read 2026-11-01; not verified at source.
