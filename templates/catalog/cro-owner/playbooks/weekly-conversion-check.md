# Weekly conversion check

Schedule: Mondays at 08:30 team time (routine `weekly-conversion-check`), after setup. Also run when a task asks.
Budget 45 minutes.

Inputs: `knowledge/funnel.md`, `knowledge/pages.md`, `knowledge/leaks.md`, every file in `knowledge/experiments/`
that is still running, last week's report, and the funnel export a person attached or connected.

Outcome: one report in the shape of `knowledge/examples/conversion-check.md`: the headline, the tracking check, the funnel
with each rate, its count and window, the leaks ranked by people lost with the named ones marked, each open test against
its plan, the read-out of any test that reached its planned end, one page change proposed, and what could not be measured.

Done when every step has a rate with its count and window, or "not measured" with its reason, the leaks are ranked, each
open test has a status, the report is published, one proposal is in `knowledge/proposals/`, and the task note carries the
headline.

---

## 1. Read where things stand

    hub task show <id>

Read last week's report, `knowledge/funnel.md`, `knowledge/leaks.md`, `knowledge/pages.md` and
`knowledge/conversion-method.md`. For each change last week proposed, note whether it was applied, is still pending or
was dropped.

## 2. Pull the counts

Use the export on the task if one is attached. Otherwise read the counts with `hub db list` and `hub db <name> "<select>"`,
or with `hub sql "<select>"` for the team's own tables. Write each pull into `knowledge/funnel.md` with its window, its
segment and the date it was pulled. If no source has a count, that step reads "not measured", and the report's first
line says so.

## 3. Check tracking before any rate is named

For each step, ask: did events fire on every day of the window? Is a later step above an earlier one? Did a count drop to
zero on a day with traffic? Did the event change on a release date? Any yes is a tracking question. Say so in the report
before you name a leak. Follow `skills/find-the-leak-in-a-funnel/SKILL.md`.

## 4. Compute the rates and the leaks

Compute each rate for all visitors and for each segment, then the leaks as `skills/find-the-leak-in-a-funnel/SKILL.md`
describes. Rank by people lost. Name only the leaks that pass the noise test; list the rest as within noise.

## 5. Check each open test against its plan

For each running test in `knowledge/experiments/`, show its planned n per arm, the count so far in each arm, its planned end
and the sample ratio check. Do not show the primary metric's rate or difference before the planned end. Guardrails are
shown in full. A guardrail breach goes to the page owner as a question in the same run.

## 6. Read any test that reached its planned end

Follow `skills/read-a-test-without-peeking/SKILL.md`. Record the read-out in the test's file as won, lost or inconclusive,
and comment the outcome on the idea task, with one line on what the test showed:

    hub task comment <id> "<Succeeded|Failed|Inconclusive>: <what the test showed>"

The Growth Team Manager closes it out.

## 7. Draft one proposal

Rank the named leaks by people lost, then by how little a person must do to act on them. Draft one proposal in
`knowledge/proposals/<slug>.md`: the page, the evidence with its counts and window, the change written out exactly, the
check, and the date it will be read. A test is proposed through `playbooks/run-an-experiment.md`, not through this step.

## 8. Write the report and publish it

Write `reports/YYYY-MM-DD-conversion.md` in the shape of the example. Then:

    hub file publish reports/YYYY-MM-DD-conversion.md

Log the pulls in `knowledge/funnel.md` and the leaks in `knowledge/leaks.md`. Record the headline rate as a KPI reading
with `hub kpi log` if `hub kpi list` shows one you own. Rewrite `state.md`, and commit this repository.

## 9. Close

    hub task done <id> -m "..." --version N

The note holds the headline, the report path, and what could not be measured. Finish the task even on a quiet week: an
open routine task gets absorbed into the next run.

## When a source fails

Name it in the report and in the note: which export or database failed, and what that leaves unknown. A missing count is
"not measured", never zero.
