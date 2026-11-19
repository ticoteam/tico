# Goals and key results in Tico

How to set a quarter's goals, write key results that can be graded, and grade them. In Tico a goal is a record with an
owner; a key result is a KPI linked to the goal, with a target.

## The shape

- **Strategy** names three to five bets (`tico strategy show`). Each bet says what it rules out and what would prove it
  wrong.
- **A goal** is what the team will achieve this quarter toward one bet. Words, no number: "New customers reach value
  in their first week."
- **A key result** is the measurable proof: a KPI with a baseline, a target and a date.
- **Tasks** are the work. Tasks link to goals; they are not key results.

Three to five goals for the company in a quarter. Two to four key results for each. More than that and nothing is a
priority.

## Writing a key result

Every key result has all of these:

| Part | Rule |
|------|------|
| Metric | A KPI that exists in Tico or can be created now, with one definition |
| Baseline | The current reading, with its source and date. If there is none: "baseline: not measured", and the first key result is to measure it |
| Target | A number |
| Date | Usually the quarter's end |
| Owner | One teammate who can move it |
| Kind | **Committed** (the team expects to hit it fully) or **stretch** (hitting about 70% would be a good result). Committed unless marked stretch |

Tests:

- **Result, not activity.** "Ship the onboarding flow" is a task. "Share of new customers who finish setup in week one
  goes from 41% to 60%" is a key result.
- **Could it be graded by someone who was not there?** If grading needs an opinion, rewrite it.
- **Does it have a pair?** A quantity key result needs a quality or cost guardrail beside it, or it will be hit the
  wrong way.
- **Is the owner able to move it?** If the number depends entirely on another team, the key result belongs there.

Targets come in two kinds in Tico: improvement (baseline, target, deadline) and maintenance (hold between a minimum
and a maximum). Use maintenance for guardrails.

## Commands

- `tico strategy show`: the bets.
- `tico goal list --all`: current goals.
- `tico goal create ... --bet <id>`: a goal you own, tied to a bet.
- `tico kpi create ...`: the metric (name, definition, unit, direction, cadence, owner, source note).
- `tico kpi link <kpi> ...`: attach the KPI to the goal, with its target.
- `tico kpi log <kpi> <value> "note" --period-end <date> --evidence <link>`: a reading.
- `tico goal checkin <id> "<words>"`: the weekly check-in.
- `tico goal status`: how goals stand.
- `tico proposal create ...`: a change to someone else's goal, a target, or the strategy.

Run any of these with `--help` for the exact fields. You create and update goals and KPIs you own. For anyone else's,
and for any target changed mid-quarter, file a proposal; the owner confirms it. Check `tico proposal list` first so you
do not refile something pending or already rejected.

## The weekly check-in

For each goal you own, three short parts: what moved (with the reading), what is blocking, what happens next week. If
the number did not move, say so in those words.

## Grading

Grade each key result from its readings, not from how the quarter felt.

- **Improvement target:** grade = (latest reading − baseline) ÷ (target − baseline), held between 0 and 1.
- **Maintenance target:** 1 if every reading in the quarter stayed inside the range; otherwise the share of weeks
  inside it.
- **No reading:** "ungraded". Never 0, never a guess. Say what is missing.
- **Estimate or partial readings:** grade them, and label the grade "provisional".
- A goal's grade is the plain average of its key results; an ungraded key result is left out and named.

Reading the grades:

| Kind | Grade | Reads as |
|------|-------|----------|
| Committed | 1.0 | Expected |
| Committed | Below 1.0 | A miss. Write why and what changes |
| Stretch | Around 0.7 | Good |
| Stretch | 1.0 every quarter | The targets are too easy |

## Rules that keep goals useful

- **Not a performance review.** A grade measures the plan, not the teammate. Do not use key-result grades to judge a
  person or to decide a bot's verdict; a role has its own scorecard for that.
- **Not a task list.** If every key result is "launch X", the team has a plan and no goals.
- **Not everything.** Keeping the lights on does not need a goal. It needs a scorecard number with a range.
- **Targets do not move.** A mid-quarter change goes through a proposal and is shown on the page as changed.
- **A bet's health comes only from goals with KPIs behind them.** A goal with no KPI says nothing about its bet.

## Quarter end

1. Grade every key result. Publish the grades on the weekly page for the last week.
2. For each miss: one line on the cause, found in the records.
3. Carry over a goal only if it is still the best use of the quarter. Do not carry by default.
4. Draft next quarter's goals from the strategy and this quarter's misses. Ask for one OK on the set, the first time.
5. Update the scorecard: new input KPIs on, retired ones off, still 15 or fewer.
