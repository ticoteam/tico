# Heartbeat

The Heartbeat is one short beat, every 10 seconds by default, that runs the jobs that must happen within seconds but have nothing to start them:

- **Due times**: time passing, such as a due date, a snooze's end or an hour gone by.
- **Recovery**: failures nothing announces, such as a dead turn, a silent run or a lost wake.
- **Outside polls**: outside systems that do not push, such as GitHub and HQ paging.

Open it from the **Heartbeat** row on **Automations** (`#/automations/heartbeat`), beside Maintenance. Everyone who can open Automations can read it. Only an owner or an admin can change it.

## Steps

A beat runs these steps in this order. A step runs only when it is due. If one step fails, the others still run.

| Step | Kind | How often |
|---|---|---|
| GitHub sync | Outside polls | Every 10 seconds |
| Personal alerts | Outside polls | Every 10 seconds |
| Silent run check | Recovery | Every minute |
| Turn fixer | Recovery | Every beat |
| Run housekeeping | Recovery | Every beat |
| Shipped merges | Recovery | Every beat |
| Task loop alerts | Recovery | Every beat |
| Snooze ends | Due times | Every beat |
| Queue check | Recovery | Every beat |
| Merge and release waits | Recovery | Every beat |
| Due reminders | Due times | Every beat |
| Updates | Due times | Every beat |
| Company page editions | Due times | Every beat |
| Ask go-ahead | Due times | Hourly, or its saved schedule; has an on/off switch |
| Email digest | Due times | Hourly |
| Bot thread digest | Due times | Hourly |
| Paused bot replies | Due times | Every beat |
| Go-live expiry | Due times | Every 10 minutes |
| Stall checks | Recovery | Every minute |
| Approval expiry | Due times | Hourly |
| Repository and setup waits | Outside polls | Every 10 seconds |
| Calls and live meetings | Recovery | Every beat |

What each step does is listed in [System routines](system-routines.md). On the Heartbeat screen, **Steps** groups the steps by kind. Each row shows:

- how often the step runs;
- its last run and last result;
- how many times it did something or failed in the last 24 hours;
- how long its last run took;
- its last error.

**Ask go-ahead** keeps its on/off switch on its row. Only an owner or an admin can use it.

## What's stored

- **One row per step.** Each beat updates the row with the step's last run, time taken and result, the last time it did something and what it did, and its last error. The row also keeps the step's counts for the last 24 hours.
- **One row per beat that did something or failed.** The row lists those steps' results. **Log** shows these beats, newest first, and you can tap a beat to see its steps. Tico keeps them for 7 days, up to 5,000 beats.
- **Quiet beats are counted, not stored.**

On Automations, the Heartbeat is a single System job. It records one run per beat, and repeated quiet runs fold into one row.

## Interval

**Every** sets the time between beats, from 5 to 60 seconds. The default is 10 seconds.

A shorter interval never makes outside polls run more often than every 10 seconds. The server's other periodic work also runs at most every 10 seconds. A longer interval slows down every step and the rest of that work too.

The screen reads the Heartbeat again once a minute while it is open.

API: `GET /api/v2/heartbeat` (with `limit` and `before` to page the log) and `PUT /api/v2/heartbeat` with `{"interval_s": 15}`. A step's switch uses `PUT /api/v2/system-routines/{key}`.

<!-- Sources checked 2026-11-06 on feat/heartbeat: backend/heartbeat.py; backend/app.py (schedule_loop); backend/migrations/20261106094503_heartbeat.py; ui/app/automations-heartbeat.js. -->
