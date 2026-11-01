# Read an experiment

Triggered by a task asking whether an A/B test or a staged rollout worked. Budget 30 minutes. The outcome
is a readout that says what the evidence supports and whether it is safe to decide.

Inputs: the experiment's plan (metric, variants, planned sample or end date, guardrails), the assignment and metric counts read through `hub db`, and the task.

Done when: the readout gives one verdict, shows the split check and the interval, and names what a person must decide.

---

## 1. Find the plan

    hub task show <id>

Find the metric chosen before launch, the planned sample size or duration, and the variants. If there was
no plan, say so in line one: the readout is exploratory and cannot declare a winner.

## 2. Check it ran cleanly

Run the split check in `skills/read-an-experiment-without-fooling-yourself/SKILL.md`: a mismatch that chance is unlikely to produce means assignment is broken, and the readout stops there. No change to the variant
mid-test; the planned sample or duration reached. If not reached, report the current numbers as
"not yet readable" and the date it will be. If anyone looked at the result early, say so.

## 3. Compute

For the planned metric: each variant's value, the difference, and its 95 percent interval. Guardrail
metrics (errors, cancellations, support tickets) beside it. Segment cuts only if planned; unplanned cuts
are labelled as leads to test, not results.

## 4. Write

`reports/YYYY-MM-DD-experiment-<name>.md`: the verdict in one line (better, worse, no detectable
difference, not yet readable), the numbers, the checks, and what a human would need to decide. Declaring
a winner is theirs. Publish, commit, finish the task.
