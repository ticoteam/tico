---
name: find-the-owner-of-a-task
description: Work out who can do a stopped task's work in its current stage, from the stage's owner and the team, and say so when that seat is not hired instead of handing the task to a stand-in.
---
# Find the owner of a task

When to use: before you reassign a task, and when a task has no owner.

Steps:
1. Read the stage's owner in `knowledge/pipeline.md`, and check it against `tico task show <id>`.
2. Check that the owner can run: `tico team show` shows each bot and person. A bot that is paused, out of credit or has no
   computer is a Paused task, not a wrong owner: leave it, the task carries on when the bot can run.
3. If the stage's owner is active and the task has another owner who cannot do the work, reassign it to the stage's
   owner: `tico task reassign <id> <owner> -m "<why>" --version N`.
4. If the work needs someone else (the feature owner of the area it touches, a person, a bot with the right access), reassign
   it to them and say why in one line.
5. If nobody on the team can do it, do not pick a stand-in. Ask your manager on a task to hire or assign, and snooze the
   task to the date they give, or ask the person above you on the task.

Checks before you finish:
- The new owner is active and can do this stage's work.
- The reason names the work, not a person's failing.
