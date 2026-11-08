---
name: write-a-test-with-a-stop-rule
description: Write one site test before it starts, with a hypothesis tied to the funnel, a primary metric set with marketing-ops where that seat exists, guardrails, a sample size, a stop rule and a draft variant, so no one reads a result against a rule written afterwards.
---
# Write a test with a stop rule

When to use: before any test is proposed or logged, and whenever a task asks what to test on a page.

Steps:
1. Name the step and the page from `knowledge/funnel.md`. A step with no measured baseline is not tested. Mark it "not
   measured" and ask for the export.
2. Write one change in one sentence. A change to two things is a redesign: say so, and do not call it a test of either part.
3. Write the hypothesis as: "If we <change>, then <primary metric> will rise by at least <size>, because <the funnel
   reading>." The reason quotes the export's number.
4. Take the primary metric and its events from the Marketing Operations Manager (`marketing-ops`) when that seat exists.
   Without that seat, name one primary metric yourself, say so in the proposal, and make it a conversion step. Name up to
   two guardrails: the next step, errors on the page, page load time, or refunds where they are counted inside the window.
5. Compute the sample per variant from the baseline rate, the smallest change worth shipping, and the confidence and power
   in `knowledge/conversion-method.md`. Write the expected days at current traffic. If that is more than eight weeks,
   narrow the change or move to a step with more people, and say why.
6. Write the stop rule: the test ends when the sample is reached and the end date has passed, whichever is later. Stop
   early only for a guardrail that breaks, and name which one.
7. Draft the variant: the exact copy, the layout note or the field change, saved to `knowledge/drafts/`.
8. Log the test in `knowledge/tests.md` as "proposed", with the date. It becomes "running" only when a person confirms it
   is live and split correctly.

Checks before you finish:
- The hypothesis names the funnel reading that prompted it.
- Someone who did not write the stop rule could check it.
- The variant draft has no claim (a price, a number, a rank, a guarantee) without a dated source.
- No test touches a sign-in, payment or personal-data page without the owner's written yes in the task.

Example (invented, for illustration): "Step: pricing to trial start, /pricing. If we move the trial button above the plan
table, then trial starts will rise by at least 10 percent, because 75 percent of visitors leave the pricing page without
starting a trial (export 2026-11-10). Sample: about 4,800 visitors per variant at the current baseline of 25 percent. Stop:
the sample is reached and 14 days have passed, whichever is later. Guardrails: errors on the page, page load time."
