# Maintenance

Maintenance is Tico's upkeep run. A run starts when its schedule says it is due, and it goes through its steps in one fixed order. A step runs only when it is switched on and due. Every other step is logged with the reason it did not run.

## Schedule and pause

- The run starts every hour by default. The other choices are every 2, 3, 6 or 12 hours, or once a day at 05:00 company time (Los Angeles).
- At most one scheduled run starts per schedule bucket, and none starts beside a live run. A Run now for one step does not use up its bucket; the bucket's full run still starts.
- Pause stops the schedule. Run now still works while the schedule is paused.

## Steps

In order:

| Step | What it does | How often |
| --- | --- | --- |
| Collect | Records which bots did work in the window: a settled turn that wrote something. Messages and memory commits do not count. Always on. | Every run |
| Clean up | Prunes old rows: events, mail, retry keys, recorder data, finished webhook and automation runs, and Maintenance's own runs older than 30 days. Deadline work stays in the server's hourly check. | Every run |
| Check health | The health scan. Still on its own timer for now. | Every run |
| Find stuck | The task round-up: one grouped list per bot, oldest items first, up to ten bots by default. The standalone timer and System routines control are retired; the legacy Run now endpoint runs this step. | Custom: every 6 hours |
| Learn | Nightly learnings. Still on its own timer for now. Off by default. | Once a day |
| Suggest | Still on its own timer for now. | Once a day |
| Update | Still on its own timer for now. | Once a day |
| Dream | The auto dream: starts a dream for up to "Most bots per run" bots (three by default, 1 to 20). | Once a day |

A step that is not moved here yet shows "on its old timer".

## How often

- Every run: each run.
- Day: once per company day, after 05:00.
- Week: seven days after the step's last attempt.
- Custom: every N hours or days, counted from the step's last attempt. A failed attempt counts, so a broken step does not retry on every run.

When every Dream start is refused by the shared wake limiter, it keeps its daily attempt for a later run that day. A pass that starts a dream or encounters another refusal or failure counts for the day.

## Run now

`POST /api/v2/maintenance/run`, or `hub maintenance run [--step KEY]`. A plain Run now uses the same claim and due rules as the schedule, so a step runs only when it is due. `--step` runs that one step even when it is not due; a switched off step stays off, and a step on its old timer is skipped. A run that is already going is returned instead of a second one. With an Idempotency-Key, a repeat of the same key returns the run that key made, and starts nothing.

## Recording and recovery

- Each step writes its own log row with its status (ran, skipped, failed, off or pending), reason, counts and cost.
- A step's cursor moves only when it reports `ran`, and only forward: a late `ran` for an older window does not move it back.
- A step that raises is recorded as failed, and the run goes on.
- A live run with no heartbeat for 30 minutes is abandoned. Its unfinished steps are marked failed, and the schedule can start a new run. A stale run is ended before the schedule checks its bucket, so it never holds a day.
- A step that returns after its run was abandoned has its result dropped. Its cost still counts on the run.

## Spend cap

The monthly judge spend cap is saved here as `monthly_cap_usd`. When it is saved, the judge enforces it. When it is not, the environment setting `TICO_DECISIONS_MONTHLY_USD` applies (default 50).

## Who can do what

Anyone signed in can read the settings, the steps and the runs. Only an owner or an admin can change the schedule, the pause, the spend cap or a step, or run it.

Maintenance wakes share one limiter: one wake per bot per run, three per task a day, and fifty across the fleet an hour. A wake that names several tasks counts once toward the fleet and once against
each of its tasks.

## Switches moved from the old jobs

- Learnings became Learn.
- Auto dream became Dream.
- Health scan became Check health.
- Task roundup became Find stuck. The stale review rule does not: it defaults off, so a saved off stale review says
  nothing about Find stuck and does not turn it off.

A switch you chose keeps its value. A switch you never chose takes the new default. The stalled task check switch was removed.
Find stuck takes the latest saved Task roundup switch when its cutover migration runs, then retires the legacy switch, schedule and Automations mirrors.

## Where to see it

- API: `GET /api/v2/maintenance`, `PATCH /api/v2/maintenance`, `GET /api/v2/maintenance/runs`, `POST /api/v2/maintenance/run`.
- CLI: `hub maintenance status`, `hub maintenance set`, `hub maintenance run`, `hub maintenance runs`.
- MCP: the `hub_maintenance*` tools.
