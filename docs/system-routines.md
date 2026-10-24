# System routines

System routines are the jobs Tico runs on its own, such as stale ticket reviews and nightly learnings. Task rules close eligible done tasks. Both appear in the same list, separate from [bot routines](routines.md), which belong to a bot and open a task for it.

Open **Settings → Routines**. The **Routines** card ("What Tico runs on its own") sits above **Bot routines**. Everyone who can open Settings sees its flat list of built-in jobs and task rules, without group headings.

Search by name, trigger (**WHEN**), action (**THEN**), or department. For example, searching `done` finds **Close 24h after done** and built-ins that act on done tasks. Searching `close` finds actions that close tasks.

Task rules are **Unreleased**. The built-in switches, schedules, and run records described here are already available.

## What each row shows

| Part of the row | What it tells you |
|---|---|
| Name and one line | What the job or rule does. |
| **WHEN … THEN …** | Its trigger and action. Multiple conditions or actions join with **AND**. |
| Department tag | Exactly one department from the org chart's fixed list. For task rules, it controls editing permissions. |
| Schedule | How often it checks or runs, in words. Daily times use Pacific time. A task rule's schedule is separate from its untouched wait. |
| **Last run** | When it last finished (hover for the exact time) and the result in words, such as "9 checked, 2 suggestions". A job with no recorded run says "Not run yet". |
| **Next run** | When the job or rule runs next, from its schedule and its last run. **Off** means it is switched off. A dash means Tico cannot tell. |
| On or off | A switch if you may change it; otherwise its saved state. Housekeeping shows **Tico needs this to run** instead. |
| **AI** pill and spend | Shown on jobs that ask Decide: which route answered, and what the job has cost this month. |

A run that failed shows **Failed:** and a plain reason in red, for example "Failed: the database was busy". The reason names the kind of failure, never the error text, because error text can hold people's data.

A job that wakes every few seconds and finds nothing to do does not add a row each time. A quiet run with the same result as the last one moves that row's times forward, at most once a minute. Built-ins and task rules share these run records. Tico keeps the last 50 records for each job or rule. Task-rule results include "2 tasks closed" or "No eligible done tasks".

The list refreshes when you open the tab and at most once a minute while it stays open.

## Who can change a row

| Row | Who can change it | Available controls |
|---|---|---|
| Task rule | Members of its department, including humans and bots in nested teams; the owner or an admin for any department | On or off, **Edit**, and schedule presets |
| **Stale ticket review**, **Nightly learnings**, **Still wanted?**, **KPI stale asks**, **Ask go-ahead** | Owner or admin | On or off, schedule presets, **Run now** |
| **Health scan** | Owner only | On or off, schedule presets, **Run now** |
| **Start fresh when the topic changes** | Owner or admin | On or off |
| Housekeeping and other fixed built-ins | No switch or schedule editor on this page | Fixed operation; housekeeping cannot be switched off |

A department tag on a built-in does not grant permission to change it. Task rules have no separate view-only role within their department. People outside that department can read the list without gaining editing rights.

**Health scan** finds nothing while the Health Monitor itself is off in **Settings → Health**.

## Schedules and Run now

Choose from the presets offered on the row:

| Row | Presets |
|---|---|
| Task rules, **Still wanted?**, **KPI stale asks**, **Health scan**, **Ask go-ahead** | Every 15 minutes, Hourly, Every 4 hours, Daily at a time |
| **Stale ticket review** | Hourly, Every 4 hours, Daily at a time |
| **Nightly learnings** | Nightly at a time; initially 03:00 |

Daily times use Pacific time. Leave the time field or press Enter to save it. These rows offer no cron expressions or custom intervals.

**Run now** appears only on the six scheduled built-ins in the permissions table. Switch the job on first. It runs through the same checks and caps as its scheduled run, then shows the result on the row. A job asking Decide shows **Running…** while it works. Task rules and **Start fresh when the topic changes** have no **Run now** button.

Nightly learnings runs once per night. A failed, capped, or unconfigured night waits until the next night unless an authorized person uses **Run now**.

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

Two jobs ask Decide, Tico's question-answering model: **Stale ticket review** and **Nightly learnings**. Decide tries the [Tico AI Gateway](ai-gateway.md) first, then your own key, then your company's AI provider. This page and the list change none of that. They only show which route answered.

| Pill | Meaning |
|---|---|
| **AI: Gateway** | The Tico AI Gateway answered. |
| **AI: Company key** | Your own key answered. |
| **AI: Company AI** | Your company's AI provider answered. |
| **Uses AI** | The job has not asked Decide yet. |

When the Gateway did not answer, a line under the pill says what ran instead and why, for example **Ran on your own key: Gateway unavailable** or **Ran on company AI: Gateway refused the key**. The pill follows the most recent run that asked Decide. A run that found nothing to ask does not change it.

**$ this month** adds up what the job's Decide calls cost since the first of the month (UTC). The stale review shares the team's monthly Decide cap with the other Decide users, so the figure is one slice of that cap.

## The jobs

Built-ins appear in one flat list. This table gives their default schedules; saved choices replace the defaults on controllable rows.

| Job | What it does | Default schedule |
|---|---|---|
| **Start assigned tasks** | Starts eligible assigned work on a free computer turn; respects task caps, usage holds, and computer capacity. | When a turn is free |
| **Stale ticket review** (AI) | Finds open tasks that look finished or forgotten and suggests to each owner what to do. See [Stale ticket review](tasks.md#stale-ticket-review). | Hourly |
| **Still wanted?** | Asks a task's person once whether a task idle for a week is still wanted. | Daily at 09:00 |
| **KPI stale asks** | Asks a KPI's keeper for a reading when it goes out of date, and a goal's owner when a KPI goes off track. | Hourly |
| **Health scan** | Checks Tico's health and wakes the Health Monitor only for a finding the last scan did not have. | Hourly |
| **Nightly learnings** (AI) | Reads yesterday's chats, tasks, Slack, mail and meetings and sends each bot what it should remember. See [Learnings](learnings.md). | Nightly at 03:00 |
| **Due reminders** | Tells a bot when one of its tasks reaches its due time. | Every 10 seconds |
| **Ask go-ahead** | A bot's question past its deadline goes ahead on its default answer, or expires. | Hourly |
| **Approval expiry** | Takes approvals and asks nobody will act on out of Needs you. | Hourly |
| **Unanswered questions** | On weekday mornings, sends questions unanswered for two business days back to their bots. | Hourly |
| **Bot routine timer** | Starts each bot routine below when it is due. | Every 10 seconds |
| **Stall checks** | Wakes the bot on a task nothing is moving, and drops queued work its bot can no longer read. | Every minute |
| **Waiting checks** | Sends bot tasks waiting on nothing back to work, and turns a bot's plain ask to a person into a small task. | Hourly |
| **Snooze ends** | Brings snoozed tasks back when their date comes and starts deferred runs that are due. | Every 10 seconds |
| **Go-live expiry** | Ends a go-live waiting for a computer that never reports. | Every 10 minutes |
| **Goal colours** | Works out every goal's colour again as its data ages. | Hourly |
| **Cleanup** | Clears old events, mail, retry keys and recorder data. | Hourly |
| **Gateway key check** | Asks the Tico AI Gateway whether this server's key still works and how much of the month's cap is used. | Hourly |
| **Billing check** | Reads HQ's nightly billing check for the owner's Health page. | Hourly |
| **Updates** | Asks people and bots for today's updates, one at a time. | Every 10 seconds |
| **GitHub sync** | Catches up on pull requests, merges and releases whose notice from GitHub never arrived. | Every 10 seconds |
| **Repository and setup waits** | Finishes a bot waiting for its repository or a worktree, and tells a person about a bot stuck in setup. | Every 10 seconds |
| **Release and feed check** | Checks for a new Tico release and fetches subscribed updates. | Every 10 seconds; a new release is looked for every 6 hours |
| **Directory sync** | Brings people in from the company directory. | Hourly, or the interval you set; "Off: no directory connected" until you connect one |
| **Meeting notes sync** | Brings in meeting notes from each person's connected Granola. | Every 30 seconds |
| **Document sync** | Refreshes linked documents from their sources. | Every few seconds |
| **Start fresh when the topic changes** | Starts a fresh session, like Clear, when a direct-chat message changes the topic. | When it happens |

**Last run** on **Meeting notes sync** and **Document sync** says "Each connection shows its own", because each connection keeps its own last run. **Start assigned tasks** has no shared run record. **Start fresh when the topic changes** says "Runs when it happens".

## If a row looks wrong

- **"Not run yet" on a job that should have run.** Wait for one schedule period to pass, then reload the tab. The list shows a run only after the job finishes.
- **Failed in red.** Read the reason. A busy database clears on the next run. A repeated "Tico refused it" or "Decide did not answer" means the job is failing each time; tell your Tico admin.
- **Uses AI with no route.** The job has not asked Decide yet, for example because nothing needed a decision.

<!-- Sources checked 2026-10-24 at PR #1293 head f0fcafdf65c082a408f4524e379cc091109a2b3c: ui/app/settings-recurring.js; backend/task_rules.py; backend/system_routines.py; backend/models.py (TaskRuleUpdate). Task-rules spec v1, acceptance 11. -->
