# Plan a test

Used by the weekly review (step 5) and by any task that asks for a test. Budget 20 minutes. Nothing here starts a test on
the live site: a person builds and starts it.

Outcome: one entry in `knowledge/tests.md` with status "proposed", and one draft in `knowledge/drafts/`.

---

## 1. Name the step and the one change

Name the step in `knowledge/funnel.md`, the page, and the single change in one sentence. If the change touches two things,
split it into two tests, or mark it a redesign and say it will not be read as a test of either part.

## 2. Write the hypothesis

"If we <change> on <page>, then <primary metric> for visitors at this step will move by at least <size>, because
<what the funnel showed>." The reason quotes the number from the export that prompted it.

## 3. Set the primary metric and the guardrails

The primary metric and its events are set with the Marketing Operations Manager (`marketing-ops`) when that seat exists.
Without that seat, pick one primary metric yourself, a conversion step from `knowledge/funnel.md`, and say so in the
proposal. Guardrails: the next step's completion, errors on the page, page load time, and refunds or cancellations where
they are counted inside the window. Name each one.

## 4. Work out the sample

Use the baseline rate from the export, the smallest change worth shipping (default a relative change of 10 percent),
and the confidence and power in `knowledge/conversion-method.md`. Feed them to a standard two-proportion sample size
calculator. Write the visitors per variant, and the expected days at the step's current daily traffic. If that is more
than eight weeks, say so and propose a bigger change or a different step.

## 5. Write the stop rule

The test ends when the sample is reached and the end date has passed, whichever is later. No reading of the result before
then. Stop early for a loss only when a guardrail breaks, and name the guardrail. Write this in the log before the test
starts, not after.

## 6. Draft the variant

Write the exact new copy, layout note or field change in `knowledge/drafts/<step-slug>-<date>.md`, with the page, the
control, the variant, the person who builds it, and the check that confirms it is live and split correctly. Run every
claim in the copy through `hub brand check -` and the claims list before it is handed over.

## 7. Log it

Add the test to `knowledge/tests.md` with status "proposed" and today's date. It becomes "running" only when a person
confirms it is live and split correctly.
