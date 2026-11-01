# Grade a quarter

Triggered by a task that asks how last quarter went, and step 3 of `playbooks/quarterly-plan.md`.
Budget 25 minutes.

Inputs: `hub goal list --all --status green,yellow,red,done,dropped`, the final KPI reading for each key result, and
`knowledge/scorecard.md`.

Outcome: one grade per key result, with its evidence, and an honest total.

Done when: every key result has a grade, or "ungraded" with the gap named; the total is written with its reason; and the
scorecard holds the quarter with its sources.

---

## 1. List the key results

`hub goal list --all --status green,yellow,red,done,dropped` and `hub goal show <id>` for each goal that
was live in the quarter. If there were no written key results, say so and grade the goals' own
readings instead; do not invent key results after the fact.

## 2. Grade each on 0 to 1

Use `skills/grade-a-key-result-from-readings/SKILL.md` for each result.

- Read the final `hub kpi show <kpi id>` value and compare it with the target and the baseline.
- Grade as a fraction of the way from baseline to target, to one decimal. Binary results are 0 or 1.
- No reading means "ungraded", never 0 and never 1. Say what was missing.
- Mark each committed or aspirational. A committed result under 1.0 needs a sentence on why.

## 3. Read the total honestly

Average the graded key results only. An average of 0.6 to 0.7 on aspirational results is healthy; 1.0
everywhere means the targets were too easy, and under 0.4 means they were out of reach or the plan
was wrong. Say which, and give the one fact that supports it.

## 4. Write what changes next time

Two or three lines: a target set too high or low, a result nobody could measure, an objective that
lost its owner. These feed step 4 of the plan playbook.

## 5. Record

Append the graded quarter to `knowledge/scorecard.md` with its sources and dates. Never edit an older
grade; add a correction line with the date.
