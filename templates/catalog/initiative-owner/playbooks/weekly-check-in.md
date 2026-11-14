# Weekly initiative check-in

Schedule: Fridays at 15:00 team time (routine `weekly-initiative-check-in`), after setup. Also run when a task asks.
Budget 30 minutes. Nothing on the Strategy page, in Tico's goals or in any KPI changes in this run.

Inputs: `knowledge/plan.md`, the last four entries of `knowledge/call-log.md`, last week's check-in, `tico strategy show`
for this seat's bet, the goals under that bet in `goals_by_bet`, and the readings of their KPIs.

Outcome: one check-in in the shape of `knowledge/examples/bet-check-in.md`: the call first, then the bet by its
statement, its wrong-if and check-by date, progress, the next step, the blockers, and what could not be read.

Done when the call is written with its evidence, the check-in is published, `knowledge/call-log.md` has this week's
entry, and the task note carries the call and the report path.

---

## 1. Read the bet as published

    tico task show <id>
    tico strategy show

Find this seat's bet by the id in `knowledge/plan.md`. If the bet is not in the published strategy, or its status is not
`active`, say so in one line on the task and stop: the check-in reports the bet's status, it does not guess one. Note
its statement, what it rules out, its wrong-if text and check-by date, and any change since last week.

## 2. Check the wrong-if first

- **A KPI threshold.** `tico kpi show <kpi id>` for the last readings. Compare each one with the line. Only a reading past
  the line counts, with its period.
- **A market entity.** `tico market show <id>` and `tico market delta` for what changed, with its source.
- **A sentence.** Is the check-by date past? If not, the line reads "not yet checkable". If it is, say what the record shows.
- `tico proposal list`: a pending `strategy_draft` about this bet means the Strategy Owner already raised it. Say so and
  link it; do not raise a second one.

## 3. Read progress against the plan

For each milestone in `knowledge/plan.md` due by this check-in: reached, late or open, with its date and the evidence.
Read each goal under the bet (`tico goal show <id>`) with its colour and readings. Name what moved since last week, and
cite it. A task closed without a reading is not progress.

## 4. Make the call

Choose one: on track, watch or looks wrong, by the rules in `knowledge/bet-practice.md`. Quote the evidence with its date.
Write the reason in two sentences at most.

## 5. Name the next step and the blockers

Follow `skills/name-the-next-step-and-the-blocker/SKILL.md`. One next step, with one owner and one date. Each blocker
names who can move it, how many days it has been stuck, and whether a task already asks for it. If no task asks, ask the
owner in one question on this task.

## 6. Looks wrong: route it

Do not write a strategy proposal, and do not file a task for the owner. If the wrong-if has tripped, the server has already
filed the trip task to the Strategy Owner, which puts the decision to the owner. Only when the evidence points against the
bet and nothing has formally tripped, tell the Strategy Owner, which owns bet proposals:

    tico message send strategy-owner "Bet <id>: looks wrong. <evidence in one line, with its date>" --needs-action

The Strategy Owner puts the decision to the owner. Keep, rewrite and retire are the owner's to choose.

## 7. Write, publish, close

Write `reports/YYYY-MM-DD-initiative.md` in the shape of `knowledge/examples/bet-check-in.md`, then:

    tico file publish reports/YYYY-MM-DD-initiative.md

Add this week's entry to `knowledge/call-log.md` and update `knowledge/plan.md`. Note progress on the task and commit this
repository. Finish the task:

    tico task done <id> -m "<the call, the bet's id, the report path, what you could not read>" --version N

## When a source fails

Name it in the report and in the note: which reading, market page, goal or meeting failed, and what that leaves unknown.
A blocked source is never "no change", and a missing reading is never "on track".
