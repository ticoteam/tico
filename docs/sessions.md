# Sessions

A **session** is one model conversation a bot resumes: a Claude Code or Codex thread on its computer.
A bot has more than one. Its main chat is one session, and every task it owns is another.
This page explains how they fit together. [Tasks](tasks.md#subtasks-and-prs) has the exact rules.

## The main chat and task sessions

- **Main chat**: the bot's chat on its page. Its session reads only main-chat messages and short task reports.
- **Task session**: each task the bot owns. It has its own room (the task's thread), its own git worktree of the
  bot's repository, and a primary session link on the task.

Task traffic never enters two context windows. What you say in a task stays in that task's session, and what you
say in the main chat stays in the main one.

When you ask a bot for something in chat and it files a task, the work moves to the task's room. The chat gets one
quiet line, "Started task: <title>". Tap that line, or any task row in the bot chat, to open the task's thread.
Messages you write there go to that task's session.

## Reports in the main chat

The main chat hears where each task got to in one quiet line: **started**, **blocked**, **done**, **declined** or
**stuck**, with the start of the note. These lines start no run. The main session reads them the next time
something wakes it. The same state is not reported twice in a row, and a private task is reported only where every
reader of the chat may read it.

## Subtasks

A subtask someone else files for a bot under its task continues the parent's session by default: same room, same
worktree, same checkpoint. The bot's turns on the parent and that subtask take turns. A subtask the bot filed for
itself is new work, so it starts its own session and runs beside the parent. A subtask whose parent belongs to
someone else, or has different readers, also starts its own session.

When a bot is blocked on a person, it files a subtask for that person under its task. When the person finishes,
declines or closes it, the bot wakes on the parent, in the same session, and picks up where it stopped.

Set `subtask_session: own` to give every subtask its own session instead.

## Checkpoints and resume

Each turn on a task leaves a **checkpoint**: the turn's final reply, kept on the task. If a task's session can't be
resumed (a new computer, a cleared thread), the next turn starts a fresh session from that checkpoint.

**Clear** on a bot's chat starts that chat over: the next turn opens a new provider thread and reads only what was
said after the clear. The main chat is one thread shared by the bot's chats, so a clear in one person's chat starts
that shared thread over too; others' chats then continue from their own history in Tico. A bot's computer must run
a runner from this release or later; an older runner keeps resuming the old thread.

In a long direct chat with a bot, when you change subject, Tico may start a fresh session, as if you pressed **Clear**,
and shows a quiet **Topic changed** line above your message. The bot no longer carries the old conversation, which keeps
long chats cheaper and on topic. An owner or an admin turns this on or off in **Settings > Routines**, **Start fresh when
the topic changes** (off by default). It never happens in task chats or group chats, and there is no undo. If Tico isn't
sure, or the check takes over 1.5 seconds, nothing changes and your message goes to the same session.

Nothing closes on its own. A task with no activity for 7 days gets one **Still wanted?** question, with **Keep** and
**Close**, to the person who asked for it (or the person who owns it). See [Picking work back up](tasks.md#picking-work-back-up).

## Spot checks

Tico watches running tasks. A task with no progress for 30 minutes gets a status check in its session: checkpoint,
or mark it blocked or done. With still no progress, Tico retries the bot once. Thirty minutes later, still nothing, the
main chat gets a **stuck** line and BotOps gets a task to find out why ([Work that is waiting](tasks.md#work-that-is-waiting)).
Tico asks no person about a stuck bot. These count toward the stalled-task limit of three a day.

## Task goals

With `task_goal: on`, a bot works each task it owns as a goal: keep going until the task's title and acceptance
criteria hold, then mark it done. On Claude Code and Codex this is the harness's native goal (`/goal`, Codex goals),
set in the task's room; other harnesses get it as a line in the prompt. A met goal marks the task done. A paused or
stopped goal leaves the task open, with a **blocked** or **stuck** line in the main chat.

A `goal:on` or `goal:off` label on a task overrides the bot's setting. A person who clears or edits the goal in the
task's room takes it over, and Tico leaves it alone after that. Goals in chat work the same way:
see [Chat goals](chat-goals.md).

## Many tasks at once

A bot works its tasks at the same time, each in its own session and worktree. The computer decides how many turns it
runs at once from its size (`capacity` in [runner.json](environments.md#runnerjson)). Turns on the same task, and
in the main chat, still take turns.

When other bots share the computer, one bot holds at most half of its turns (at least one), so the others always have
room for a review or a release; a bot alone on its computer uses every turn. A person's own message to the bot is not held back by this. When a turn frees, it goes to the bot
holding the fewest turns there, then to the work that has waited longest. Work held back this way says "Waiting for a
free turn on <computer>" in its chat and on its task. The task shows the same waiting reason as the chat (a paused bot,
an update, a computer being set up) to anyone who can read the task.

Each task session has a git worktree at `sessions/bot-<slug>/<task>` on branch `tico-session/<task>`. A bot can hold
up to 30 worktrees, or fewer when the computer gets under 5 GB free. Nothing is removed to make room: at the limit
the bot ends its turn and Tico wakes the task when its worktree is ready ([Task worktrees](worktrees.md)), or the bot,
or a person, cleans one up first. A task's worktree goes when the task is finished: at the end of
that bot's turn, or within about 10 minutes for another bot's copy or one whose files were still uploading. A worktree
with unsaved changes, or commits that are on no remote, stays until someone saves or discards them.
See [Task worktrees](worktrees.md) for code worktrees and cleanup.

## Linked sessions

You can link other sessions to a task to track them: a Bot Desk or Orca thread URL, a `claude --resume <id>` or
`codex resume <id>` command. Use **Sessions +** on the task or `hub task link <task> <value> --session`. Linked
sessions are for tracking only. Task chat and comments always go to the owning bot's primary session.

The task's ⋯ menu has **Copy session ID** for the primary session, to resume it by hand.

## Settings

| Setting | Values | What it does |
|---|---|---|
| `session` | unset (default), `task`, `bot` | Unset: each task is its own session and chat shares one. `task`: each chat conversation also gets its own (BotOps). `bot`: one session for everything. |
| `subtask_session` | `parent` (default), `own` | Whether a subtask someone else files continues its parent's session or gets its own; one the bot files for itself always gets its own. |
| `task_goal` | `off` (default), `on` | Work each task as a native goal until it is met. |
| `review_model` | `own` (default), `check` | Run a subtask handed to it under another's task on the company's check model ([Tasks](tasks.md#a-subtask-on-a-check-model)). |
| `goal:on`, `goal:off` | task labels | Override `task_goal` for one task. |
| `capture_stream` | `off` (default), `on` | Keep each run's raw provider stream, both ways, for a week (owner only; see below). |

Set them with `hub bot update <slug> --session task`, `--subtask-session own`, `--task-goal on` or `--capture-stream on`, or `hub_bot_update`.

### Capturing a run for a replay test

With `capture_stream: on`, the runner records every frame it sends a Claude or Codex harness and every frame it reads
back, with the turn's secrets masked, up to about 5 MB per run (a frame that no longer fits is left out with a `skipped` record). Other harnesses
are not captured yet. Tico keeps each capture for 7 days; turning capture off deletes the bot's captures, and a run that had a secret taken out while it was going keeps none. `hub run export <attempt> --fixture` writes it as a folder of
three files: `input.json` (the message and model settings), `<provider>_transcript.ndjson` (the raw frames) and
`output.json` (the events and result Tico recorded). A raw stream can hold customer text, so only owners turn it on,
and only owners and engineering bots read it, for runs they can already see in the bot's run list. The MCP tool answers only whether a run was captured and its size; the CLI writes the files.

## FAQ

**Why didn't my main chat see what I said in a task?**
Each task has its own session. The main chat sees only a one-line report when the task starts, is blocked, done,
declined or stuck. To give the task more to go on, write in the task's thread.

**Can a bot work on two of my tasks at once?**
Yes. Each task runs in its own session and worktree, side by side. Two messages on the same task take turns. On a Codex ChatGPT sign-in, the task lanes take turns too, because OpenAI allows one job at a time per login: a waiting task shows *Waiting for <login>* with who holds it and its place, not a failure ([how it works](how-it-works.md#one-turn-per-chatgpt-login)).

**What happens when a bot needs something from me?**
It files a subtask for you under its task, which shows in your Needs you. When you finish it, the bot wakes on the
original task and continues in the same session.

**A task's session was lost. Does the bot start over?**
No. The next turn starts a fresh session from the task's last checkpoint.

**Where did temporary assignments go?**
They were removed in 0.3.41. Per-task sessions do the same job: give the bot the task, and it gets its own session.
