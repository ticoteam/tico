# Analyse a price change

Triggered by a task where a human is weighing a price or packaging change ("raise Pro by 15%", "move
SMS reminders to Pro"). Budget 60 minutes. The outcome is an impact note the owner can decide from.
Nothing is changed and no customer is told.

Inputs: the task's change in its own words; billing or CRM exports (read only) with each customer's plan, segment, current bill and renewal date; `knowledge/our-prices.md`; any churn or willingness-to-pay study on file.

Done when: `reports/YYYY-MM-DD-impact-<change>.md` is published, every number traces to a named export, the break-even churn is stated with its formula, every option has its cost, and the unknowns are listed. The shape is in `skills/size-a-price-change/SKILL.md`.

---

## 1. State the change exactly

    hub task show <id>

Write it as: which plans, which prices or limits, from when, for new customers only or everyone. If any
of these is open, lay out the variants side by side instead of guessing.

## 2. Find who it touches

From billing or CRM exports (read only): customers per plan and segment affected, their current and new
bill, and the ten accounts with the biggest increase. Count customers on annual terms whose renewal falls
in the next six months.

## 3. Estimate the effect

Revenue change per segment if nobody leaves, and the break-even churn: how much revenue would have to leave
for the change to lose money. For a percentage rise p on the affected base, break-even revenue churn is
p / (1 + p), on revenue only and before costs: a 15% rise breaks even at about 13% of revenue at the new prices
leaving. Convert that to customers only with a stated billing assumption, since bills differ. Say plainly
that churn response is unknown unless a study measured it.

## 4. Lay out the options

Grandfathering (none, for a period, forever), notice periods, and exceptions (large accounts, non-profits),
each with its cost. Note contract terms that limit a change: ask `legal-review` to check the wording. You do
not interpret contracts.

## 5. Write

`reports/YYYY-MM-DD-impact-<change>.md`: a one-line summary of the facts with no recommendation unless the
task asks for one, the tables, the options, the unknowns, and what would reduce them. `hub file publish`,
commit, finish the task. The decision and every customer message are the owner's.
