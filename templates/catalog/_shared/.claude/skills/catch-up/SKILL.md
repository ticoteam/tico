---
name: catch-up
description: Catch a person up on what changed in this chat since their last message before the catch-up request - one table of what is done, one table of what is next with ETAs (anything waiting on them first), and nothing else. Use when a person says catch up, catch-up, /catch-up or ketchup.
metadata:
  function: tico
---
# Catch up

Tell the person what got done since their last message before this catch-up request and what comes next, in two tables.
Notices, task updates and other agents' replies are not the person's messages. Assume they remember their last message
before this request and nothing after it.

Write in plain words, the way one person catches up another. Say what changed for people, not how you wired it up
("tests pass", not "CI is green"). Leave out commit hashes, file paths, commands and test counts unless the person needs
one to act. Name each task, pull request or agent the way the person knows it.

## Gather (read-only)

Keep this turn read-only: do not edit code, start work, move tasks or post anywhere.

1. Collect your work since the person's last message before this catch-up request, what other agents reported back, and
   the live state of every task, pull request or check this chat touched.
2. Check every fact against live state before you write it down.
3. Find what is next: work still running, work you will do next, and anything only the person can do (a merge, an OK, a
   choice, missing access). Include earlier asks that still wait on them. Never invent a step to fill the table.

## Write the reply

Two tables, then stop. No charts, diagrams, images or prose recap.

**Done**

| What | Result | Link |
|---|---|---|

- One row per thing finished since the person's last message before this catch-up request, newest last.
- "Result" is one short line on what changed for people. Name bugs you found and fixed along the way.
- Never list something as done if it still waits on the person or is still running.

**Up next**

| What | Who | ETA | Link |
|---|---|---|---|

- One row per next step. Put rows that wait on the person first, with **You** in "Who" and your pick in "What" (for
  example "Merge #393 (recommend: merge)").
- "Who" is You, me, or the agent or service doing it.
- "ETA" is your honest estimate (for example "~15 min", "after the checks", "today"). If it depends on something else,
  say what ("after the maps box is up"). Write "unknown" rather than guess.

If nothing changed and nothing is next, say so in one line and stop. Leave the Link column empty when there is no link.