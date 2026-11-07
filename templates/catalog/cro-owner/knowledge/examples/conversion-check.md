A sample of excellent output for a fictional team. Every name, count and rate is invented.

```markdown
# Harbor Desk conversion check, week of 2 to 8 Nov 2026

Sample output for Harbor Desk, a fictional desk-booking software team. Nothing on the site was changed.

**Headline: pricing → checkout is the step losing the most people: 75 this week, a rate of 25.0% against 29.0% in its best
of the last 13 weeks. Fans carry most of it (19.6%, named). The annual-price test reached its planned sample and read
inconclusive, so nothing ships from it.**

## Tracking check (first, before any leak)

- All five steps have events on every day of the window. No later step sits above an earlier one.
- The checkout-start event counts page loads. A visitor who returns from the payment page is counted once, so the
  checkout count may be low by an unknown amount. See "could not measure".

## Funnel (2 to 8 Nov, daily export summed, pulled 9 Nov)

| Step | All | Creators | Fans |
|---|---|---|---|
| Landing page visits | 9,400 | 5,640 | 3,760 |
| Visit → sign-up complete | 2,820 (30.0%) | 2,030 (36.0%) | 790 (21.0%) |
| Sign-up → account confirmed | 1,410 (50.0%) | 1,120 (55.2%) | 290 (36.7%) |
| Visit → pricing page (signed in or not) | 1,880 (20.0%) | 1,320 (23.4%) | 560 (14.9%) |
| Pricing → checkout started | 470 (25.0%) | 360 (27.3%) | 110 (19.6%) |
| Checkout → purchase | 188 (40.0%) | 150 (41.7%) | 38 (34.5%) |

## Leaks, ranked by people lost (all visitors)

Best is the best weekly rate in the last 13 weeks, from the same export. Noise is 1.96 × sqrt(p(1 − p) ÷ n) for this week's
rate p and its count n.

| Rank | Step | Entering | This week | Best | People lost | Noise | Named |
|---|---|---|---|---|---|---|---|
| 1 | Pricing → checkout started | 1,880 | 25.0% | 29.0% | 75 | 2.0 pts | Yes, gap 4.0 pts |
| 2 | Visit → sign-up complete | 9,400 | 30.0% | 30.6% | 56 | 0.9 pts | No, within noise |
| 3 | Visit → pricing page | 9,400 | 20.0% | 20.5% | 47 | 0.8 pts | No, within noise |
| 4 | Sign-up → account confirmed | 2,820 | 50.0% | 51.0% | 28 | 1.8 pts | No, within noise |
| 5 | Checkout → purchase | 470 | 40.0% | 42.0% | 9 | 4.4 pts | No, within noise |

Segment leaks, on their own lines and not added to the 75:
- Fans at pricing → checkout: 110 of 560 (19.6%) against 24.0% in its own best. 25 people. Gap 4.4 points against noise
  3.3 points. Named.
- Creators at the same step: 360 of 1,320 (27.3%) against 29.0%. 22 people. Within noise.

## Open tests

- Annual price first on the pricing page (Marketing task 412, label `channel:conversion`, brief in
  `knowledge/experiments/annual-price-first.md`). It reached its planned sample on 8 Nov. Read below.

## Read-out: annual price first (28 Sep to 8 Nov, six weeks)

- Planned: 3,887 per arm, from a baseline of 25.0% and a smallest change worth shipping of 2.8 points.
- Counts: control 1,001 of 4,004 (25.0%); variant 1,062 of 4,010 (26.5%).
- Difference: +1.5 points. 95% interval: −0.4 to +3.4 points.
- Sample ratio check: 4,004 and 4,010 against an even split of 4,007 each; chi-square p about 0.95. No mismatch.
- Guardrail, support tickets tagged "price" in the first 14 days: 31 in control, 29 in variant. Not breached.
- Call: inconclusive. The interval includes zero. Not shipped.
- A +1.5 point change needs about 13,300 per arm. At 1,340 eligible entrants a week that is 20 weeks, and the limit is six.
  The page is too small for a test that size. A larger change is the next proposal, not a longer test.

## Proposal (one this week)

P-07, pricing page on a phone. Evidence: Fans convert 19.6% at pricing → checkout against 27.3% for Creators, and the
export has no device split. Change: none yet. The page owner pulls a device split for the pricing page by segment and
sends a 390 px screenshot of the plan table. Check: the split is read in the report of 16 Nov. A layout change is drafted
only if the split supports it.

## Could not measure

- Device split: not in the export, so the Fans gap is not split by phone and desktop.
- Returning visitors from the payment page: they may be counted once fewer than they happened.
- Search landing pages: no search export was attached this week.

## Sources

- Web analytics export, daily counts, 2 to 8 Nov 2026, pulled 9 Nov.
- Previous 13 weeks of weekly counts, same export definition, pulled 9 Nov.
- Test brief and daily exposure counts for annual-price-first, 28 Sep to 8 Nov.
- Support ticket tags, export pulled 9 Nov.
- `knowledge/conversion-method.md`, read 9 Nov.
```
