# Task Manager

The Task Manager is a built-in helper that keeps tasks moving without anyone having to look. It sits outside the
org chart like the [Librarian](librarian.md) and the [Health Monitor](health-monitor.md), and runs on Haiku 5.5 at low
effort. Every team has it: it is added when a team is set up, and an existing team gets it on upgrade. It is **on by
default**. The owner can pause it (`POST /api/v2/task-manager/turn-off`, `hub task-manager bot off`); pausing keeps its
history and `turn-on` wakes it again.

It tidies tasks, never the work. It does not touch bots, credentials, computers, sends or spending.

## What it checks

Once an hour on weekdays, 08:00 to 18:00 in the team's time zone, the server looks for these in plain code
(`backend/task_manager.py`). No model runs for the look.

| Item | What it means |
|---|---|
| Quiet task | Nothing has moved it for an hour and it waits on nothing. Where Decide read its last messages ([quiet task triage](tasks.md)) and could not tell, or found it stuck, the line says `class: unsure` or `class: stuck`. |
| Overdue ask | A question to a person is past its deadline, or has gone a business day unanswered. |
| Review or QA ticket without a PR | A review or QA child of a ticket has no linked pull request, or a second one is open for the same parent and step. |
| Merged or shipped, step unmoved | A ticket's pull request merged or its release shipped, but the ticket did not move. |
| Blocked by its own subtasks | A task waits only on its own quiet subtasks. |
| Held bot | A bot is held by back-off or a turn budget. Skipped on an install without the hold columns. |
| Owner review waiting | A bot's interrupted runs have waited more than an hour for its owner. |
| Duplicate titles | Two open tasks share a title, requester and owner. |

A task belongs to its owner's department, one of the nine on the org chart (a group inside it counts as it). A task
whose owner is in none, such as BotOps or the Assistant, is listed under **Company**. Each department lists at most 30
items. An item reported once is left out for a day unless its task changed, so a case the bot was unsure of does not
wake it every hour.

A private task, or one of a bot the Task Manager has no access to, never appears in a report, and the bot cannot read
or write it: it meets the same privacy fence as any other bot.

## What happens next

- **Nothing to report:** no task, no model call. The run record says "Nothing to do".
- **Something to report:** one task per department, owned by the Task Manager, titled `Task check: <department> (<n>)`.
  Each item has the task id, its title, its owner, why it is listed and the `hub` command that would fix it. The run
  record says "3 departments, 7 items".
- **A check task is still open:** the next report replaces its body and wakes the bot only if the body changed, so
  reports never pile up. Once the bot marks it done, the next report opens a new one.

The wake is the routine's own (`trigger=schedule`, `refs.wake=task_manager_report`).

The bot acts, it does not ask: it fixes the obvious, low-risk items with the suggested command and notes what it did on
the check task. For anything it is unsure of it leaves one line for the department in **Needs you**, never one per item.

## What it may do

Narrow rights on tasks only, through the ordinary permission system:

- close a task with a note, and set its status, step and wait;
- relate and unrelate tasks, and comment;
- reassign a task within the owner's department (anywhere else is refused);
- file a subtask for BotOps when the fix is in the setup.

It cannot change a task's lane, number, labels or type, and it is never a task mover.

## Switches and commands

The hourly report is a [system routine](system-routines.md#task-manager-report) (key `task_manager_report`). The owner
or an admin can switch it, pick its schedule and run it now.

| Do | API | `hub` |
|---|---|---|
| See the bot, the report's switch, schedule, last run and each department's last check | `GET /api/v2/task-manager` | `hub task-manager status` |
| Report on or off | `PUT /api/v2/system-routines/task_manager_report` `{"on": false}` | `hub task-manager on` / `off` |
| Schedule: `business` (default), `hourly`, `4h`, `daily` with `at` | same, `{"preset": "daily", "at": "09:00"}` | `hub task-manager schedule daily --at 09:00` |
| Run the check now | `POST /api/v2/system-routines/task_manager_report/run` | `hub task-manager run` |
| Pause or wake the bot (owner) | `POST /api/v2/task-manager/turn-off` / `turn-on` | `hub task-manager bot off` / `on` |

The same four actions are the MCP tools `hub_task_manager`, `hub_task_manager_set` and `hub_task_manager_run`.
While the bot is paused the report finds nothing to wake and says so on its row.
