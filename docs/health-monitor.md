# Check health

Check health keeps an install healthy without anyone having to look. It is a maintenance step: plain code, with no
model and no bot. It runs on each [Maintenance](maintenance.md) run, hourly by default in company time. Its switch and
Run now are part of Maintenance; the separate Health scan system routine is removed.

Settings > Health lists what it found.

Settings > Health also lists checks that belong to one feature, documented on that feature's page: public roadmap
suggestions with no receiving bot are in [Public roadmaps](roadmaps.md), and the Tico AI Gateway key (near or at its
monthly cap, not recognised, or not ready) is in [Tico AI Gateway](ai-gateway.md#what-the-card-says).

The **Turns** check shows each computer's running, starting, waiting and queued counts, with each
wait reason and oldest age. See [Turn counts](computers.md#turn-counts) for the start deadline, load brake and older-runner rules.

The **Company turns** check records when the company warning level is crossed.
It shows the company count, warning and stop levels, and when the incident opened or resolved.
See [Company turn levels](computers.md#company-turn-levels) for what counts, defaults, recovery and how an admin changes the levels.
This record does not provide the separate automatic fixer.

Health's **Stuck bots** check and `hub health check` name the bot, its queue split as `hub pipeline` splits it (jobs
that can start, jobs behind an interrupted run awaiting review, jobs held on purpose), the oldest age, why it cannot
start, and a fix or a command to inspect its runs. Work held on purpose never makes a bot stuck. The web Stuck list,
Overview and the silent-computer line split the queue the same way. **Tasks not progressing** names visible bot tasks
that nothing is set to move, the tasks Task Manager lists as stalled. Settings > Health shows the same summaries.

**Pull requests not progressing** names visible bot PRs that have remained conflicting for more than 12 hours,
or green and mergeable without a review for more than 12 hours. Each names the ticket, waiting age and whose
move it is: the author fixes conflicts or review changes, merges after approval, or moves the ticket on;
an open check names its reviewer even after a push. Completed human subtasks do not count as check verdicts.
Health keeps private check owners and verdicts hidden. When it cannot see all check evidence, it asks for a
review status check instead of recommending a merge or move on. These clocks start when the existing GitHub sync
first observes the condition, survive repeated refreshes, and clear when it resolves. A new head starts a new
review wait. Older links acquire a clock on their next sync; Health performs no extra GitHub polling.

## What it checks

Check health runs the plain-code checks below and the Health page's own checks: the queue (Stuck bots), refused queue
work, mail asks, bot history, computers offline, and the rest. Each check is tagged **urgent** or **noisy**:

- **Urgent** checks file on the run that first finds the problem: `push_stuck`, `worktree_error`, the database and
  backup checks, and auth.
- **Noisy** checks file only if the problem is still there on the next run, so a blip that clears first files nothing:
  `runner_outdated`, `slow` and `quota`.

Setup checks (each problem is one BotOps task): `runner_outdated`, `runner_restarting`, `computer_silent`,
`limited_no_fallback`, `default_login_limited`, `check_limited`, `run_hold`, `claim_failed`, `claim_check`,
`new_sessions`, `turn_budget`, `reviewer_holds_tasks`, `responder_has_tasks`, `doer_idle`, `quiet_unworked`,
`release_approval_wait`, `stuck_setup`, `worktree_error`, `push_stuck`, `cannot_fetch`.

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
- A fix only a person can make (approving a mail-sending ask, restarting their own computer) reaches them only as a
  Needs you card that BotOps raises on its task. Check health never writes to a person.
- A mail ask (the mail alerts setting, `mail_settings.alerts`) becomes a BotOps task. BotOps asks the owner through an
  approval, and the setting turns on when that approval is granted.

Each finding has a **key** (what and where, on this install) and a **signature** (`hm-` and 12 hex digits: what, without
where, the same on every install). Tico bug reports group by signature.

## Tico bugs

A Tico-bug problem files one ticket to the Tico team: one per problem, and at most 5 new tickets a day. The message is
written from the check alone (what it means, the fix in fixed words, the signature, the version and how often it
happened), with the redacted [diagnostics bundle](support.md). A finding's own text (which names computers and bots),
task text, message text and people's words are never sent. A busy process is named only when it is one of Tico's own;
anything else is "a process" or "a Tico container". When the problem comes back, a "Seen again" message goes on the
same ticket. The ticket is the owner's: replies show on their Support page.

The team rule `health_monitor_reports` (on by default; in Settings > Health and `PUT /api/v2/access/rules`) files
tickets without a Needs you card. When it is off, each report is an **Ask before reporting** approval that a person
reads first, sent with `hub support send --approval <id>` once approved. A declined or withdrawn approval clears the
report, so the next report of that problem asks again. `TICO_SUPPORT=off` turns reporting off with the rest of support.

## On the Tico team's side

A ticket starting `[Health Monitor]` carries its signature. HQ keeps the signature with each ticket, so the staff list
groups by it (`GET /v1/staff/tickets?signature=hm-...`). The Support bot groups tickets by signature: one engineering
task per signature, and every other install's ticket or repeat is a comment on it
(`templates/catalog/support/playbooks/tico-hq-tickets.md`). When a release ships the fix, the team replies once to every
open ticket with that signature and closes each (`POST /v1/staff/tickets/by-signature/close`, or
`software/hq-tickets close-signature hm-...`); each company reads the reply on its Support page.

## Where it shows

- The account menu and the status dot show only two states: "Can't reach Tico", and "Tico stopped / bots aren't
  running" (which includes no computer online). Health check lines are not in the menu or on the dot; they are listed
  only on Settings > Health, where each finding shows as "seen N times, last ...".
- The "Review" action for uncertain work is on the bot's page.

## Commands

    hub health findings        what Check health found, with each problem's report
    hub health report <key>    report a finding as a Tico bug, one ticket per problem
