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
| Find stuck | The task round-up: one grouped list per bot, least recently sent first, then oldest line, up to ten bots by default. Only lines the list would show set its age. The standalone timer and System routines control are retired; the legacy Run now endpoint runs this step. | Custom: every 6 hours |
| Check health | Plain-code health checks: urgent findings file at once, noisy ones on their second run. Setup problems become BotOps tasks; Tico bugs follow the support report path. | Every run |
| Learn (beta) | Scores activity with Tico Decide and keeps up to 15 items per bot for its next Update turn. Off by default. The nightly timer and System routines control are retired; both legacy Run now endpoints run this step. | Once a day |
| Suggest | Needs Privacy consent. Fetches suggestions from Tico Gateway with count-only stats, at most once every 24 hours, and keeps them for each bot's next Update. | Once a day |
| Update | Builds the day's update queue from 05:00, skipping bots with no work unless learnings or suggestions are waiting. The server keeps sending requests and drafting people's updates after the run ends. | Once a day |
| Dream | The auto dream: starts a dream for up to "Most bots per run" bots (three by default, 1 to 20). | Once a day |

## How often

- Every run: each run.
- Day: once per company day, after 05:00.
- Week: seven days after the step's last attempt.
- Custom: every N hours or days, counted from the step's last attempt. A failed attempt counts, so a broken step does not retry on every run.

Suggest waits at least 24 hours between successful fetches, measured from the request time. A skip inside that window keeps its daily attempt for a later run that day.

When every Dream start is refused by the shared wake limiter, it keeps its daily attempt for a later run that day. A pass that starts a dream or encounters another refusal or failure counts for the day.

Update skips before 05:00 company time without using up its cadence, so it can build the day's queue on a later run.

## Run now

`POST /api/v2/maintenance/run`, or `hub maintenance run [--step KEY]`. A plain Run now uses the same claim and due rules as the schedule, so a step runs only when it is due. `--step` runs that one step even when it is not due; a switched off step stays off. A run that is already going is returned instead of a second one. With an Idempotency-Key, a repeat of the same key returns the run that key made, and starts nothing.

## Recording and recovery

- Each step writes its own log row with its status (ran, skipped, failed, off or pending), reason, counts and cost.
- Update stays pending while its day's requests are queued or out. Once the queue drains, its original step row completes, even while Maintenance is paused.
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

Find stuck runs before Check health so BotOps can get its task list. If that list uses BotOps's wake for the run,
Check health still files its findings and keeps its wake for the next allowed run. With the default schedule, it tries again one hour later.

## Switches moved from the old jobs

- Learnings became Learn.
- Auto dream became Dream.
- Health scan became Check health.
- Task roundup became Find stuck. The stale review rule does not: it defaults off, so a saved off stale review says
  nothing about Find stuck and does not turn it off.

A switch you chose keeps its value. A switch you never chose takes the new default. The stalled task check switch was removed.
Find stuck takes the latest saved Task roundup switch when its cutover migration runs, then retires the legacy switch, schedule and Automations mirrors.

Learn takes the latest saved learnings switch at cutover only while its Maintenance setting is still seeded.
A person's Maintenance choice is preserved, including one made before cutover. The old setting and its read-only
Automations mirror are retired, and unfinished mirror runs are skipped.

## Where to see it

The Learn step's settings include the last seven days of runs, items read, items sent per bot, saved, dropped and cost. `GET /api/v2/maintenance` returns these as the Learn row's `stats` (`days`, `runs`, `items_read`, `items_sent`, `saved`, `dropped`, `cost_usd`).

- Web: Automations › Maintenance. It is the first row of the list. Its screen has the run's schedule, the pause switch
  and the monthly cap, one row per step with its switch, How often and number of bots, Run now, and a Runs tab with each
  step's line. Anyone signed in can read it; only an owner or an admin can change it.
- API: `GET /api/v2/maintenance`, `PATCH /api/v2/maintenance`, `GET /api/v2/maintenance/runs`, `POST /api/v2/maintenance/run`.
- CLI: `hub maintenance status`, `hub maintenance set`, `hub maintenance run`, `hub maintenance runs`.
- MCP: the `hub_maintenance*` tools.
