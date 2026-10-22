# System routines

System routines are the jobs Tico runs on its own timers, such as the hourly stale ticket review and the nightly learnings. They are separate from [bot routines](routines.md), which belong to a bot and open a task for it.

Open **Settings → Routines**. The **System** section ("What Tico runs on its own") sits above **Bot routines**. It lists every built-in job. Everyone who can open Settings sees it. The section is read-only: it has no switches or buttons. **Unreleased**: it ships with the next release.

## What each row shows

| Part of the row | What it tells you |
|---|---|
| Name and one line | What the job does. |
| Schedule | How often it runs, in words: "Hourly", "Nightly at 03:00" (Pacific time), "Every 10 seconds". |
| **Last run** | When it last finished (hover for the exact time) and the result in words, such as "9 checked, 2 suggestions". A job with no recorded run says "Not run yet". |
| **Next run** | When the job runs next, from its schedule and its last run. A dash means Tico cannot tell. |
| **Tico needs this to run** | Shown on every Housekeeping row. |
| **AI** pill and spend | Shown on jobs that ask Decide: which route answered, and what the job has cost this month. |

A run that failed shows **Failed:** and a plain reason in red, for example "Failed: the database was busy". The reason names the kind of failure, never the error text, because error text can hold people's data.

A job that wakes every few seconds and finds nothing to do does not add a row each time. A quiet run with the same result as the last one moves that row's times forward, at most once a minute. Tico keeps each job's last 50 runs.

The list refreshes when you open the tab and at most once a minute while it stays open.

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

Tico runs 25 built-in jobs in four groups. The schedule is the one the list shows.

### Checks that suggest

| Job | What it does | Schedule |
|---|---|---|
| **Stale ticket review** (AI) | Finds open tasks that look finished or forgotten and suggests to each owner what to do. See [Stale ticket review](tasks.md#stale-ticket-review). | Hourly |
| **Still wanted?** | Asks a task's person once whether a task idle for a week is still wanted. | Daily |
| **KPI stale asks** | Asks a KPI's keeper for a reading when it goes out of date, and a goal's owner when a KPI goes off track. | Hourly |
| **Health scan** | Checks Tico's health and wakes the Health Monitor only for a finding the last scan did not have. | Hourly |

### Learning

| Job | What it does | Schedule |
|---|---|---|
| **Nightly learnings** (AI) | Reads yesterday's chats, tasks, Slack, mail and meetings and sends each bot what it should remember. See [Learnings](learnings.md). | Nightly at 03:00 |

### Reminders and deadlines

| Job | What it does | Schedule |
|---|---|---|
| **Due reminders** | Tells a bot when one of its tasks reaches its due time. | Every 10 seconds |
| **Ask go-ahead** | A bot's question past its deadline goes ahead on its default answer, or expires. | Hourly |
| **Approval expiry** | Takes approvals and asks nobody will act on out of Needs you. | Hourly |
| **Unanswered questions** | On weekday mornings, sends questions unanswered for two business days back to their bots. | Hourly |

### Housekeeping

Every row here says **Tico needs this to run**.

| Job | What it does | Schedule |
|---|---|---|
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

**Last run** on **Meeting notes sync** and **Document sync** says "Each connection shows its own", because each connection keeps its own last run.

## If a row looks wrong

- **"Not run yet" on a job that should have run.** Wait for one schedule period to pass, then reload the tab. The list shows a run only after the job finishes.
- **Failed in red.** Read the reason. A busy database clears on the next run. A repeated "Tico refused it" or "Decide did not answer" means the job is failing each time; tell your Tico admin.
- **Uses AI with no route.** The job has not asked Decide yet, for example because nothing needed a decision.
