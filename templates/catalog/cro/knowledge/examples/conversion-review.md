A sample of excellent output for a fictional team. Every name, URL and number is invented.

```markdown
# Northwind conversion review, Wed 2026-11-04

Sample output for Northwind, a fictional tutoring marketplace. Nothing on the website was changed.

**Headline: the biggest loss is home to pricing, where 5,070 of 6,310 visitors never reached pricing in the last 28 days
(80 percent). One test is proposed on it. It is not running yet.**

## Funnel (export "Northwind analytics, 28 days to 2026-11-03, all traffic", pulled 2026-11-04)

| Step | Reached | Completed | Lost | Rate | Previous 28 days |
|---|---|---|---|---|---|
| Home to pricing | 6,310 | 1,240 | 5,070 | 20% | 19% |
| Pricing to trial start | 1,240 | 310 | 930 | 25% | 24% |
| Trial start to first booking (first 24 hours only) | 310 | 118 | 192 | 38% | 41% |

- Biggest loss in people: home to pricing, 5,070 lost. Pricing to trial start is next, at 930 lost, and already has a
  test running (below).
- Against the 28 days before: 19 percent then, 20 now. A step this size moves about a point from chance, so this is
  "about the same".
- Not measured: trial to first booking beyond 24 hours. The export stops there.

## Tests

- `pricing-trial-button`: running. Sample 2,310 of 4,800 per variant. Ready to read on 2026-11-19 or when the sample
  is reached, whichever is later.
- `booking-address-field`: ended 2026-10-30. No clear difference (booking 38 percent control, 39 percent variant, the
  interval spans zero). Logged as no clear difference.

## One test proposed

- Step: home to pricing, all traffic.
- Hypothesis: if the home page shows the plans and their prices above the fold, then the share of visitors who reach
  pricing will rise by at least 20 percent, from 20 to about 24 percent, because 5,070 of 6,310 visitors leave the home
  page without reaching pricing.
- Primary metric: the share who reach pricing, set here by CRO since Northwind has no marketing-ops seat yet. Guardrails:
  errors on the page, page load time.
- Sample: about 1,700 per variant, about 15 days at 225 visitors a day, for a 20 percent relative lift. At the default 10
  percent the sample would be about 6,700 per variant, about 59 days, more than eight weeks, so the smallest change worth
  shipping is set at 20 percent.
- Stop rule: the sample is reached and 14 days have passed, whichever is later. No early stop for a win.
- Draft: `knowledge/drafts/home-to-pricing-2026-11-04.md`. Not built. A person builds and starts it.

## Could not read

- The export has no returning-visitor split, so the source split is the only segment checked.
- No CRM export this week. Trials are not checked against paid customers.

## Sources

- Funnel export, 28 days to 2026-11-03, pulled 2026-11-04.
- Pages /, /pricing and /start checked as a visitor, 2026-11-04.
- `knowledge/tests.md`, as of 2026-11-04.
```
