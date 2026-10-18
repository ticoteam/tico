# Health Monitor

The Health Monitor is a built-in helper that keeps an install healthy without anyone having to look. It is **off by
default**. The owner turns it on or off with one switch in **Settings > Health** (`POST /api/v2/health-monitor/turn-on`,
`/turn-off`). Turning it off pauses it; its history stays.

Settings > Health also lists checks that belong to one feature, documented on that feature's page: public roadmap
suggestions with no receiving bot are in [Public roadmaps](roadmaps.md).

## What it checks

Once an hour, while it is on, the server checks the install in plain code (`backend/health_monitor.py`). No model runs:

| Check | Kind | What it means |
|---|---|---|
| `runner_outdated` | setup | An online computer is on an older release, cannot work with this one, or failed to update; or it holds queued work until its running turns finish so it can update (the finding names that work) |
| `runner_restarting` | setup | A computer a person pressed Restart for has said it is restarting for over 34 minutes (20 minutes still claiming, then up to 14 for its running turns); the finding names the turns still running and the work it holds |
| `computer_silent` | setup | A computer has not checked in for 10 minutes or more while work is queued for its bots |
| `limited_no_fallback` | setup | A bot's turns stopped on a subscription or spend limit in the last day and it has no fallback model: none of its own, and not the company's either (*Fall back when a plan runs out* is off in **Settings > AI providers**, no company provider is on another runtime than the bot's, or the bot turned fallback off or names a fallback tool with no model) |
| `default_login_limited` | setup | A computer's default login for an AI tool (the one a turn uses with no subscription) is out of quota: in the last day at least two turns on it stopped on a usage limit and none has completed since, and the provider gave no reset time or one still ahead. One finding per computer and tool; it goes away once a turn on that login completes or the reset time passes. Fallback hops and check-model turns count; a turn on a subscription does not, nor one on a tool each bot signs in with its own key (`api_keys = "bot"`: Gemini CLI, pi). While it lasts, a check or review subtask on that login moves to the next check model instead of retrying ([A subtask on a check model](tasks.md#a-subtask-on-a-check-model)) |
| `stuck_setup` | setup | A bot has been in setup over an hour while work waits for it |
| `worktree_error` | setup | A task worktree failed on its computer |
| `push_stuck` | setup | A runner reports a checkout with commits it cannot push (diverged) |
| `cannot_fetch` | setup | A runner keeps failing to fetch a bot's repository |
| `queue_idle` | tico-bug | The job the next claim would hand out (a person's message before older work) has been queued 15 minutes or more (a person's message: 2 minutes) on an online computer that has room, no limit and no update or restart waiting, and none of the claim's own reasons to hold it (drained, worktrees being maintained, a lapsed lease still running, interrupted work awaiting review, a usage-limit cooldown, readiness not reported, a rejected sign-in, a subscription profile, a check model, a busy lane). A held bot's Health row names that reason instead. Its report says where as `Where: bot-N on runner-M`, the labels of the attached diagnostics bundle |
| `worktree_fallback` | tico-bug | A task's turn ran in the bot's checkout because its worktree hit Permission denied |
| `event_stream` | tico-bug | A runner lost its event stream three times in its last 50 error lines, not counting drops from 5 minutes before a server start (a deploy, an update, a restart) to 2 minutes after, nor drops the runner says were back within 30 seconds (a proxy or tunnel resetting the long-lived stream; the runner polls meanwhile and catches up on reconnect) |
| `readiness_403` | tico-bug | A runner's readiness report keeps being refused |
| `server_cpu` | tico-bug | The server averaged 40% CPU or more for an hour while hardly used |

Runner checks read the error lines each runner already sends with its heartbeat (`recent_errors`). The Health Monitor's
own [watcher](watchers.md), `software/health-scan`, runs on its computer every hour and reports a process or Docker
container using 50% CPU or 2 GB of memory in two runs in a row (`HEALTH_BUSY_CPU`, `HEALTH_BUSY_MB`), leaving out the
model CLIs that run turns.

Each finding has a **key** (what and where, on this install) and a **signature** (`hm-` and 12 hex digits: what, without
where, the same on every install). A finding the last scan did not have opens a task for the Health Monitor, or wakes it on
the task it has; a finding still there an hour later says nothing; one that goes away and comes back wakes it again,
unless it is back within a day of its last wake, in which case it only counts.

## What it does with a finding

- **setup**: it gives BotOps a task with the fix. BotOps does it under its own rules. A setup finding is never sent to
  the Tico team: its text names this install's bots and computers, and `report` refuses it (409 `not_a_tico_bug`),
  with or without the team rule below.
- **tico-bug**: `hub health-monitor report <key>` files a support ticket to the Tico team. The server writes the message
  from the check alone (check, what it means and the fix in fixed words, signature, kind, version, how often) and attaches
  the redacted [diagnostics bundle](support.md). The finding's own text (which names computers and bots), task text,
  message text and people's words are never sent. A busy process is named only when it is one of Tico's own (`python3`,
  `uvicorn`, `node`, `git`, `docker`, `caddy`, `cloudflared`, `tico`, or a Tico compose service's container); anything
  else is "a process" or "a Tico container". One ticket per signature;
  when the finding comes back, a "Seen again" message goes on the same ticket. The ticket is the owner's: replies show on
  their Support page. At most 5 new reports a day.

The team rule **Health Monitor may file Tico bug reports without asking** (`health_monitor_reports`, on by default, in
Settings > Health and `PUT /api/v2/access/rules`) lets it file without a Needs you card. With it off, each report is a
`support` approval a person reads first, sent with `hub support send --approval <id>` once approved. A declined or
withdrawn approval clears the report, so the next `report` of that signature asks again.
`TICO_SUPPORT=off` turns reporting off with the rest of support.

## On the Tico team's side

A ticket starting `[Health Monitor]` carries its signature. HQ keeps the signature with each ticket, so the staff list
groups by it (`GET /v1/staff/tickets?signature=hm-...`). The Support bot groups tickets by signature: one engineering
task per signature, and every other install's ticket or repeat is a comment on it
(`templates/catalog/support/playbooks/tico-hq-tickets.md`). When a release ships the fix, the team replies once to every
open ticket with that signature and closes each (`POST /v1/staff/tickets/by-signature/close`, or
`software/hq-tickets close-signature hm-...`); each company reads the reply on its Support page.

## Commands

    hub health-monitor findings                       what the last scan found, with each signature's report
    hub health-monitor report <key>                   report a finding as a Tico bug
    hub health-monitor report --process process:<name> --cpu N --mb N   report what the watcher saw
