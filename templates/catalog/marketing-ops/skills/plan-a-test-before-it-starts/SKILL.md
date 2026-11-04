---
name: plan-a-test-before-it-starts
description: Decide whether a page or form test is worth running, size it from the events the page can produce, and write the stop rules before launch so the result cannot be argued into a win.
---
# Plan a test before it starts

When to use: before any landing-page, form or funnel test is launched, and before anyone calls a test a win,
a loss or inconclusive.

Steps:
1. Name the decision the test feeds. If every plausible result leads to the same change, do not test. Ship it and
   measure after launch.
2. Check whether it is a fix. A broken form, a missing field or a claim that breaks a rule is repaired.
3. Write one primary metric, one counter-metric (the thing that must not get worse, such as refunds or bounce on
   the next step) and the tie-break, if any. Count events, not clicks, for the primary.
4. Take the baseline rate for the primary event over the last four weeks. Choose the smallest relative lift worth
   acting on. Estimate the events needed per arm (a standard two-proportion calculation, or the experimentation
   tool's calculator). If the page cannot produce that count in about four weeks, stop: pick a page with more
   traffic, a bigger change, or a qualitative method.
5. Write the end date and the ship rules in the test record before launch: win (above the bar, guardrails clean),
   loss (guardrail broken or result below the bar), inconclusive (keep the control).
6. Launch a small share first and check the split and the events for two days. Stop only for a broken arm.
7. Read the result only at the end date. Check the split ratio before the result. Do not add a segment, metric or
   second change to the test while it runs.

Checks before you finish:
- The primary metric is one named event, and the test record says where it comes from.
- The sample plan is in the record, with the baseline and the lift it can detect.
- Each outcome has its action written down before the test starts.
- A result that did not reach its sample is written as inconclusive, not as a trend.
- The record gets its end date. Anything past thirty days without one goes on the retirement list.

Example (invented, for illustration): "Booked-call button on the pricing page. Baseline 2.1% over four weeks, about
600 visits a week. The smallest lift worth acting on is 25%, which needs roughly 15,000 visits per arm. At 600 visits a
week in total, two arms of 15,000 need 30,000 / 600 = 50 weeks, so it is not run. Instead: the form's broken phone field is fixed (a fix, not a
test), and the change is measured for four weeks against the prior month."
