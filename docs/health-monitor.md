# Check health

Check health keeps an install healthy without anyone having to look. It is a maintenance step: plain code, with no
model and no bot. It runs on each [Maintenance](maintenance.md) run, hourly by default in company time. Its switch and
Run now are part of Maintenance; the separate Health scan system routine is removed.

Settings > General > Health > Details lists what it found.

For a runner update blocked by changed files, Health and BotOps say when only flattened Tico links remain.
The runner checks their bytes and targets: these links are repaired on the next update, with no decision about
local work. Other changes still need a commit or stash. See [Updates](updates.md#a-mac-or-linux-checkout).

Settings > General > Health > Details also lists checks that belong to one feature, documented on that feature's page: public roadmap
suggestions with no receiving bot are in [Public roadmaps](roadmaps.md), and the Tico AI Gateway key (near or at its
monthly cap, not recognised, or not ready) is in [Tico AI Gateway](ai-gateway.md#what-the-card-says).

The **Turns** check shows each computer's running, starting, waiting and queued counts, with each
wait reason and oldest age. See [Turn counts](computers.md#turn-counts) for the start deadline, load brake and older-runner rules.

The **Company turns** check records when the company warning level is crossed.
It shows the company count, warning and stop levels, and when the incident opened or resolved.
See [Company turn levels](computers.md#company-turn-levels) for what counts, defaults, recovery and how an admin changes the levels.
This record does not provide the separate automatic fixer.

Health's **Stuck bots** check and `tico health check` name the bot, its queue split (jobs
that can start, jobs behind an interrupted run awaiting review, jobs held after repeated refusals until cooldown,
jobs held on purpose), the oldest age, why it cannot
start, and a fix or a command to inspect its runs. Work held on purpose never makes a bot stuck. The web Stuck list,
Company › Campus and the silent-computer line split the queue the same way. Settings > General > Health > Details
shows the same summaries.

**Tasks** shows the never-stuck check's hourly reading: whether every rule that holds at all times holds (each open task
waits on something; each bot's turn has a run, a wake or a next try), and how many tasks are on a retry, with a
manager, with a person or unable to run. It is bad when a rule is broken. See
[Rules that hold at all times](pipelines-v3.md#rules-that-hold-at-all-times).

A cloud machine asleep while all its jobs wait on purpose shows those waits as queue information. If any job needs
a fix, such as signing in its check model, Health marks the bot stuck even when an older task waits on purpose.
Ready jobs do not hide that fix, even at the company turn limit. If no job needs a fix, the company hold still shows.
Its last boot's load raises no reporting warning while it rests, even if it just contacted Tico. The queue
cutoff uses the next runnable job's age, including when readiness still needs a fresh report. Spending limits and
other holds still apply while that report is missing. Work that can run but stays asleep past that cutoff still
raises a problem; **Starting** means the machine is starting.

**Pull requests not progressing** names visible bot PRs that have stayed conflicting for more than 12 hours, or
green and mergeable for more than 12 hours. Each names the task, the wait and the move: its owner resolves the conflict,
or merges or says why it waits. These clocks start when GitHub sync first sees the condition, survive repeated
refreshes, and clear when it resolves. Health performs no extra GitHub polling.

**Needs you plate** names each person whose Needs you count is over 10 items (the number the Tasks page's You tab
shows): a warning over 10, bad over 25. It names the three bots asking most of that plate. An administrator sees
everyone's plate; anyone else sees only their own. A plate of 10 or fewer shows nothing.

## What it checks

Check health runs the plain-code checks below and the Health page's own checks: the queue (Stuck bots), refused queue
work, mail asks, bot history, computers offline, and the rest. Each check is tagged **urgent** or **noisy**:

- **Urgent** checks file on the run that first finds the problem: `push_stuck`, `worktree_error`, the database and
  backup checks, and auth.
- **Noisy** checks file only if the problem is still there on the next run, so a blip that clears first files nothing:
  `runner_outdated`, `slow` and `quota`.

Setup checks (each problem is one BotOps task): `runner_outdated`, `runner_restarting`, `computer_silent`,
`limited_no_fallback`, `default_login_limited`, `check_limited`, `run_hold`, `claim_failed`, `claim_check`,
`new_sessions`, `turn_budget`, `reviewer_holds_tasks`, `responder_has_tasks`, `doer_idle`, `stuck_setup`,
`worktree_error`, `push_stuck`, `cannot_fetch`.

Tico-bug checks (each problem is also reported to the Tico team): `queue_idle`, `worktree_fallback`, `event_stream`,
`readiness_403`, `runner_route_latency`, `server_cpu`.

Runner checks read the error lines each runner already sends with its heartbeat (`recent_errors`).

**The idle-CPU check runs in the runner**, hourly. Each runner posts its samples to `POST /api/v2/runners/idle-load`
(runner only). A process or Docker container is filed only when it is busy in two samples in a row. The model CLIs
that run turns are left out.

## Findings and tasks

- Each problem is one BotOps task. A repeat adds to the task's count and moves its last-seen time; it does not open a
  second task.
- When the problem clears, the task gets a "cleared" comment, and BotOps closes it.
- A finding with no BotOps bot is logged on the step result. No person gets a task from a health check.
- BotOps gets one grouped wake for changed health tasks, subject to Maintenance's shared wake limits. A refused wake
  waits for the next allowed run.
- A fix only a person can make (OKing mail alerts, restarting their own computer) reaches them only as an ask that
  BotOps adds to its task, in their Needs you. Check health never writes to a person.
- A mail ask (the mail alerts setting, `mail_settings.alerts`) becomes a BotOps task. BotOps asks the owner for an OK
  on it.

Each finding has a **key** (what and where, on this install) and a **signature** (`hm-` and 12 hex digits: what, without
where, the same on every install). Tico bug reports group by signature.

## Tico bugs

A Tico-bug problem files one ticket to the Tico team: one per problem, and at most 5 new tickets a day. The message is
written from the check alone (what it means, the fix in fixed words, the signature, the version and how often it
happened), with the redacted [diagnostics bundle](support.md). A finding's own text (which names computers and bots),
task text, message text and people's words are never sent. A busy process is named only when it is one of Tico's own;
anything else is "a process" or "a Tico container". When the problem comes back, a "Seen again" message goes on the
same ticket. The ticket is the owner's: replies show on their Support page.

The team rule `health_monitor_reports` (on by default; in Settings > General > Health > Details and `PUT /api/v2/access/rules`) files
tickets without asking anyone. When it is off, each report is an OK asked of the owner on a task, bound to exactly that
message: they read it first, and it is sent with `tico support send --approval <id>` once OK'd. An OK sent back or
withdrawn clears the report, so the next report of that problem asks again. `TICO_SUPPORT=off` turns reporting off with the rest of support.
A copy run from source (version `dev`: a checkout, a test, the never-stuck rig) files nothing unless
`TICO_HQ_URL` is set; Maintenance's run details say `source_copy`.

## On the Tico team's side

A ticket starting `[Health Monitor]` carries its signature. HQ keeps the signature with each ticket, so the staff list
groups by it (`GET /v1/staff/tickets?signature=hm-...`). The Support bot groups tickets by signature: one engineering
task per signature, and every other install's ticket or repeat is a comment on it
(`templates/catalog/support-owner/playbooks/tico-hq-tickets.md`). When a release ships the fix, the team replies once to every
open ticket with that signature and closes each (`POST /v1/staff/tickets/by-signature/close`, or
`software/hq-tickets close-signature hm-...`); each company reads the reply on its Support page.

## Where it shows

- The account menu and the status dot show only two states: "Can't reach Tico", and "Tico stopped / bots aren't
  running" (which includes no computer online). Health check lines are not in the menu or on the dot; they are listed
  only on Settings > General > Health > Details, where each finding shows as "seen N times, last ...".
- The "Review" action for uncertain work is on the bot's page.

## Commands

    tico health findings        what Check health found, with each problem's report
    tico health report <key>    report a finding as a Tico bug, one ticket per problem
