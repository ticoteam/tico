---
name: design-a-growth-routine
description: Turn a growth check someone keeps doing by hand (ad fatigue, search rankings, churn signals, mentions, stale numbers) into a routine with a cadence, a condition to act, a self-check, memory between runs and a stop. Use when an owner or person asks for a recurring check, or the same manual check shows up in three weekly pages.
metadata:
  function: growth
---
# Design a growth routine

When to use: someone asks for "a weekly check on X", or the same hand check appears in three
weekly pages. A routine belongs to the Owner whose number it protects; you design it with them and
they own it.

Steps:
1. Name the one number the routine protects and its Owner (`tico growth channel list`).
2. Set how often it **looks**, by how fast the signal moves, not how often an update would be nice:

   | Signal | Look |
   |---|---|
   | search rankings, links | weekly |
   | ad fatigue, cost per customer drift | every 2 to 3 days |
   | signup and activation funnel | weekly |
   | churn signals | daily |
   | content decay | monthly |
   | competitor pricing and pages | weekly |
   | mentions and replies | daily |

3. Set when it **acts**, separately: the threshold that has to be crossed. Most runs should end
   "checked, nothing to do" in one line.
4. Write the self-check before it acts: is the change bigger than normal week-to-week movement, is
   it a holiday or a tracking break (`skills/check-a-number-before-it-is-reported/SKILL.md`)?
5. Write what it remembers between runs: the last run, what it already flagged and a cooldown, so
   it never flags the same thing or messages the same person twice in the window.
6. Write the stop: when it skips, when it hands to a person, and that it turns itself off after
   three errors in a row and says so.
7. Write the output: a line in the Owner's task, a draft (never a send or a spend without the
   owner's rule), or a blocker for a person.
8. Propose it on a task to the Owner with the eight parts above. The Owner creates it
   (`tico automation create --file -`, docs/routines.md). Start with one; add the next only after it
   has run quietly for two weeks.

Checks before you finish:
- Every part is filled; "n/a" is not a stop.
- The cadence matches the signal table.
- Nothing it does spends money or sends to the public without a rule that allows it.
