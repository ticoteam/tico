# Paid media method

The standing rules behind the weekly review and every change proposal. Thresholds are platform guidance or
dated third-party benchmarks; each line says which.

## Measure the result, not the click

- One primary conversion per campaign, named in `knowledge/targets.md`. Clicks and page views explain a result;
  they are not one.
- Check the conversion action list in each ad platform. A page view or a secondary event set as primary
  inflates results. Mark each action primary or secondary and report only the primary as a result.
- Platform counts overlap. Two platforms can both claim the same booked call. Never add them together; report
  each platform's count with its name, and the de-duplicated total from the CRM when it exists.
- Keep view-through and click-through conversions in separate lines. View-through counts are often reported in
  "All conversions" only.
- Configured budget and observed spend are different facts. A budget set at campaign level may be overridden at
  ad group level. Report the spend you read, and note the setting that caused it.
- Reporting lags. Record the time of each pull. Hourly pacing is unavailable on some platforms; do not infer it
  from partial-day arithmetic.
- Censored values stay censored. Platforms report impression share below 10% or above 90% as bounds. Write
  "under 10%", never an exact 10%.

## Learning and volume

- Tracking comes first. A bidding strategy that optimises for a conversion needs that conversion recorded.
- Google's Smart Bidding best-practice guidance sets a minimum of 15 conversions with values in the last 30 days
  for Target ROAS. Third-party guides put a workable Target CPA at about 30 or more conversions a month. Use the
  platform's own figure when the account shows one.
- Meta's developer guidance advises about 50 optimisation events per ad set in seven days to exit learning. Some
  2026 blogs quote lower figures; treat those as unverified and check Meta's Business Help Center.
- Splitting a small budget across many ad sets multiplies the volume each one needs. Fewer, larger ad sets learn
  faster.
- Do not edit a campaign inside its learning window unless something unusual shows up. Each edit can restart
  learning.
- Set a target near the current average cost per result, not below it. A target far below history starves reach
  and the volume collapses (third-party guidance, 2025-2026).

## Creative testing

- Each variant tests one idea: the offer, the pain, or the proof. Two changes in one variant cannot be read.
- Do not call a winner from under seven days or under the conversion count in `knowledge/targets.md` (default 30
  per variant).
- Rotate creative before fatigue shows in frequency and falling click-through, not after results have fallen for
  a month.

## Claims and categories

- Every claim is one a dated source supports: a price with its period, a saving with its baseline, a rating with
  its source and date. A claim without a basis is removed, not softened.
- Guarantees, testimonials and competitor names need their own basis (terms, consent, trademark rules). Check
  the platform's ad policy before launch.
- Classify the ad before setting targeting. Platforms restrict targeting for housing, credit and employment ads
  (Meta's special ad categories; Google's limits on personalised housing ads). A campaign misclassified as general
  can be rejected or restricted after launch.

## Sources

- Google Ads Smart Bidding best practices (current PDF, read 2026-11-01): the 15-conversion Target ROAS minimum.
- Meta for Developers, learning-phase guidance (read 2026-11-01): about 50 events per ad set in seven days.
- Third-party bidding and learning-phase guides, 2026 (read 2026-11-01). Ranges only; not used as platform rules.
- Account reporting notes from a live paid bot (censored impression share, configured versus observed budget,
  view-through segmentation, lag), 2026. The rules are general; the figures are not.
