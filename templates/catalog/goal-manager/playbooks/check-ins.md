# Check-ins

A check-in is the goal owner's own words about what is going on. Readings are facts; a check-in is
interpretation. Keep them apart: never put an explanation in a reading's note, never make up an answer.

Inputs: `hub goal show <id>` (the owner, the KPIs, the reason line and recent check-ins), then the owner's answer when it arrives.

Only above Passive (`level` in `hub goal strategy-bot`); at Passive you ask nobody. When to ask: a goal turned yellow or red in this run's status pass (a goal already red that did not change is not a trigger), or a KPI on a goal is stale or missing for a second pass. Not when a check-in for that goal exists from the last 7 days (`hub goal checkin-list <id>`), and at most one
question per goal per week. Only the owner's check-ins count here: your own comment on a goal after a level-3
decision is not their answer.

1. `hub goal show <id>`: owner, KPIs, the reason line, recent check-ins.
2. Ask the owner, a human or a bot, one question with the facts in it:
   "Activation is 52% against the 58% we need on pace this week (from 55% last week). What is going on?" Ask a
   human with `hub task create --owner <person>` when it needs their answer; ask a bot with `hub question ask <bot>`.
   Do not suggest an answer and do not argue with the one you get. Where the Strategy Bot is on, a question to a person
   counts toward their 5 a day: read `asked_today` in `hub goal strategy-bot` first.
3. When the answer arrives, record it in their words:
   `hub goal checkin <goal-id> "<their words>" --signal on_track|at_risk|off_track --from <owner> [--kpi <id>]`.
   Choose the signal only when they said it; otherwise leave it out. A long answer is trimmed to what they said
   about the cause and the next step, quoted, not paraphrased.
4. No answer after 3 days: one nudge, then leave it and mention it in the weekly review.

**Done when:** the owner's answer is recorded in their words as a check-in, or the one nudge is sent and the missing answer is noted for the weekly review.
