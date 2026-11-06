# System routines

System routines are the jobs Tico runs on its own, such as stale ticket reviews. Task rules close eligible done tasks. Both appear in the same list, separate from bot routines, which are [Automations](routines.md) now. Learn runs in [Maintenance](maintenance.md), with its switch and schedule there; its nightly timer and read-only Automations mirror are retired.

Open **Settings → System** (old `#/settings/rules` and `#/settings/routines` links land here too). The page has, top to bottom: **What Tico runs on its own** (this list), **Releases** and **Task Manager**. The same jobs and task rules show read-only on the Automations page. Everyone who can open Settings sees the flat list of built-in jobs and task rules, without group headings. The Task Manager's report shows its schedule as **Hourly on weekdays, 08:00–18:00**, and its row says when the Task Manager is paused.

Wake rules are automations now: **What wakes bots** (the built-in rules, read-only) and one **What wakes ...** automation per bot, team, task type, department or task, on the Automations page. A person's message, an ask, an answer, an approval, a needs-action flag, a reply and a gate verdict always wake.

**Releases** turns the daily release on or off, sets one or two times (company time), names the Release Manager and Technical Writer (or finds them by template), and shows the next and last release hour and what waits at Pending Release. Owners and admins change it; someone who moves Dev tickets gets **Release now** here (with a note field; a note asks even when nothing is pending) and on the board's Pending Release column.

**Task Manager** shows whether it is on, its last run, and each department's last check, item count and open check task. The owner pauses and resumes it.

Search by name, trigger (**WHEN**), action (**THEN**), or department. For example, searching `done` finds **Close 24h after done** and built-ins that act on done tasks. Searching `close` finds actions that close tasks.

Task rules, the built-in switches, schedules and run records described here are all available.

## What each row shows

| Part of the row | What it tells you |
|---|---|
| Name and one line | What the job or rule does. |
| **WHEN … THEN …** | Its trigger and action. Multiple conditions or actions join with **AND**. |
| Department tag | Exactly one department from the org chart's fixed list. For task rules, it controls editing permissions. |
| Schedule | How often it checks or runs, in words. Times use Pacific time. A task rule's schedule is separate from its untouched wait. |
| **Last run** | When it last finished (hover for the exact time) and the result in words, such as "9 checked, 2 suggestions". A job with no recorded run says "Not run yet". |
| **Next run** | When the job or rule runs next, from its schedule and its last run. **Off** means it is switched off. A dash means Tico cannot tell. |
| On or off | A switch if you may change it; otherwise its saved state. Housekeeping shows **Tico needs this to run** instead. |
| **AI** pill and spend | Shown on jobs that ask Decide: which route answered, and what the job has cost this month. |

A run that failed shows **Failed:** and a plain reason in red, for example "Failed: the database was busy". The reason names the kind of failure, never the error text, because error text can hold people's data.

A job that wakes every few seconds and finds nothing to do does not add a row each time. A quiet run with the same result as the last one moves that row's times forward, at most once every 5 minutes, so a 10-second job's last run can read up to 5 minutes ago. A failure with the same reason as the last row folds the same way: the row keeps the time of the first failure and counts the repeats. A job that does not apply here writes nothing when it has nothing to do. Built-ins and task rules share these run records. Tico keeps the last 50 records for each job or rule. Task-rule results include "2 tasks closed" or "No eligible done tasks".

The list refreshes when you open the tab and at most once a minute while it stays open.

## Who can change a row

| Row | Who can change it | Available controls |
|---|---|---|
| Task rule | Members of its department, including humans and bots in nested teams; the owner or an admin for any department | On or off, **Edit**, and schedule presets |
| **Stale ticket review**, **KPI stale asks**, **Ask go-ahead** | Owner or admin | On or off, schedule presets, **Run now** |
| **Task Manager report** | Owner or admin | On or off, schedule presets, **Run now** |
| **Start fresh when the topic changes** | Owner or admin | On or off |
| Housekeeping and other fixed built-ins | No switch or schedule editor on this page | Fixed operation; housekeeping cannot be switched off |

A department tag on a built-in does not grant permission to change it. Task rules have no separate view-only role within their department. People outside that department can read the list without gaining editing rights.

## Schedules and Run now

Choose from the presets offered on the row:

| Row | Presets |
|---|---|
| Task rules, **KPI stale asks**, **Ask go-ahead** | Every 15 minutes, Hourly, Every 4 hours, Daily at a time |
| **Task Manager report** | Hourly on weekdays 08:00 to 18:00 (the default), Hourly, Every 4 hours, Daily at a time |
| **Stale ticket review** | Hourly, Every 4 hours, Daily at a time |

Times use company time (Pacific). Leave the time field or press Enter to save it. These rows offer no cron expressions or custom intervals.

Task roundup is now [Maintenance's Find stuck step](maintenance.md). Its old switch, schedule and read-only Automations mirror are retired. The latest saved switch carries over at cutover. Use `hub maintenance run --step find_stuck` to run it now; `POST /api/v2/system-routines/task_roundup/run` also runs that step and returns Maintenance's `{run, started}` response.

**Run now** appears only on the scheduled built-ins in the permissions table. Switch the job on first. It runs through the same checks and caps as its scheduled run, then shows the result on the row. A job asking Decide shows **Running…** while it works. Task rules, **Start fresh when the topic changes** and **Stalled task check** have no **Run now** button.

The retired `learnings` Run now endpoint starts Maintenance's Learn step and returns `{run, started}`. Learn is off by default; its switch stays in effect for Run now.

**Auto dream** is not a System routine. It is Maintenance's Dream step, once a day, for up to "Most bots per run" bots (three by default). Its switch and "Most bots per run" are in Automations › Maintenance. See [Learnings](learnings.md#dream).

## Task rules

The list starts with one enabled task rule: **Close 24h after done**, tagged **Engineering**. Its row reads **WHEN a task has been done and untouched for 24 hours THEN close it**. It checks every 15 minutes by default.

The rule applies to eligible tasks across the company. Its Engineering tag controls who may edit the rule; it does not limit which tasks it closes.

A task must be in a done-indicating state and untouched for the full 24 hours. Any comment, reopen, or edit restarts the wait. A reopened task must be done again before it can qualify.

The rule skips tasks that are:

- Waiting on a person.
- Snoozed.
- Pinned with a `pin` or `pinned` task tag or legacy label.
- Carrying an open question or pending approval.

Sidebar bookmarks for filtered task views are not task pins and do not protect tasks from this rule.

Each pass closes at most 50 tasks across all task rules. Every automatic close adds one task-history note naming the rule, such as `Closed by rule 'Close 24h after done'`.

Reopen a task to undo the close. The rule never deletes tasks. Switching it **Off** saves that state and stops further closes by that rule, including after a server restart. Other enabled rules continue to run.

**New task rule** defaults to your department. **Edit** lets an authorized member change a rule's name, department, untouched wait, and on/off state. The wait accepts 1 to 8760 whole hours. Use **Save rule** to save these fields; the row has the separate schedule picker described above.

The department picker uses only the org chart's fixed department list; it accepts no free-text tags. A rule has exactly one department tag. To move a rule to another department, you need permission in both its current and destination departments. The server checks both.

Only the done-and-untouched trigger and close action are available for task rules. There are no per-task overrides. Editing a task rule does not change a bot's routines.

## Which AI answered

**Stale ticket review** asks Decide, Tico's question-answering model. Decide tries the [Tico AI Gateway](ai-gateway.md) first, then your own key, then your company's AI provider. This page and the list change none of that. They only show which route answered. Learn also asks Decide; its counts and cost appear in Maintenance.

| Pill | Meaning |
|---|---|
| **AI: Gateway** | The Tico AI Gateway answered. |
| **AI: Company key** | Your own key answered. |
| **AI: Company AI** | Your company's AI provider answered. |
| **Uses AI** | The job has not asked Decide yet. |

When the Gateway did not answer, a line under the pill says what ran instead and why, for example **Ran on your own key: Gateway unavailable** or **Ran on company AI: Gateway refused the key**. The pill follows the most recent run that asked Decide. A run that found nothing to ask does not change it.

**$ this month** adds up what the job's Decide calls cost since the first of the month (UTC). The stale review shares the team's monthly Decide cap with the other Decide users, so the figure is one slice of that cap.

## Task Manager report

**Task Manager report** (key `task_manager_report`) builds one report per department in plain code and wakes the
[Task Manager](task-manager.md) only for a department that has something. Its run record reads "3 departments, 7 items"
or "Nothing to do". The default preset runs hourly on weekdays between 08:00 and 18:00 in the team's time zone; a weekend
or evening tick does not run. It finds nothing while the Task Manager bot is paused. Details, the checks and the
`hub task-manager` commands are on [Task Manager](task-manager.md).

## The jobs

Built-ins appear in one flat list. This table gives their default schedules; saved choices replace the defaults on controllable rows.

The list shows only the jobs that can run on this server. A job whose feature is not set up is left out here and on
Automations (its rows, runs and counts) until it is: the **only when** column says what each one needs. Nothing is
deleted, so the row comes back by itself. Every job records its runs.

Check health runs as a [Maintenance](maintenance.md) step. It has no separate System routines row or timer.

| Job | What it does | Default schedule | Only when |
|---|---|---|---|
| **Start assigned tasks** | Starts eligible assigned work on a free computer turn; respects task caps, usage holds, and computer capacity. Each start is one run. | When a turn is free | |
| **Stale ticket review** (AI) | Finds open tasks that look finished or forgotten and suggests to each owner what to do. See [Stale ticket review](tasks.md#stale-ticket-review). | Hourly |  |
| **KPI stale asks** | Asks a KPI's keeper for a reading when it goes out of date, and a goal's owner when a KPI goes off track. | Hourly |  |
| **Task Manager report** | Looks for stalled tasks, overdue asks, review tickets without a PR, merged work whose step did not move and similar, and opens one "Task check" task per department for the [Task Manager](task-manager.md) only when there is something. Nothing to report is a quiet run: no task, no model call. | Hourly on weekdays, 08:00 to 18:00 |  |
| **Books check** | Reads the [Bookkeeper](bookkeeper.md)'s Finance work queue in plain code and opens one "Books check" task for it only when something is new. Nothing new is a quiet run: no task, no model call. Opens nothing while the Bookkeeper is paused. | Hourly | a Bookkeeper bot |
| **Books watch** | Reads runway, unusual spend, duplicate charges, price increases and unused subscriptions in plain code and opens one "Watch" task for the [Bookkeeper](bookkeeper.md) only when something new is found. Not a switch: the Bookkeeper's pause stops it. | Daily | a Bookkeeper bot |
| **Rules digest** | Once a week, opens the Bookkeeper's "Rules digest" task for the posting rules made in the last seven days. No new rules is a quiet run. Not a switch. | Weekly | a Bookkeeper bot |
| **Due reminders** | Tells a bot when one of its tasks reaches its due time. | Every 10 seconds |  |
| **Ask go-ahead** | A bot's question past its deadline goes ahead on its default answer, or expires. | Hourly |  |
| **Earlier messages** | Tells each manager of a bot once, in a direct message, when the bot's Home still holds closed history. Plain code: no model is called, and nothing new is a quiet run. Asks nobody answered are on the bot's task roundup. See [Thread helper](thread-helper.md). Not a switch. | Every 15 minutes |  |
| **Email digest** | Emails a person once an hour the threads they follow on digest that have new messages: each title, its unread count and a link, never the messages. Nothing new is a quiet run. Each person turns theirs off under Email digests. See [Thread helper](thread-helper.md). Not a switch. | Hourly |  |
| **Approval expiry** | Takes approvals and asks nobody will act on out of Needs you. | Hourly |  |
| **Unanswered questions** | On weekday mornings, questions unanswered for two business days go back to their bots as lines on the bots' task roundup, never as a wake. | Hourly |  |
| **Stall checks** | Every minute: allocates waiting tickets, closes timed-out checks, drops queued work its bot can no longer read, and alerts once when an Engineering task has been blocked over four hours (to its manager, or else its requester). It does not wake a bot about one stalled task: the task roundup lists those. | Every minute |  |
| **Task roundup (retired)** | Grouped task lists now run through [Maintenance Find stuck](maintenance.md), with its switch, schedule and wake limits. | Maintenance schedule |  |
| **Turn fixer** | Frees turns proven dead (lease lost and process gone, or stopped before they started) with a note on the task; holds a dead turn that may have pushed, sent or paid for BotOps instead of retrying it; keeps one Health finding per busy-turns incident and hands what it cannot fix to BotOps. See [Computers](computers.md#the-turn-fixer). | Every 10 seconds |  |
| **Waiting checks** | Turns a bot's plain ask to a person into a small task. It no longer sends a waiting task back to work; a waiting task that waits on nothing is listed on the task roundup after a day. | Hourly |  |
| **Snooze ends** | Brings snoozed tasks back when their date comes and starts deferred runs that are due. | Every 10 seconds |  |
| **Go-live expiry** | Ends a go-live waiting for a computer that never reports. | Every 10 minutes |  |
| **Page monitors** | Reads each competitor page the Librarian or owner watches (at most 50 a day, the longest unchecked first) and files a market insight only when one changed. See [Market watch](market-watch.md). | Daily | a watched page |
| **Goal colours** | Works out every goal's colour again as its data ages. | Hourly |  |
| **Gateway key check** | Asks the Tico AI Gateway whether this server's key still works and how much of the month's cap is used. | Hourly | a Gateway key |
| **Billing check** | Reads HQ's nightly billing check for the owner's Health page. | Hourly | HQ's staff key (Tico's own server) |
| **Updates** | Asks people and bots for today's updates, one at a time. | Every 10 seconds |  |
| **GitHub sync** | Catches up on pull requests, merges and releases whose notice from GitHub never arrived. | Every 10 seconds |  |
| **Repository and setup waits** | Finishes a bot waiting for its repository or a worktree, and tells a person about a bot stuck in setup. | Every 10 seconds |  |
| **Release and feed check** | Checks for a new Tico release and fetches subscribed updates. | Every 10 seconds; a new release is looked for every 6 hours |  |
| **Directory sync** | Brings people in from the company directory. | Hourly, or the interval you set; "Off: no directory connected" until you connect one | a directory connected |
| **Meeting notes sync** | Brings in meeting notes from each person's connected Granola. | Every 30 seconds | a Granola connection |
| **Document sync** | Refreshes linked documents from their sources. | Every 5 seconds | a linked document |
| **Start fresh when the topic changes** | Starts a fresh session, like Clear, when a direct-chat message changes the topic. | When it happens |  |
| **Paused bot replies** | Lets bot replies the loop guard paused in a thread go, as many as its hour has room for. | Every 10 seconds | |
| **Thread retention** | Deletes messages past the time their thread keeps them, in short batches. | Every 15 minutes | |
| **Production checks** | Reads a few production invariants in plain code and shows each failing one on Health. | Every 15 minutes | |
| **Thread routing** (AI) | Wakes the bots a person's unaddressed thread message is for, three at most. | As messages arrive | |
| **Thread ranking** (AI) | Gives each thread with new messages in the last 14 days a level from 1 to 10, for the threads API's `sort=important` order, which no screen sends yet. In that order a thread that needs attention comes first, whatever its level. A thread is ranked at most once an hour, and at most 20 threads a pass and 40 an hour. A thread never ranked sits at 5; a failed call keeps its last level. That order otherwise leaves out level 1 threads, threads of finished tasks, and task threads no person owns or asked for. It also stops for the day once the server's Decide calls reach 80% of the bots' daily call budget, so the checks that act on tasks keep room. | Every 5 minutes | |
| **Bot thread digest** | Starts one run an hour for each bot with new messages in the threads it follows on digest. | Hourly | |
| **Nightly memory tidy** | Proposes memory and knowledge edits for the pilot bots. | Nightly at 03:30 | |
| **Tico company heartbeat** | Tells app.tico.dev this server's version once a day. | Daily | a linked Tico company |
| **Scheduled update** | Updates a hosted server to the release Tico support scheduled, once no bot turn runs. | Every minute | a hosted server |
| **Silent run check** | Marks a run that has gone silent and ends a run past this server's time limit. | Every minute | |
| **Run housekeeping** | Ends runs whose lease ran out, settles interrupted runs, lifts usage limits and cooled holds, gives an active bot with no computer one, and settles missed run notes. | Every 10 seconds | |
| **Shipped merges** | Marks a merged pull request shipped once the running release has it. | Every 10 seconds | |
| **Task loop alerts** | Tells a task's owner and requester once a day when its status keeps changing back and forth. | Every 10 seconds | |
| **Queue check** | Notes why a bot's queued work is not starting, for the bot's row and Health. | Every 10 seconds | |
| **Merge and release waits** | Wakes a task waiting for a merge or release that already happened, once. | Every 10 seconds | |
| **Daily release** | At the release hour, sends the Release Manager one task for what is at Pending Release. | At the release hour | the daily release on |
| **Company page editions** | Starts the Company page editions that came due and sends waiting reader notices. | Every 10 seconds | a Company page that is not Closed |
| **Outside text check** | Screens outside text a restart left waiting. | Every 10 seconds | |
| **Finance upkeep** | Posts Finance's waiting events, Gateway usage and prepaid costs, ends expired holds and finished approvals, watches documents and opens next month's period. | Every 10 seconds | |
| **Hourly deadlines** | Turns off a webhook that failed for five days, retires replaced computers, and hands a person a support ticket or screened item that waited an hour. | Hourly | |
| **Strategy note check** | Starts the Strategy Bot's Monday note above Passive and pauses it at Passive. | Hourly | a Strategy Bot |
| **Tico Cloud check** | Reads this company's Tico Cloud access from HQ and renews its file hosting keys. | Every minute on Tico Cloud, else hourly | a Gateway key |
| **Personal alerts** | Sends people's opt-in alerts through HQ paging, within their quiet hours and the monthly caps. | Every 10 seconds | someone set up alerts |
| **Workspace key check** | Checks a pending or failing Google Workspace key again until it works or its hour is up. | Every 10 seconds | a Workspace key |
| **Calls and live meetings** | Routes live-meeting windows a restart left behind, and ends call access for people and bots who lost it. | Every 10 seconds | |
| **Inbound mail** | Brings in mail and bills from HQ, and deletes inbound mail older than 90 days. | Every 15 seconds | |
| **Gateway webhooks** | Brings in webhooks and Tico notices waiting at Tico Gateway. | Every 15 seconds with a relay, else every minute | a Gateway key |
| **Physical mail** | Reads and routes letters the mail relay and the mail-intake inbox kept. | Every minute | physical mail received |
| **Letters sync** | Sends approved letters and follows each one until it is delivered. | Every 30 seconds | a letter sent |
| **Sleep when idle** | Asks HQ to put this server to sleep once nobody has used it for the set time. | Every 5 minutes; every minute on a cell | sleep set, or a cell |
| **Cloud machines** | Starts bots' cloud machines for work and stops them when idle. | Every 10 seconds | a Tico Cloud company |

Every row shows its last run. **Meeting notes sync** and **Document sync** record one run per pass, **Thread routing** one per pass of waiting messages, **Start fresh when the topic changes** one per check, and **Start assigned tasks** one per start.

Not listed, because they are not timed jobs of their own: the automations engine (its runs are each automation's own), the workers that send webhooks and sync Finance's bank, books and Gateway (they act on queued work and show it on that work's page), the one-off backfills a release runs once, and the restart of a worker that stopped.

## If a row looks wrong

- **"Not run yet" on a job that should have run.** Wait for one schedule period to pass, then reload the tab. The list shows a run only after the job finishes.
- **Failed in red.** Read the reason. A busy database clears on the next run. A repeated "Tico refused it" or "Decide did not answer" means the job is failing each time; tell your Tico admin.
- **Uses AI with no route.** The job has not asked Decide yet, for example because nothing needed a decision.

<!-- Sources checked 2026-11-05 at main 1ed46c055: docs/maintenance.md; backend/maintenance_steps/dream.py; backend/dream_auto.py; ui/app/automations-maintenance.js (Auto dream). Earlier, 2026-10-24 at PR #1293 head f0fcafdf65c082a408f4524e379cc091109a2b3c: ui/app/settings-recurring.js; backend/task_rules.py; backend/system_routines.py; backend/models.py (TaskRuleUpdate). Task-rules spec v1, acceptance 11. -->
