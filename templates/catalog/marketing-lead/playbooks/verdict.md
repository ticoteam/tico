# Verdict

When: a test's end date has passed (`tests_due` in `hub growth week`), or a test's stop rule was hit. The verdict is the
only place a test is judged. Read the rule in the Idea page ("How verdicts are decided"); this is the same rule.

## The rule

Check in this order. The first rule that applies gives the verdict.

1. **Lost** if the stop rule was hit at any point in the window.
2. **Lost** if the judged KPI did not beat its baseline in the predicted direction. The baseline is the judged KPI's last
   reading before `test_from`.
3. **Inconclusive** if the judged KPI has fewer events than `min_events` in the window. Say how many and how many are needed.
4. **Won** if the change reached at least the win share of the prediction. Win share is
   (window value − baseline) ÷ (predicted change). The win share is `verdict_win_pct` in `hub growth settings`, 60 by default.
5. **Inconclusive** for anything in between.

Example: the prediction was "bookings from price requests up 8 a week". The baseline is 14 a week. The window averages 19 a
week. Change is 5, which is 5 ÷ 8 = 62%: Won.

`paused` is not a verdict: the owner pauses a test for now. It sets no verdict date and leaves the task open. `dropped` is
the owner stopping a test for a reason outside the rule. It sets the verdict date and closes the task, and it says nothing
about the bet, so use `--bet-effect none`.

## Steps

1. Read the idea: `hub growth idea show <task id>`. Note the prediction, the window, the stop rule, `min_events`,
   `judged_kpi_id` and the audience.
2. Read the judged KPI across the window and the baseline: `hub kpi show <judged_kpi_id>`. A reading is a fact. Use the
   reading that covers each period; a partial reading is not a value to judge.
3. Count events in the window, and check the stop rule against every reading in it.
4. Apply the rule above and write one line of why it is that verdict.
5. Write it:

       hub growth idea verdict <task id> --status won --result "<the numbers, the window, the source>" \
         --learning "<one sentence on what we now believe about the customer>" \
         --bet-effect supports --confidence high

   `--bet-effect` is `supports` for Won, `weakens` for Lost and `none` for Inconclusive, Dropped or an idea with no bet.
   `--confidence` is `low`, `medium` or `high`. Brand-track ideas always carry it. For performance tests, give it when you have one.

6. Cohort numbers that the result rests on (booked, cancelled, retained) come from the source of record, with the view or
   link as the source in `--result`. Do not put a number in the result that you did not read.

## Learning

The learning is a claim about customers, not about the test. "Price shoppers churn in two weeks" is a learning.
"The price test lost" is not. Put the learning in `memory/learnings.md` as well, dated.

## Next

If the learning suggests a next test, file it as a follow-up: `hub growth idea create ... --follow-up-of <task id>`
(see `playbooks/propose-ideas.md`). Do not start it here; the weekly meeting picks it.

## Quality

- The owner may change a verdict within three days; say what would change your mind in the result line if the evidence is close.
- If the rule and the evidence disagree, the verdict is Inconclusive and the line says why.
- Never write a verdict that the rule does not give, even when the owner would like one.
