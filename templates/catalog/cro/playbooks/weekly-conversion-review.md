# Weekly conversion review

Schedule: Wednesdays at 09:00 team time (routine `weekly-conversion-review`), after setup. Also run when a task asks.
Budget 45 minutes.

Inputs: `knowledge/funnel.md`, `knowledge/tests.md`, last week's review, the funnel export attached to the task (28 days,
by landing page and step), and the pages named in `knowledge/funnel.md`.

Outcome: one review in `reports/YYYY-MM-DD-conversion.md`: the headline, the funnel by step and landing page against the
28 days before, the biggest drop-off with its evidence, the status of each running test, one proposed test with its
draft and stop rule, and what could not be measured. Nothing on the site changes.

Done when every step has a result or a "not measured" line with its reason, the proposed test is written in the shape of
`playbooks/plan-a-test.md` with its draft saved, the review is published, and the task note carries the headline.

---

## 1. Read where things stand

    hub task show <id>

Read last week's review and `knowledge/tests.md`. For each test running, note its sample so far and whether its end date
has come. For each change last week proposed, note whether it was built, slipped or dropped.

## 2. Read the funnel

Follow `skills/find-the-biggest-drop-off/SKILL.md` on the export attached to the task. If no export is attached, the review
says "funnel not measured this week", checks the pages in `knowledge/funnel.md` as a visitor would, and asks for the
export in one question on the task. Never estimate a rate.

## 3. Check the steps as a visitor

For each step in `knowledge/funnel.md`, load the page as a stranger, and note the one action it asks for, the form
fields, any error text and the next step. Note a step that does not load, shows an error, or asks for more than its one
action. Nothing is submitted: no form is sent and no account is made.

## 4. Check the test log

For each test running, check its stop rule. Mark it "running, sample x of y", "ready to read", or "stopped by a
guardrail". Read a finished test with `skills/read-a-test-result-honestly/SKILL.md`, and record the decision in
`knowledge/tests.md`.

## 5. Propose one test

Choose the step with the biggest loss that has no test running. Write the test with `playbooks/plan-a-test.md`: the
hypothesis, the metric, the sample, the stop rule, and the draft variant saved to
`knowledge/drafts/<step-slug>-<date>.md`. If a test is already running on that step, propose nothing new there.

## 6. Write the review and publish it

Write `reports/YYYY-MM-DD-conversion.md` in the shape of `knowledge/examples/conversion-review.md`. Then:

    hub file publish reports/YYYY-MM-DD-conversion.md

Add each dated reading to `knowledge/tests.md`, rewrite `state.md`, and commit this repository.

## 7. Close

    hub task update <id> --status done --note "..."

The note holds the headline, the report path, and what you could not read. Finish the task even on a quiet week: an open
routine task gets absorbed into the next run.

## When a source fails

Name the failed export or page in the report and in the closing note, and what that leaves unknown. A blocked source is
never "no drop-off".
