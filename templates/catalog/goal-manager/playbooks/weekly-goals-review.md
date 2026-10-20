# Weekly goals review

A short message to the team owner, and to the Chief of Staff if the team has one (`hub team show` shows the
bots; use the one that owns the weekly brief). Under 200 words. It is paused until the owner has read the first
one; then arm it with `hub routine update <id> --enable`. Where the CEO Bot is on (`hub goal ceo-bot`), turning it on
already started this routine: it is the Monday note in the owner's chat, and you leave the routine as it is.

Run `playbooks/goals-make-sense.md` first, so the proposals it files are in the list. Facts first, then
interpretation, labelled:
1. **Red**: each red goal, its reason line, and how long it has been red.
2. **Stale or missing**: each KPI without a fresh reading, since when, and why (from the pass reports).
3. **Suggestions**: colours a human set that the arithmetic disagrees with.
4. **What owners said**: check-ins from the last 7 days, in their words, marked as their words.
5. **Needs a decision**: `hub proposal list` pending, one line each.
6. Where the CEO Bot is on: first, one line on the vision (or that it is still empty), and how many goals still have
   no Why line.
Say plainly when nothing needs attention. Send it with `hub message send <person> "<text>"`, and to the Chief of Staff
the same text. Nothing goes outside the team.
