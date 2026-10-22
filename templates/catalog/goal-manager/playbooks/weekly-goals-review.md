# Weekly goals review

A short message to the team owner, and to the Chief of Staff if the team has one (`hub team show` shows the
bots; use the one that owns the weekly brief). Under 200 words. It is the Monday note in the owner's chat, and only above Passive (`level` in `hub goal ceo-bot`):
the server starts this routine when the owner raises the level and pauses it back at Passive. Leave the routine as it
is; at Passive, a run of it ends with one line saying the note is off at Passive.

Run `playbooks/goals-make-sense.md` first, so the proposals it files are in the list. Facts first, then
interpretation, labelled:
1. **Red**: each red goal, its reason line, and how long it has been red.
2. **Stale or missing**: each KPI without a fresh reading, since when, and why (from the pass reports).
3. **Suggestions**: colours a human set that the arithmetic disagrees with.
4. **What owners said**: check-ins from the last 7 days, in their words, marked as their words.
5. **Needs a decision**: `hub proposal list` pending, one line each.
6. First, one line on the vision (or that it is still empty), and how many goals still have
   no Why line. At level 2, one line on the recoveries waiting and what the approved ones changed.
7. The level-3 offer, at level 2 only when `level3_offer.due` in `hub goal ceo-bot` is true (10 recoveries approved in
   a row with your pick unchanged, and none offered in the last 30 days): one sentence saying so, and that the owner
   can raise the level in Settings > CEO Bot if they want. Then record it with `hub goal ceo-bot-level3-offer`, so it
   is offered once. Never raise the level yourself, never ask again before it is due, and never press.
Say plainly when nothing needs attention. Send it with `hub message send <person> "<text>"`, and to the Chief of Staff
the same text. Nothing goes outside the team.
