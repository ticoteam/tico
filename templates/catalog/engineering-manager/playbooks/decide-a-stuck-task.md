# Decide a stuck task

Use this each time {{app_name}} wakes you about a task: its owner couldn't move it, it is Late, it has no owner, it is on
ice, it was pushed past, or it changed after an OK or merged outside the queue. Budget 10 minutes. The
outcome is one decision on the task with one line of reason.

## Steps
1. Read the task: `tico task show <id>`. Note its version, stage, owner, whose turn it is, what is missing to leave the
   stage, who tried and when it last changed. Read the owner's last notes in its conversation.
2. Read what is missing with `skills/read-what-is-missing/SKILL.md`. Unread is not met.
3. Pick the one decision that fits:
   - The owner is the wrong one, or it can't run, or there is none: **reassign** to the bot or person who can do it
     (`skills/find-the-owner-of-a-task/SKILL.md`): `tico task reassign <id> <owner> -m "<why>" --version N`.
   - An earlier stage's work is wrong or missing: **move back** to that stage, saying what to redo:
     `tico task move <id> "<stage>" -m "<what to redo>" --version N`.
   - What is missing no longer matters: **move on**, saying why: `tico task move <id> next -m "<why>" --version N`.
   - It waits on the outside world or a known date: **snooze** to that date:
     `tico task snooze <id> --until <date> -m "<what it waits for>" --version N`.
   - It is no longer wanted, or duplicates another task: **close** it: `tico task close <id> -m "<why>" --version N`.
     If a person asked for it, ask them instead.
   - Only a person can decide: **ask** them once, with `skills/write-the-stuck-task-question/SKILL.md`.
   - A bot or a tool is broken: **file** the fix for BotOps: `tico task file --parent <id> --owner botops "<what is broken>"`.
   - A failing check or a review finding on a task's own pull request goes back to that task's owner, who fixes it in
     place; file a separate task only for a broken bot, tool or shared infrastructure, and never a person's task about it.
4. Late with the owner still working (new commits, new page versions): the task is slow, not stuck. Leave the owner on
   it, and say so in one line: `tico task comment <id> "<what changed, why it stays>"`. That alone is not a decision, so
   pair it with a snooze to the date the owner named, or ask the owner's manager.
5. Record a stop that repeats in `memory/learnings.md`. The third repeat in a week is a proposal to your manager, on a task.

## Never
- Do the owner's work, or attach a page or answer an ask in its place.
- Push past a requirement that can't be pushed past, or a people-only OK.
- Reassign to make a task move when the new owner can't do the work.
