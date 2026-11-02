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

Each turn on a task leaves a **checkpoint**: the turn's final reply, kept on the task.

A bot's chat and a task each keep one session, however long it runs. The harness compacts it on its own when it nears
the bot's `compact_at`, and that is the only size limit: Tico never starts a new session to save tokens. The next turn
resumes the saved session. If the host refuses, Tico asks once more after a short pause.

A new session starts only when the saved one is not on this computer (another computer ran the bot, or its
files were cleared) or the host refuses it twice. The new session starts from the task's checkpoint and what was said
since that turn, the bot's own replies included, and it is told so. A session does not move between computers. Every
run records how its session began (`turns.session_kind`, `turns.session_why`, [hub sql](hub-sql.md)): resumed or new,
with the reason (`none_saved`, `not_found`, `resume_failed`, `compact_failed`, `interrupted`, `fallback_side`). A new
session other than the first also leaves a line in the run's diagnostics and in the task's history ("Started a new
session: the old one wasn't on this computer"), and Health names a bot that lost its session more than twice in a day.

A resumed session is sent only what is new: what was said since its last turn, the current message and this turn's
notices. On a harness that says when it compacts (the `compaction_events` capability: Claude Code and pi), the bot's standing
instructions become one line while they, AGENT.md and the model are unchanged, and the assigned task one line while it
is unchanged, but only while the session's last turn and the whole prompt come to under 60% of its Compact at. If the
session compacts during a shortened turn anyway, the turn is given the lines it left out at once. The whole prompt goes
again on a new session, after any compaction (the harness's own or `compact_first`), after a side session or an
interrupted turn, and after a turn that did not complete; the repo rules go again after a compaction or a turn that did
not complete. A resumed session that turns out to be missing starts again with what a replacing session gets: the
repo rules, the checkpoint and the room since its last turn. Other harnesses get the whole prompt every turn.

A stateless bot (`stateless` in its config; Counsel by its template) keeps no session across tasks. Each task starts its
own session and resumes only that task's, and a chat message to it starts a new session that is not saved. It gets the
whole prompt every turn and is never told to read `memory/` or `state.md`. A new session for a task still starts from
the task's checkpoint, as above.

A turn that was cut off (a restart's drain, a **Stop**) leaves its session in place. The next turn resumes it and is
told to check what the interrupted turn already did before repeating a step.

When a turn runs on a fallback harness or model, it runs in a side session of its own, started from the checkpoint. It
never replaces the saved session; the next turn on the usual harness resumes that one and is told a side session ran.

When a run fails because the session ran out of room, the next claim for it carries `compact_first`: the saved session
is resumed and compacted before the turn (Claude Code `/compact`, Codex `thread/compact/start`, pi's RPC `compact`,
other harnesses with the `compaction` capability their own way). A harness that cannot compact, or a compaction that fails, starts a new
session (`compact_failed`). If the compacted run still fails the same way, or an older runner ignores the flag, Tico
clears the session for the next run. `/compact` keeps its session.

Older runners also started a new session once a session passed a size (`rotate_at`, `rotate_after_turns`); those
settings are gone, and a saved config that still carries them is ignored.

**Clear** on a bot's chat starts that chat over: the next turn opens a new provider thread and reads only what was
said after the clear. The main chat is one thread shared by the bot's chats, so a clear in one person's chat starts
that shared thread over too; others' chats then continue from their own history in Tico. A bot's computer must run
a runner from this release or later; an older runner keeps resuming the old thread.

In a long direct chat with a bot, when you change subject, Tico may start a fresh session, as if you pressed **Clear**,
and shows a quiet **Topic changed** line above your message. The bot no longer carries the old conversation, which keeps
long chats cheaper and on topic. An owner or an admin turns this on or off in **Settings > System**, **Start fresh when
the topic changes** (off by default). It never happens in task chats or group chats, and there is no undo. If Tico isn't
sure, or the check takes over 1.5 seconds, nothing changes and your message goes to the same session.

Nothing closes on its own. A task with no activity for 7 days gets one **Still wanted?** question, with **Keep** and
**Close**, to the person who asked for it (or the person who owns it). See [Picking work back up](tasks.md#picking-work-back-up).

## Spot checks

Tico watches running tasks. A task with no progress for 30 minutes gets a status check in its session: checkpoint,
or mark it blocked or done. With still no progress, Tico retries the bot once. Thirty minutes later, still nothing, the
main chat gets a **stuck** line and BotOps gets a task to find out why ([Work that is waiting](tasks.md#work-that-is-waiting)).
Tico asks no person about a stuck bot. These count toward the stalled-task limit of three a day.

An idle task unchanged since its owner's last settled checkpoint skips a plain automatic stall or status check.
Changed state or a missing checkpoint leaves the check eligible under the usual wake rules. Human messages still
wake the bot as before, and leased or running sessions keep their recovery checks
([details](hub-sql.md#what-woke-each-run-and-what-it-did)).

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
runs at once from its size (`capacity` in [runner.json](environments.md#runnerjson)). An owner or an admin can set
**Turns at once** on the computer instead (Settings > Computers), up to 256; clearing it goes back to the size. A runner
that predates `turns_at_once_v1` runs at most the capacity it reports, and Settings says so. Turns on the same task,
and in the main chat, still take turns.

Nothing caps a bot below the computer's turns. When a turn frees, it goes to the bot holding the fewest turns
there, then to the work that has waited longest, so bots that share a computer take turns between them. A bot's own
**Turns at once** (set in its editor) caps its turns across every computer together, a person's message included: at
its limit its queued work waits on every computer, and its other bots keep their turns. Work held back this way says
"Waiting for a
free turn on <computer>" in its chat and on its task. The task shows the same waiting reason as the chat (a paused bot,
an update, a computer being set up) to anyone who can read the task.

Each task session has a git worktree at `sessions/bot-<slug>/<task>` on branch `tico-session/<task>`. A bot can hold
up to 30 worktrees, or fewer when the computer gets under 5 GB free. Nothing is removed to make room: at the limit
the bot ends its turn and Tico wakes the task when its worktree is ready ([Task worktrees](worktrees.md)), or the bot,
or a person, cleans one up first. A task's worktree goes when the task is finished: at the end of
that bot's turn, or within about 10 minutes for another bot's copy or one whose files were still uploading. A worktree
with unsaved changes, or commits that are on no remote, stays until someone saves or discards them.
See [Task worktrees](worktrees.md) for code worktrees and cleanup.

A private live meeting's bot turns get a worktree of their own the same way, `sessions/bot-<slug>/private-<id>`, but
nothing in it is ever saved: it is never committed for the bot or pushed, and it is deleted, unsaved changes and all,
within about 10 minutes of the meeting ending ([Live meetings](live-meetings-api.md)). Unlike a task's, it needs no
upstream: a checkout with no remote gives it a copy of its own `HEAD`.

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

### Why a run stopped or stalled

`hub run list <bot>` includes `end_reason`, `ended_at`, `last_event`, `last_event_at` and
`last_heartbeat_at`. Reasons distinguish a finished run, a model error (with its reported code and message),
a process exit (code or signal), a named timeout limit, cancellation and its actor, a lost heartbeat,
and a runner restart. An explicit runner OOM report is identified as OOM; a kill signal or words in stderr alone do not prove an OOM kill.

Every minute, the server checks attempts for five minutes without either an attempt heartbeat or an event.
It marks them `stalled` in the run list and Health, with their last event and time. This warning does not stop
the attempt or change its lease. Fresh activity clears it.

The server also ends a run that has been silent this way, with no heartbeat or event for five minutes, once it has run
past its bot's `max_run_minutes` plus five minutes. It does not wait for the computer. The run ends as `expired`, and its
end reason reads "Ended by the server: no heartbeat for 5 minutes, more than 5 minutes past max_run_minutes (60 minutes)",
where 60 is the bot's own limit. A renewal does not restore the run, but its late result is still filed. While
the computer keeps sending heartbeats, its own limit applies instead.

The runner also keeps up to 200 lines / 64 KiB of scrubbed harness stderr per attempt for seven days,
even with provider stream capture off. `hub run export <attempt> --fixture` adds `stderr.log` when present,
and works with stderr alone when no provider stream exists. Its access rules remain the same as raw stream
exports. The MCP summary reports the stderr byte count and end reason without returning the log text.
An older runner can report only the failure evidence it has; historical runs are not assigned a guessed cause.

## FAQ

**Why didn't my main chat see what I said in a task?**
Each task has its own session. The main chat sees only a one-line report when the task starts, is blocked, done,
declined or stuck. To give the task more to go on, write in the task's thread.

**Can a bot work on two of my tasks at once?**
Yes. Each task runs in its own session and worktree, side by side. Two messages on the same task take turns. On a Codex ChatGPT sign-in, the task lanes run side by side too and share the plan's usage allowance ([how it works](how-it-works.md#one-turn-per-chatgpt-login)).

**What happens when a bot needs something from me?**
It files a subtask for you under its task, which shows in your Needs you. When you finish it, the bot wakes on the
original task and continues in the same session.

**A task's session was lost. Does the bot start over?**
Only if it cannot be found on this computer or the host refuses it twice. The next turn then starts a new session from the task's last checkpoint, and the run says why.

**Where did temporary assignments go?**
They were removed in 0.3.41. Per-task sessions do the same job: give the bot the task, and it gets its own session.
