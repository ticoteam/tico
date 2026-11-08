# Health Monitor

The Health Monitor is a built-in helper that keeps an install healthy without anyone having to look. It is **off by
default**. The owner turns it on or off with one switch in **Settings > Health** (`POST /api/v2/health-monitor/turn-on`,
`/turn-off`). Turning it off pauses it; its history stays.

Settings > Health also lists checks that belong to one feature, documented on that feature's page: public roadmap
suggestions with no receiving bot are in [Public roadmaps](roadmaps.md), and the Tico AI Gateway key (near or at its
monthly cap, not recognised, or not ready) is in [Tico AI Gateway](ai-gateway.md#what-the-card-says).

The **Turns** check shows each computer's running, starting, waiting and queued counts, with each
wait reason and oldest age. See [Turn counts](computers.md#turn-counts) for the start deadline, load brake and older-runner rules.

The **Company turns** check records when the company warning level is crossed, even when Health Monitor is off.
It shows the company count, warning and stop levels, and when the incident opened or resolved.
See [Company turn levels](computers.md#company-turn-levels) for what counts, defaults, recovery and how an admin changes the levels.
This record does not provide the separate automatic fixer.

Health's **Stuck bots** check and `hub health check` name the bot, its queue split as `hub pipeline` splits it (jobs
that can start, jobs behind an interrupted run awaiting review, jobs held on purpose), the oldest age, why it cannot
start, and a fix or a command to inspect its runs. Work held on purpose never makes a bot stuck. The web Stuck list,
Overview and the silent-computer line split the queue the same way. **Tasks not progressing** names visible tasks
and links their recovery diagnosis. Settings > Health shows the same summaries.

**Pull requests not progressing** names visible bot PRs that have remained conflicting for more than 12 hours,
or green and mergeable without a review for more than 12 hours. Each names the ticket, waiting age and whose
move it is: the author merges main, or the reviewer reviews. These clocks start when the existing GitHub sync
first observes the condition, survive repeated refreshes, and clear when it resolves. A new head starts a new
review wait. Older links acquire a clock on their next sync; Health performs no extra GitHub polling.

## What it checks

Once an hour, while it is on, the server checks the install in plain code (`backend/health_monitor.py`). No model runs:

| Check | Kind | What it means |
|---|---|---|
| `runner_outdated` | setup | An online computer is on an older release, cannot work with this one, or failed to update; or it holds queued work until its running turns finish so it can update (the finding names that work) |
| `runner_restarting` | setup | A computer a person pressed Restart for has said it is restarting for over 34 minutes (20 minutes still claiming, then up to 14 for its running turns); the finding names the turns still running and the work it holds |
| `computer_silent` | setup | A computer has not checked in for 10 minutes or more while work is queued for its bots |
| `limited_no_fallback` | setup | A bot's turns stopped on a subscription or spend limit in the last day (a model's temporary capacity does not count) and it has no fallback model: none of its own, and not the company's either (*Fall back when a plan runs out* is off in **Settings > Models**, no company provider is on another runtime than the bot's, or the bot turned fallback off or names a fallback tool with no model) |
| `default_login_limited` | setup | A computer's default login for an AI tool (the one a turn uses with no subscription) is out of quota: in the last day at least two turns on it stopped on a usage limit and none has completed since, and the provider gave no reset time or one still ahead. One finding per computer and tool; it goes away once a turn on that login completes or the reset time passes. Fallback hops and check-model turns count; a turn the provider refused for temporary model capacity does not (a check or task model's either), nor a turn on a subscription, nor one on a tool each bot signs in with its own key (`api_keys = "bot"`: Gemini CLI, pi). While it lasts, a check or review subtask on that login moves to the next check model instead of retrying ([A subtask on a check model](tasks.md#a-subtask-on-a-check-model)) |
| `check_limited` | setup | A bot's check or review subtask on another model ([A subtask on a check model](tasks.md#a-subtask-on-a-check-model)) has been held by that model's usage limit for 12 hours: its job is still queued and every turn on it since then stopped on the limit, as Tico retries it after each reset. The finding names the bot, the model, since when and the task (a private task is not named). One finding per check task; it goes away once a turn on it ends any other way, it completes, or the task is done, closed or declined |
| `run_hold` | setup | Tico held a bot after five runs in a row failed within a minute of starting ([Failed runs wait, then stop](how-it-works.md)). The finding names the bot and the kind of error (usage limit, session too large, sign-in, gateway, other); it goes away when the bot is released, or when its next try (Tico runs one on its own at growing intervals, 30 minutes to 8 hours) completes |
| `claim_failed` | setup | Tico could not start a queued run: an error happened while its claim checked or prepared it, so the run is held for a wait (a minute at first, doubling with each failed try, up to an hour) and Tico tries it again on its own ([Failed runs wait, then stop](how-it-works.md)). The finding names the bot and how many of its runs are held; it goes away when they start or leave the queue, and Release here or `hub bot release` frees them at once |
| `claim_check` | setup | A bot's own claim check (its budget, back-off or login as a whole) raised an error, so each claim passes the bot over and its queued runs wait while the other bots' runs go on. Tico runs the check again after a wait of a minute, doubling with each failure in a row, up to five minutes. The finding names the bot and the error class, and goes away when one of its runs starts or the bot has no queued runs |
| `new_sessions` | setup | A bot started more than two new sessions in the last 24 hours because its old one was not found on its computer, would not resume or compact, was interrupted, or was still too large to send after compacting ([Sessions](sessions.md#checkpoints-and-resume)). A first session, a person's Clear and a topic change do not count. One finding per bot; it goes away when the count falls to two or fewer |
| `turn_budget` | setup | Tico held a bot for starting more runs in an hour or a day than its run budget allows. The finding names the bot and the window; it goes away when the bot is released or its budget raised |
| `reviewer_holds_tasks` | setup | A bot with the Reviewer profile owns open tasks that are not review or QA requests; a Reviewer does not pull those by itself ([Profiles](how-it-works.md#profiles)). One finding per bot; it goes away when the tasks move or the profile changes |
| `responder_has_tasks` | setup | A bot with the Responder profile, which takes no tasks, has open tasks. One finding per bot |
| `doer_idle` | setup | A Doer had at least three runs in the last week and every one wrote nothing to Tico, so its profile may not fit its work. A bot made this week is left out. One finding per bot |
| `quiet_unworked` | setup | A bot's run was handed a deferred task over two hours ago, and the bot has not worked it since or said when to pick it up (`hub task later`); the finding names the bot and the task ([Leave work for the next run](using-tico.md#leave-work-for-the-next-run)) |
| `release_approval_wait` | setup | A release's publish approval (a `publish` approval whose payload names the `version` and `commit`) has been ready over 60 minutes with no decision (`TICO_RELEASE_APPROVAL_WAIT_MINUTES` on the server; `0` turns the check off). The finding names the version, how long it has waited and the person whose decision it is. One finding per approval: a re-cut release's new ask is its own, and it goes away once the person decides or the ask is withdrawn. Nothing approves it for them, and who may approve is unchanged |
| `stuck_setup` | setup | A bot has been in setup over an hour while work waits for it |
| `worktree_error` | setup | A task worktree failed on its computer |
| `push_stuck` | setup | A runner reports a checkout with commits it cannot push (diverged) |
| `cannot_fetch` | setup | A runner keeps failing to fetch a bot's repository |
| `queue_idle` | tico-bug | The job the next claim would hand out (a person's message before older work) has been queued 15 minutes or more (a person's message: 2 minutes) on an online computer that has room, no limit and no update or restart waiting, and none of the claim's own reasons to hold it (drained, worktrees being maintained, a lapsed lease still running, interrupted work awaiting review, a usage-limit cooldown, readiness not reported, a rejected sign-in, a subscription profile, a check model, a busy lane). A held bot's Health row names that reason instead. Its report says where as `Where: bot-N on runner-M`, the labels of the attached diagnostics bundle |
| `worktree_fallback` | tico-bug | A task's turn ran in the bot's checkout because its worktree hit Permission denied |
| `event_stream` | tico-bug | A runner lost its event stream three times in its last 50 error lines, not counting drops from 5 minutes before a server start (a deploy, an update, a restart) to 2 minutes after, nor drops the runner says were back within 30 seconds (a proxy or tunnel resetting the long-lived stream; the runner polls meanwhile and catches up on reconnect) |
| `readiness_403` | tico-bug | A runner's readiness report keeps being refused |
| `runner_route_latency` | tico-bug | Attempt input, renew or event requests exceed one second at p95 in the last fifteen minutes of recorded traffic; also shown on Health. Histograms are merged across minutes and callers, rather than averaging percentiles |
| `server_cpu` | tico-bug | The server averaged 40% CPU or more for an hour while hardly used |

Runner checks read the error lines each runner already sends with its heartbeat (`recent_errors`). The Health Monitor's
own [watcher](watchers.md), `software/health-scan`, runs on its computer every hour and reports a process or Docker
container using 50% CPU or 2 GB of memory in two runs in a row (`HEALTH_BUSY_CPU`, `HEALTH_BUSY_MB`), leaving out the
model CLIs that run turns.

Each finding has a **key** (what and where, on this install) and a **signature** (`hm-` and 12 hex digits: what, without
where, the same on every install). Each finding also has a **severity**, `warn` or `bad`: `bad` where the Health page says
bad for the same thing (a computer on a release this server cannot work with, whose bots are paused), `warn` otherwise.

Only something the Health Monitor has not been told of starts a run:

- **A new finding** opens a task for it.
- **A finding whose severity rises** (from `warn` to `bad`) wakes it on its task ("Worse now"), or opens a new task
  if that one is closed or declined.
- **A finding back after its task was finished** (done, closed or declined) counts as new: it wakes the Health Monitor
  on that task while it is only done ("Back after its task was finished"), or opens a new task once it is closed or
  declined.

Seeing a finding again only counts: each hourly scan that sees it adds one to its count and moves its last-seen time,
and nobody is woken. That holds while it stays there and when it goes away and comes back while its task is still
open; then the task gets a quiet note ("Seen again (N times, last ...)") the Health Monitor reads on its next turn
there. **Settings > Health** shows each finding as "seen N times, last ..." (`occurrences` and `last_seen_at` in
`GET /api/v2/health-monitor`, with its `severity`).

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
