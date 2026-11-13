---
name: read-a-bet-and-its-wrong-if
description: Read one strategy bet from the Strategy page and check its wrong-if against the record before saying anything about the bet's status. Use it at the start of every check-in and whenever a task asks how a bet is doing.
---
# Read a bet and its wrong-if

When to use: at the start of a check-in, before any call on the bet, and whenever a task asks whether a bet still holds.

Steps:
1. Run `tico strategy show`. Find the bet by the id recorded in `knowledge/plan.md`. Record its statement, what it rules out, its why,
   its status and its wrong-if exactly as published, with the strategy version number.
2. If the bet is not in the published strategy, or is not `active`, stop. Say so in one line. Do not infer a status.
3. Read the goals under the bet in `goals_by_bet` and their colours in `health_by_bet`. A goal with no KPI is coloured by its
   owner's check-ins, so say which kind each goal is.
4. Check the wrong-if by its kind:
   - a KPI threshold: `tico kpi show <kpi id>`. Read the last few readings with their periods. A reading counts as past the line
     only when it is past it; a reading on the line is not;
   - a market entity: `tico market show <id>` and `tico market delta`, with the source of each change;
   - a dated sentence: note the check-by date. Before it, the answer is "not yet checkable".
5. Check `tico proposal list` for a pending strategy draft on this bet. If one exists, the bet was already raised.
6. Note any change to the bet since the last check-in, with the version it came from.

Checks before you finish:
- Every reading you quote has its date and its period.
- The wrong-if is quoted as published, not paraphrased.
- Nothing about the bet was changed on the Strategy page, in a goal or in a KPI.

Example (generic): "Bet 'Repeat buyers do the work of new ones': wrong-if is a 90-day repeat rate below 40 percent, check by
2027-01-31. Readings 2026-10-20 to 2026-11-03: 46, 43, 41. None past the line. Version 4 of the strategy."
