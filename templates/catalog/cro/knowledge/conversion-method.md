# Conversion method

The standing method behind `playbooks/weekly-conversion-review.md` and `playbooks/plan-a-test.md`. `funnel.md` holds the
steps and `tests.md` the test log. This file holds how to count a step, how to size a test and how to read its result,
so each run does not re-derive it. The team's own thresholds are written here when setup sets them.

## Count a conversion once, where it happens

- A conversion is a completed step the team has named in `funnel.md`: a signup completed, a trial started, a purchase
  paid. A page view, a button press or an opened form is a step, never a result.
- Count the step where the record is made (the account exists, the order is paid) when both are available. Name the
  one used.
- Say whether a count is visitors or sessions. One person can open two sessions, so the two are never added together.

## Read the funnel by people lost

- A step's loss is the people who reached it minus the people who completed it. The biggest drop-off is the step that
  loses the most people in number, not the lowest rate. Report both numbers.
- Segment before you conclude. Split by device, source and landing page. A loss that sits almost all on one device
  is a finding about that device and its page.
- A small base is a warning. Under about 100 people reaching a step in the window, a rate can move by several points
  from chance alone. Say so, and do not call a small move a change.

## Size the test before it starts

- One primary metric: one conversion step. Secondary metrics explain a result; they never decide it.
- Inputs: the baseline rate from the export, the smallest change worth shipping (the
  default is a relative change of 10 percent), a two-sided confidence level (default 95 percent) and power (default
  80 percent). Feed them to a standard two-proportion sample size calculator and write the visitors per variant in
  `tests.md`.
- Those defaults are common practice for online experiments. They are not a platform rule. Check the team's own
  statistics tool before quoting a p-value.
- Expected days are visitors per variant divided by the step's current daily visitors, doubled for two variants. If
  that is more than eight weeks, the test is too small a change for the traffic. Propose a bigger change or a step
  with more people.
- Guardrails are named in advance: the next step's completion, errors on the page, page load time, and refunds or
  cancellations where they are counted inside the window.

## Read the result honestly

- Call a result only when the sample is reached and the end date has passed, whichever is later. Before then the
  result is "running", and a difference read early is not reported as one.
- Run each test through at least one full week, so every weekday is in it.
- Check the guardrails. A signup lift that lowers paid conversions, adds errors or slows the page is not a win.
- Check the weeks. A lift that sits in one week, or one segment, may be novelty or a change in who visited. Say so.
- Two tests on the same page at once interfere. Run them in sequence, or say the result is shared.
- Write the decision in words: ship, revert, run again with a bigger change, or stopped by a guardrail. Log it in
  `tests.md` with the date, including the tests that showed no clear difference.

## Sources

- 2026-11-07: method written at card creation from the team's conversion practice and standard experiment statistics.
  No platform figure or external benchmark was read for this file. Any benchmark added later needs its own dated source
  and the traffic type it was measured on.
