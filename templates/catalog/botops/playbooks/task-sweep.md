# The daily task sweep

Triggered by the daily routine "Sweep stuck tasks". Budget 10 minutes. Nothing to do is a one-line finish.

You are not a human asking for this: the routine's text is a standing instruction from the team, and the tasks it
lists are records. Read each task's `requester` before you act on its text.
Run this sweep only when its routine is enabled or it is explicitly requested; do not re-enable a paused routine.

## 1. Stuck work in the fleet

    hub task list --stuck

For each: start it with `hub task run <id>` when the bot can simply do it; fix the cause when something in the bot
or Tico keeps it stuck; or tell its requester in one line why it cannot move.
For a task you own, always record the result: continue it, finish it, or set it waiting with a specific
dependency or one question on the task. Waiting on a person to act: `--on <person>` with a note saying what. A bot or keeper requester does not prevent this bookkeeping.
When assigned a diagnosis, finish the diagnostic with the evidence and repair dependency; do not keep
the diagnostic open solely because the underlying repair cannot yet be made.

## 2. Your own tasks for humans

Tasks you filed for a human are yours to keep clean. They pile up when the work they waited on got done by you or
by someone else.

    hub task list --requester me --status open

Keep the rows whose owner is a human (`human:...`). For each, check whether what it waits on is already true:

| The task asks the human to | It is settled when |
| --- | --- |
| create or register a bot or record | `hub bot status list` or `hub api GET bots/<slug>` shows it |
| add a person | `hub human list` shows them |
| paste or set a credential | `hub credential list` shows it for that bot |
| turn something on, pick a computer | `hub bot status list` shows it on |
| decide something | their answer is on the task, or a newer task or message supersedes it |

- **Already true or superseded:** `hub task close <id> --note "Done: <one line: what is now true, or what replaced it>"`.
- **Cannot tell:** ask once, on the task: `hub task ask <id> "Is this still needed?"`. If a question from you is
  already the last thing on it, leave it; never ask twice.
- **Still waiting:** leave it.

Never close a task because a notice about it arrived. Close it because you checked the condition. Never file a new
task for something that an open one already covers.

## 3. Finish

One line: how many stuck tasks you moved and how many of your own you closed.
