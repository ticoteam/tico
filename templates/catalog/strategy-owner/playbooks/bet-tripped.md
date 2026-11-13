# Bet tripped

A task titled "Bet N: may be wrong". The server files it when an active bet's wrong-if trips: a KPI crossed its
threshold, a market entity the bet cites changed, or a sentence bet's check-by date passed. It files one per bet per
published version, at every level, Passive included: the server detected the trip, so your only job is the words.
The outcome is one proposal for the owner that says what happened and what you would do. Budget 15 minutes.

Inputs: the trip task (the bet id, the wrong-if that tripped and the evidence), `tico strategy show`, and the reading or market item it cites.

**Never edit goals**, and never the strategy: the proposal is the owner's to decide. **Never invent a
number**: the evidence is the reading, the market change or the date in the task, cited.

---

## 1. Read the bet and the evidence

- The task: the bet's id, which wrong-if tripped, and the evidence (the reading and its period, the market change,
  or the date).
- `tico strategy show`: the bet's statement, why, rules out, wrong-if and cites, and its goals under `goals_by_bet`
  with `health_by_bet`.
- A KPI trip: `tico kpi show <kpi id>`, the last few readings with their evidence. Compare each reading with the line yourself:
  only a reading past the line is part of the trip (9 is not below 8). One reading past the line after a run of good ones may
  be noise or a partial period; say so.
- A market trip: `tico market show <id>` for the cited entity, and `tico market delta` for what changed and its source.
- A sentence bet past its check-by date: the goals that serve it and anything in the market or the readings that
  speaks to the sentence. Often there is nothing, and that is the finding.
- `tico proposal list`: a pending `strategy_draft` about this bet means it was already said; finish the task with
  one line pointing at it.

## 2. Write one proposal

One `strategy_draft` proposal that does one of three things to this bet:

- **retire** it: `{"bets": [{"id": "<bet id>", "status": "retired"}]}`. Its goals stay;
  the owner's realignment ask on publish lists them;
- **rewrite** it: a new statement, why, rules out or wrong-if, with the same `id`;
- **keep** it with a new wrong-if: the same statement and a `wrong_if` the evidence has not already crossed, with a
  new `check_by`.

    tico proposal create --kind strategy_draft --payload-file bet.json --reason "<the evidence in one line, then your pick and why>"

The reason is a few lines at most: what tripped, with its date and source; which of the three you recommend; and
why, in one sentence. Pick one; do not leave it as a menu. A trip proposal does not count toward the five a day: a
wrong bet should not wait until tomorrow.

If the evidence is thin or the bet is one the owner may want to stress-test before deciding, say in the reason that
the Scenarios or Pre-mortem lens (`playbooks/run-a-lens.md`) on this bet would help; do not run it as part of this
task.

## 3. Finish

A `proposal create` that returns its id has filed the proposal: finish then. Do not list the proposals again to confirm it,
and do not reopen the task.

1. `tico task done <id> -m "<the bet, what tripped, your pick>" --version N`.
2. Add one line to `memory/decisions.md` (the bet, the trip, the proposal), and commit this repository. Nothing else: no goals proposed,
   no other bets touched.
3. Reply in 2 to 3 short lines, in this order: the bet by its statement; what tripped, quoting only the readings past the line,
   with their dates; your pick; and that the owner decides. No bullets. Do not say what you closed, recorded or committed; nothing else.

**Done when:** one pending `strategy_draft` proposal for this bet exists (or one was already pending, and this task says so), the task is done with its note, `memory/decisions.md` has the line, and the reply is written.
