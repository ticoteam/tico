# System routines

System routines are the jobs Tico runs on its own, such as the General Manager report. They appear in one list, separate from bot routines, which are [Automations](routines.md) now. Learn runs in [Maintenance](maintenance.md), with its switch and schedule there; its nightly timer and read-only Automations mirror are retired.

Open **Automations → System jobs** (`#/automations/system-jobs`), a built-in row beside Maintenance; old `#/settings/system`, `#/settings/rules` and `#/settings/routines` links land there too. **General Manager** is on the General Manager's **Settings** tab. The same jobs also show read-only on the Automations page. Everyone who can open Automations sees the flat list of built-in jobs, without group headings. The General Manager's report shows its schedule as **Hourly on weekdays, 08:00–18:00**, and its row says when the General Manager is paused.

Wake rules are automations now: **What wakes bots** (the built-in rules, read-only) and one **What wakes ...** automation per bot, team, department or task, on the Automations page. A task that is a bot's turn, a person's message, an ask, an answer, an OK asked or given, a needs-action flag and a reply always wake.

**General Manager** shows whether it is on, its last run, and each department's last check, item count and open check task. The owner pauses and resumes it.

Search by name, trigger (**WHEN**), action (**THEN**), or department.

## What each row shows

| Part of the row | What it tells you |
|---|---|
| Name and one line | What the job or rule does. |
| **WHEN … THEN …** | Its trigger and action. Multiple conditions or actions join with **AND**. |
| Department tag | Exactly one department from the org chart's fixed list. |
| Schedule | How often it checks or runs, in words. Times use Pacific time. |
| **Last run** | When it last finished (hover for the exact time) and the result in words, such as "9 checked, 2 suggestions". A job with no recorded run says "Not run yet". |
| **Next run** | When the job runs next, from its schedule and its last run. **Off** means it is switched off. A dash means Tico cannot tell. |
| On or off | A switch if you may change it; otherwise its saved state. Housekeeping shows **Tico needs this to run** instead. |
| **AI** pill and spend | Shown on jobs that ask Decide: which route answered, and what the job has cost this month. |

A run that failed shows **Failed:** and a plain reason in red, for example "Failed: the database was busy". The reason names the kind of failure, never the error text, because error text can hold people's data.

A job that wakes every few seconds and finds nothing to do does not add a row each time. A quiet run with the same result as the last one moves that row's times forward, at most once every 5 minutes, so a 10-second job's last run can read up to 5 minutes ago. A failure with the same reason as the last row folds the same way: the row keeps the time of the first failure and counts the repeats. A job that does not apply here writes nothing when it has nothing to do. Tico keeps the last 50 records for each job.

The list refreshes when you open the tab and at most once a minute while it stays open.

## Who can change a row

| Row | Who can change it | Available controls |
|---|---|---|
| **KPI stale asks**, **Books check** | Owner or admin | On or off, schedule presets, **Run now** |
| **General Manager report** | Owner or admin | On or off, schedule presets, **Run now** |
| **Start fresh when the topic changes** | Owner or admin | On or off |
| Housekeeping and other fixed built-ins | No switch or schedule editor on this page | Fixed operation; housekeeping cannot be switched off |

A department tag on a built-in does not grant permission to change it.

## Schedules and Run now

Choose from the presets offered on the row:

| Row | Presets |
|---|---|
| **KPI stale asks**, **Books check** | Every 15 minutes, Hourly, Every 4 hours, Daily at a time |
| **General Manager report** | Hourly on weekdays 08:00 to 18:00 (the default), Hourly, Every 4 hours, Daily at a time |

Times use company time (Pacific). Leave the time field or press Enter to save it. These rows offer no cron expressions or custom intervals.

**Run now** appears only on the scheduled built-ins in the permissions table. Switch the job on first. It runs through the same checks and caps as its scheduled run, then shows the result on the row. A job asking Decide shows **Running…** while it works. **Start fresh when the topic changes** has no **Run now** button.

The retired `learnings` Run now endpoint starts Maintenance's Learn step and returns `{run, started}`. Learn is off by default; its switch stays in effect for Run now.

**Auto dream** is not a System routine. It is Maintenance's Dream step, once a day, for up to "Most bots per run" bots (three by default). Its switch and "Most bots per run" are in Automations › Maintenance. See [Learnings](learnings.md#dream).

## Which AI answered

A job that asks Decide, Tico's question-answering model, shows which route answered. Decide tries the [Tico AI Gateway](ai-gateway.md) first, then your own key, then your company's AI provider. This page and the list change none of that. They only show which route answered. Learn also asks Decide; its counts and cost appear in Maintenance.

| Pill | Meaning |
|---|---|
| **AI: Gateway** | The Tico AI Gateway answered. |
| **AI: Company key** | Your own key answered. |
| **AI: Company AI** | Your company's AI provider answered. |
| **Uses AI** | The job has not asked Decide yet. |

When the Gateway did not answer, a line under the pill says what ran instead and why, for example **Ran on your own key: Gateway unavailable** or **Ran on company AI: Gateway refused the key**. The pill follows the most recent run that asked Decide. A run that found nothing to ask does not change it.

**$ this month** adds up what the job's Decide calls cost since the first of the month (UTC). The job shares the team's monthly Decide cap with the other Decide users, so the figure is one slice of that cap.

## General Manager report

**General Manager report** (key `task_manager_report`) builds one report per department in plain code and wakes the
[General Manager](general-manager.md) only for a department that has something. Its run record reads "3 departments, 7 items"
or "Nothing to do". The default preset runs hourly on weekdays between 08:00 and 18:00 in the team's time zone; a weekend
or evening tick does not run. It finds nothing while the General Manager bot is paused. Details, the checks and the
`tico general-manager` commands are on [General Manager](general-manager.md).

## The jobs

Built-ins appear in one flat list. This table gives their default schedules; saved choices replace the defaults on controllable rows.

The list shows only the jobs that can run on this server. A job whose feature is not set up is left out here and on
Automations (its rows, runs and counts) until it is: the **only when** column says what each one needs. Nothing is
deleted, so the row comes back by itself. Every job records its runs.

Check health runs as a [Maintenance](maintenance.md) step. It has no separate System routines row or timer.

The jobs that must happen within seconds (due times, recovery and outside polls) are steps of the [Heartbeat](heartbeat.md), one row here. Each step's last run, result, day counts and last error are on the Heartbeat's own screen on Automations.

| Job | What it does | Default schedule | Only when |
|---|---|---|---|
| **Heartbeat** | Runs its steps in one short beat: the [never-stuck check](tasks.md#when-a-bot-stops-early), which moves and wakes tasks; updates and digests; the turn fixer, run housekeeping and the other recovery checks; GitHub sync, repository waits and personal alerts. See [Heartbeat](heartbeat.md). | Every 10 seconds, or the interval set there |  |
| **KPI stale asks** | Asks whoever keeps a KPI for a reading when it goes out of date, and a goal's owner when a KPI goes off track. | Hourly |  |
| **General Manager report** | Looks for quiet tasks, overdue asks, held bots and similar, and opens one "Task check" task per department for the [General Manager](general-manager.md) only when there is something. Nothing to report is a quiet run: no task, no model call. | Hourly on weekdays, 08:00 to 18:00 |  |
| **Books check** | Reads the [Finance Owner](bookkeeper.md)'s Finance work queue in plain code and opens one "Books check" task for it only when something is new. Nothing new is a quiet run: no task, no model call. Opens nothing while the Finance Owner is paused. | Hourly | a Finance Owner bot |
| **Books watch** | Reads runway, unusual spend, duplicate charges, price increases and unused subscriptions in plain code and opens one "Watch" task for the [Finance Owner](bookkeeper.md) only when something new is found. Not a switch: the Finance Owner's pause stops it. | Daily | a Finance Owner bot |
| **Rules digest** | Once a week, opens the Finance Owner's "Rules digest" task for the posting rules made in the last seven days. No new rules is a quiet run. Not a switch. | Weekly | a Finance Owner bot |
| **Page monitors** | Reads each competitor page the Librarian or owner watches (at most 50 a day, the longest unchecked first) and files a market insight only when one changed. See [Market watch](market-watch.md). | Daily | a watched page |
| **Goal colours** | Works out every goal's colour again as its data ages. | Hourly |  |
| **Gateway key check** | Asks the Tico AI Gateway whether this server's key still works and how much of the month's cap is used. | Hourly | a Gateway key |
| **Billing check** | Reads HQ's nightly billing check for the owner's Health page. | Hourly | HQ's staff key (Tico's own server) |
| **Release and feed check** | Checks for a new Tico release and fetches subscribed updates. | Every 10 seconds; a new release is looked for every 6 hours |  |
| **Directory sync** | Brings people in from the company directory. | Hourly, or the interval you set; "Off: no directory connected" until you connect one | a directory connected |
| **Meeting notes sync** | Brings in meeting notes from each person's connected Granola. | Every 30 seconds | a Granola connection |
| **Document sync** | Refreshes linked documents from their sources. | Every 5 seconds | a linked document |
| **Start fresh when the topic changes** | Starts a fresh session, like Clear, when a direct-chat message changes the topic. | When it happens |  |
| **Thread retention** | Deletes messages past the time their thread keeps them, in short batches. | Every 15 minutes | |
| **Skill usage** | Folds skill uses into daily counts for Library › Skills and deletes raw uses, and the daily counts of skills outside the directory, older than 90 days. | Hourly | |
| **Tasks reading** | Checks the rules that hold at all times over every open task (each waits on something, each bot's turn has a run, a wake or a next try) and counts the tasks on a retry, with a manager, with a person or unable to run, for Health. Plain code: no model, no wake. | Hourly | |
| **Production checks** | Reads a few production invariants in plain code and shows each failing one on Health. | Every 15 minutes | |
| **Skill screening** (AI) | Screens each proposed skill and update with plain-code patterns and Decide High before an admin can approve it, a few a pass, oldest first. Mandatory: no switch, and no other model answers in its place. See [Skills](skills.md#screening). | As skills are proposed | a skill proposal |
| **Thread ranking** (AI) | Gives each thread with new messages in the last 14 days a level from 1 to 10, for the threads API's `sort=important` order, which no screen sends. In that order a thread that needs attention comes first, whatever its level. A thread is ranked at most once an hour, and at most 20 threads a pass, up to ten threads in one call, and 40 calls an hour. A thread never ranked sits at 5; a failed call keeps its last level. That order otherwise leaves out level 1 threads, threads of finished tasks, and task threads no person owns or asked for. It also stops for the day once the server's Decide calls reach 80% of the bots' daily call budget, so the checks that act on tasks keep room. | Every 5 minutes | |
| **Tico company heartbeat** | Tells app.tico.dev this server's version once a day. | Daily | a linked Tico company |
| **Scheduled update** | Updates a hosted server to the release Tico support scheduled, once no bot turn runs. | Every minute | a hosted server |
| **Outside text check** | Screens outside text a restart left waiting. | Every 10 seconds | |
| **Finance upkeep** | Posts Finance's waiting events, Gateway usage and prepaid costs, ends expired holds, watches documents and opens next month's period. | Every 10 seconds | |
| **Hourly deadlines** | Turns off a webhook that failed for five days, retires replaced computers, hands a person a support ticket or screened item that waited an hour, and keeps unsent a held write nobody decided in time. | Hourly | |
| **Strategy note check** | Starts the Strategy Owner's Monday note above Passive and pauses it at Passive. | Hourly | a Strategy Owner |
| **Tico Cloud check** | Reads this company's Tico Cloud access from HQ and renews its file hosting keys. | Every minute on Tico Cloud, else hourly | a Gateway key |
| **Workspace key check** | Checks a pending or failing Google Workspace key again until it works or its hour is up. | Every 10 seconds | a Workspace key |
| **Inbound mail** | Brings in mail and bills from HQ, and deletes inbound mail older than 90 days. | Every 15 seconds | |
| **Gateway webhooks** | Brings in webhooks and Tico notices waiting at Tico Gateway. | Every 15 seconds with a relay, else every minute | a Gateway key |
| **Physical mail** | Reads and routes letters the mail relay and the mail-intake inbox kept. | Every minute | physical mail received |
| **Letters sync** | Sends approved letters and follows each one until it is delivered. | Every 30 seconds | a letter sent |
| **Sleep when idle** | Asks HQ to put this server to sleep once nobody has used it for the set time. | Every 5 minutes; every minute on a cell | sleep set, or a cell |
| **Cloud machines** | Starts bots' cloud machines for work and stops them when idle. | Every 10 seconds | a Tico Cloud company |

Every row shows its last run. **Meeting notes sync** and **Document sync** record one run per pass, and **Start fresh when the topic changes** one per check.

Not listed, because they are not timed jobs of their own: the automations engine (its runs are each automation's own), the workers that send webhooks and sync Finance's bank, books and Gateway (they act on queued work and show it on that work's page), the one-off backfills a release runs once, and the restart of a worker that stopped.

## If a row looks wrong

- **"Not run yet" on a job that should have run.** Wait for one schedule period to pass, then reload the tab. The list shows a run only after the job finishes.
- **Failed in red.** Read the reason. A busy database clears on the next run. A repeated "Tico refused it" or "Decide did not answer" means the job is failing each time; tell your Tico admin.
- **Uses AI with no route.** The job has not asked Decide yet, for example because nothing needed a decision.

<!-- Sources checked 2026-11-10 on chris/pipelines-v3-cutover-docs: backend/system_routines.py (JOBS, CONTROLS); backend/heartbeat.py; backend/wake_rules.py (ALWAYS_WAKE); backend/task_rules.py. -->
