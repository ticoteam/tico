# Quarterly plan and OKR draft

Schedule: the first of each month at 09:00 team time (routine `quarterly-plan`), after setup. Also run by hand. Budget 45 minutes.

Inputs: `hub strategy show`, `hub goal list --all`, the KPI readings for the live goals, the weekly updates and imported meetings since the quarter started, `knowledge/rhythm.md`, `knowledge/scorecard.md`, and last quarter's report in `reports/`.

Outcome: one report for the owner. In the last month of a quarter, last quarter graded and next quarter drafted; in the other months, a mid-quarter check-in. Nothing in Tico changes.

Done when: the report is written and published, every source that could not be read is named in it, and the task is closed with the headline and the report path.

---

## 1. Decide which report this is

Read `knowledge/rhythm.md` for the quarter dates. In the last month of a quarter, write the plan.
Otherwise write the check-in (step 6). If the task names one, do that.

## 2. Read the record

    hub strategy show
    hub goal list --all
    hub goal show <id>
    hub kpi show <kpi id>
    hub update list --kind weekly --limit 12
    hub meeting search --since <quarter start>

The strategy first: the published version's active bets are what the quarter serves, and each goal's `bet_id`
says which. `health_by_bet` shows which bets have a KPI-backed goal and how it is going. Nothing published: plan
against the setup answer in `state.md`, marked unconfirmed. Then `knowledge/scorecard.md` and last quarter's
report. A source you cannot read is named in the closing line.

## 3. Grade last quarter

Follow `playbooks/grade-a-quarter.md`. Write the result into the report and `knowledge/scorecard.md`.

## 4. Draft next quarter

Use `skills/draft-an-objective-from-a-bet/SKILL.md` for each objective and `skills/write-a-measurable-key-result/SKILL.md`
for each key result.

1. Pick the one focus: the single outcome that would make the quarter a success, from the strategy's active
   bets (`hub strategy show`) and the goals still live. Name the bet it serves.
2. Draft three to five objectives, each an outcome in plain words with about three key results, and each under
   the bet it serves. An objective that serves no active bet is marked so and needs a sentence on why it is in
   the plan; one that cuts against a bet's "rules out" is a question for the owner, not an objective. A bet with
   no KPI-backed goal (gray health) gets a key result that could measure it.
3. Each key result: a metric, its baseline (with source and date), a target, a date, a proposed owner
   marked "to confirm", and committed or aspirational.
4. Carry over what did not finish only if it still serves an active bet; say why.
5. List what you left out and why. A short list of what is ruled out is part of the plan.

## 5. Check it against the cap

More than the cap in `knowledge/rhythm.md`: cut to the cap and list the rest under "Not this quarter".
Any key result phrased as an activity is rewritten or moved to "How we might get there".

## 6. The mid-quarter check-in

One line per key result: reading, target, confidence from 0 to 1 that it lands, and the one fact
behind the confidence. Then three lines: what is off track, what needs a decision, what to drop.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-quarterly-plan.md` in the shape of `knowledge/examples/quarterly-plan.md`,
then `hub file publish reports/YYYY-MM-DD-quarterly-plan.md`. Commit, then `hub task update <id>
--status done --note`: the headline, the path, what you could not read. Always finish the task.
