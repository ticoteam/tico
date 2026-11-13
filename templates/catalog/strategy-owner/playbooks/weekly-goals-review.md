# Weekly goals review

A short message to the team owner, and to the Chief of Staff if the team has one (`tico team show` shows the
bots; use the one that owns the weekly brief). Under 200 words. It is the Monday note to the owner, and
only above Passive (`level` in `tico goal strategy-bot`): the server starts this routine when the level is above
Passive and pauses it back at Passive. Leave the routine as it is; at Passive, a run of it ends with one line
saying the note is off at Passive.

Inputs: `tico goal list --all`, `tico proposal list`, the week's pass reports, `tico strategy show`, `tico goal strategy-bot`, and `tico lens runs`.

Run `playbooks/goals-make-sense.md` first, so the proposals it files are in the list. Facts first, then
interpretation, labelled:
1. **Red**: each red goal, its reason line, and how long it has been red.
2. **Stale or missing**: each KPI without a fresh reading, since when, and why (from the pass reports).
3. **Suggestions**: colours a human set that the arithmetic disagrees with.
4. **What owners said**: check-ins from the last 7 days, in their words, marked as their words.
5. **Needs a decision**: `tico proposal list` pending, one line each.
6. First, one line on the strategy: the published version and its note, or that none is published yet
   (`tico strategy show`), and how many goals with no bet still have no Why line. Then each active bet with no
   KPI-backed goal, and each bet whose health is gray (`goals_by_bet`, `health_by_bet`), one line each, with the
   goal and KPI you would suggest; a bet in both is one line, and none is a line too. When a version is published
   and `proof_since` in `tico strategy show` is null (How we know they want it is empty) or more than 30 days ago,
   run `playbooks/keep-proof-current.md` first, then add one line: how old it is, and that a proposal is in Needs you,
   one was already waiting, or nothing new was found. At level 2, one line on
   the recoveries waiting and what the approved ones changed. At level 3, one
   line per decision you made this week (from `memory/decisions.md`: the goal, what you did, why), and that each has an Undo in
   the Strategy Owner log.
7. The level-3 offer, at level 2 only when `level3_offer.due` in `tico goal strategy-bot` is true (10 recoveries approved
   in a row with your pick unchanged, and none offered in the last 30 days): one sentence saying so, and that the owner
   can raise the level in Settings > Strategy Owner if they want. Then record it with
   `tico goal strategy-bot-level3-offer`, so it
   is offered once. Never raise the level yourself, never ask again before it is due, and never press.
   Also one line suggesting a lens (`tico lens list`; Scenarios, Pre-mortem, Blue Ocean, or Startup idea check for a young company) when no lens has run
   against the current published version in the last 90 days (`tico lens runs`). It is a suggestion in the note; never
   run one yourself from this routine.
Say plainly when nothing needs attention. Send it with `tico message send <person> "<text>"`, and to the Chief of Staff
the same text. Nothing goes outside the team.

**Done when:** the note is sent to the owner (and to the Chief of Staff, if the team has one), under 200 words, or the run has ended with the one-line Passive note.
