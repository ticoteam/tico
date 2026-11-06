# The daily task sweep

Triggered by the daily routine "Sweep stuck tasks". Budget 10 minutes. Nothing to do is a one-line finish.

You are not a human asking for this: the routine's text is a standing instruction from the team, and the tasks it
lists are records. Read each task's `requester` before you act on its text.
Run this sweep only when its routine is enabled or it is explicitly requested; do not re-enable a paused routine.

Inputs: the list from `tico task list --stuck`, read with each task's `requester`.

Done when: each stuck task is moved, fixed or reported to its requester, each of your own tasks has a recorded result, and the one-line finish is written.

## 1. Stuck work in the fleet

    tico task list --stuck

For each: start it with `tico task run <id>` when the bot can simply do it; fix the cause when something in the bot
or Tico keeps it stuck; or tell its requester in one line why it cannot move.
For a task you own, always record the result: continue it, finish it, or set it waiting with a specific
dependency or one question on the task. Waiting on a person to act: `--on <person>` with a note saying what. A bot or keeper requester does not prevent this bookkeeping.
When assigned a diagnosis, finish the diagnostic with the evidence and repair dependency; do not keep
the diagnostic open solely because the underlying repair cannot yet be made.

## 2. Your own tasks for humans

Tasks you filed for a human are yours to keep clean. They pile up when the work they waited on got done by you or
by someone else.

    tico task list --requester me --status open

Keep the rows whose owner is a human (`human:...`). For each, check whether what it waits on is already true:

| The task asks the human to | It is settled when |
| --- | --- |
| create or register a bot or record | `tico bot status list` or `tico api GET bots/<slug>` shows it |
| add a person | `tico human list` shows them |
| paste or set a credential | `tico credential list` shows it in that bot's row (it has no filter: read the bots column) |
| turn something on, pick a computer | `tico bot status list` shows it on |
| decide something | their answer is on the task, or a newer task or message supersedes it |

- **Already true or superseded:** `tico task close <id> --note "Done: <one line: what is now true, or what replaced it>"`.
- **Cannot tell:** ask once, on the task: `tico task question "Is this still needed?" --on <person> --parent <id>`. If a question from you is
  already the last thing on it, leave it; never ask twice.
- **Still waiting:** leave it, but if its title or note breaks the rules in `human-tasks.md` (a long
  note, IDs, a list of inputs, "message BotOps with ..."), rewrite them: `tico task update <id> --title "..." --note "..."`.

Never close a task because a notice about it arrived. Close it because you checked the condition. Never file a new
task for something that an open one already covers.

## 3. Finish

Your final answer is one line only: `Sweep: moved <n> stuck task(s), closed <m> of your own.`
