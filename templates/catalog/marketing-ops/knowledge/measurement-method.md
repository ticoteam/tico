# Measurement method

The standing rules behind the weekly data check, the handoff measure and any test on a page or form. The
team's own convention lives in `knowledge/tracking.md`; this file says why each rule is there.

## Conversions

- One number, one definition. A conversion is one named event (a booked call, a trial start, a purchase).
  A count that adds two events together is two numbers. Report the event the goal names, and report any
  other as a diagnostic.
- A page view or a click is a diagnostic, not a conversion. Ad platforms can count them as conversions
  when the wrong action is set as primary. Check the conversion action list in each platform every month.
- People are counted once. Platform-attributed counts overlap, so they cannot be added together. Dedupe on
  the CRM record or the event identifier, and say so when you could not.
- Keep view-through and click-through conversions in separate lines of every report.
- Every number carries its attribution window. A lead that arrives forty days after an ad belongs to the
  window it entered, not to the campaign that spent the money.

## Tracking health

- A tag that has fired zero times in seven days is broken until shown otherwise. Zero is not a result.
- A form source field that is blank means "unknown", never "direct".
- Sample-ratio check: when traffic is split between two arms, the counts must match the split within chance.
  A chi-square p-value below 0.001 means the split is broken (PostHog's warning level; Microsoft's
  experimentation platform stops at 0.0005). Fix the split before reading any result.

## Tests

- Fix, do not test. A broken form, a missing source field or a claim that breaks a rule is repaired. It is
  not an experiment.
- Plan for flat results. A Microsoft study of its own experiments found about a third of ideas improved the
  target metric, a third were flat and a third made it worse (Kohavi and colleagues, ThinkWeek paper, 2009).
  A 2024 analysis of unbiased effect estimates put the median true effect of proposed changes near zero
  (Georgiev, 2024). Write down what a flat result will lead to before the test starts.
- Size the test first. The baseline rate and the smallest lift worth acting on give the events needed per
  arm. If the page cannot reach that count in about four weeks, do not test it. Choose a page with more
  traffic, a bigger change, or a qualitative method.
- Fix the primary metric, one counter-metric and the end date before launch. Write the tie-break too.
- Do not stop because a result looks decided. Stop only for a broken arm. Do not extend a fixed-horizon test
  to reach significance.
- Retire anything still running after thirty days without an end date.

## Decision table

| Situation | Action |
| --- | --- |
| A tag fired zero times in seven days | Broken. Fix it. Report the figure as unknown. |
| Split counts far from the split | Stop reading. Fix the split. Restart the test. |
| Two platforms report different conversions | Report the one agreed definition. Flag the other. |
| Test ends short of its sample | Inconclusive. Keep the control. Label any switch a preference. |

## Sources

- Kohavi, R. and colleagues, "Online controlled experiments" (Microsoft ThinkWeek paper, fiscal year 2009). Cited through a 2026 research brief; check the original.
- Georgiev, G., analysis of unbiased effect estimates, 31 August 2024. Cited through a 2026 research brief.
- PostHog, sample-ratio mismatch warning and experiment sample-size notes, 2025-2026. Cited through a 2026 research brief.
- Oldroyd, J. and InsideSales.com, speed-to-lead study, 2007 (used in `skills/measure-lead-handoff-delay`). Old, phone-contact data: read it as direction, not as a target.
- Read 2026-11-01.
