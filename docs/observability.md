# Product analytics and error reporting for your Tico install (optional)

Tico can send privacy-filtered usage analytics to PostHog and error reports to Sentry. Both are
**off until configured**; a stock install sends nothing. You can use one project per environment, or share
one PostHog project between several apps by filtering on the `app` property (Tico sends
`app=tico`).

## Activation

1. Create a PostHog project (and, optionally, a Sentry project for Tico). Preserve the pinned SDK
   and privacy filters described below.
2. Set the variables below in the API service's environment. In Docker, add them to the install's private `.env` and explicitly
   forward them with the [compose override](#docker-configuration) below; `.env` alone does not pass them into the container. Do not commit populated values.

| Variable | Purpose |
| --- | --- |
| `TICO_OBSERVABILITY_ENVIRONMENT` | Required for either SDK: `production`, `staging`, `development`, or `test` |
| `TICO_OBSERVABILITY_ID_SECRET` | Required for browser telemetry: a securely generated, stable secret of at least 32 characters; server only |
| `TICO_POSTHOG_KEY` | Public `phc_` project key for your PostHog project; enables browser analytics with the host and required common settings |
| `TICO_POSTHOG_HOST` | Exactly `https://us.i.posthog.com` or `https://eu.i.posthog.com`, matching the chosen project's region |
| `TICO_SENTRY_DSN` | Public HTTPS Sentry ingestion DSN for the Tico project; enables browser errors with the required common settings |
| `TICO_SENTRY_SERVER_DSN` | Public HTTPS ingestion DSN for the same Tico project; enables API/scheduler errors with the environment setting |
| `TICO_INSTANCE_NAME` | Optional name for this install on its error reports (`instance` tag, made a lowercase slug such as `acme-co`); unset: the team's name is sent (`TICO_TEAM_NAME`), or the opaque `TICO_ENVIRONMENT_ID` when it has none |
| `TICO_RELEASE` | Optional release label; only letters, numbers, dots, underscores and hyphens, up to 100 characters; unset: the Tico version |

   Public DSNs must use a Sentry ingestion host, numeric project path, and public hexadecimal
   key; legacy password-bearing DSNs and custom/self-hosted endpoints are rejected. Only
   human/owner sessions can fetch `/api/v2/observability`; responses are not cached. The endpoint
   exposes the public project key/DSN and an opaque identity, never the identity secret.
3. Install the locked Python dependencies. The checked-in browser vendor bundle is ready
   for the existing static-file deployment. To regenerate it: `npm ci` then
   `npm run build:observability`. Commit the generated bundle with source/dependency changes.
4. In a staging deployment, sign in, navigate, create a test task,
   send a test chat, and exercise a safe rejected submission. Inspect `app=tico`, environment,
   outcomes and duration fields in PostHog. Inspect one controlled frontend error and API
   failure in Sentry. Confirm network payloads contain no content or raw URLs. Test logout
   and a different account; check that identities change and prior events do not follow it.
5. Enable it in production only after the staging check. To disable, remove the relevant key/DSN or required environment and restart
   the API. Browser config refreshes every 30 seconds;
   reload open tabs to apply configuration changes immediately.

No `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, or `SENTRY_PROJECT` is sent to the browser. This
plain JavaScript UI needs no source-map upload for its unminified application files; stack
locations are restricted to known static filenames and line/column numbers. The minified SDK
bundle has no published source maps, and this change does not implement source-map upload.
If upload is added later, its authentication token must remain in CI/build secrets, never in
public config or browser assets. Release/debug mapping needs its own tested privacy policy.

## Event contract and interpretation

Every explicit analytics event uses `app=tico` and the configured `environment`. Where
applicable it includes `workflow`, `action`, `outcome`, `duration_ms`, `active_duration_ms`,
and a fixed `route` template. It never invents a team field.

| Events | Meaning |
| --- | --- |
| `route_viewed` | Authenticated navigation to an allowlisted route; all bot paths become `/bot/:bot`, document subpaths become `/docs`, unknown routes become `/unknown` |
| `active_time` | Incremental route usage, flushed every 30 seconds, on route changes, hidden state and page exit |
| `workflow_started` | A recognized submission began |
| `workflow_completed` | Its API operation returned success after the existing retry cycle |
| `workflow_failed` | The API operation rejected or exhausted retries; no error text or request content accompanies it |

Instrumented submissions: chat sends (including task/conversation and page chat), task
creation/update, approval decisions, note saves, meeting import/edit/delete/send,
bot definition changes, and settings transition requests. JSON and supported multipart
submissions use the same wrapper; individual network retry attempts do not duplicate events.
Unknown endpoints, polling reads, credential checks, and the legacy unauthenticated local
Tico are untracked. Other dedicated UI modules and background workers are not comprehensively
instrumented yet. A submission success means the API accepted it, not that a bot finished its
work or that a settings transition finished applying.

`duration_ms` on workflow events is **submission latency**, including retries. It does not
measure drafting time, bot execution time or end-to-end task completion. `active_duration_ms`
on those events is the portion of submission latency during visible, recent user activity.
Route active time stops 60 seconds after the last pointer/key/scroll/touch activity and
excludes hidden-tab time. It is a proxy for engagement, not verified labor or task effort;
reading without interaction can be undercounted, multiple tabs can overlap, and offline/page
close delivery can be lost. Each active-time event is a delta; sum deltas rather than treating
one as the full visit duration.

Identity is `tico:` plus HMAC-SHA256 of the authenticated server actor using the server-only
secret. It is stable and pseudonymous, not anonymous. The authenticated endpoint optionally returns one bounded staff display name from the
server's admitted roster metadata. Stored names equal to the generated ID title are
ambiguous and omitted; absent/invalid/ambiguous names use the verified Access email
only when it matches the admitted roster row. This fallback intentionally transmits
work email as the display name, without a separate email property. On `route_viewed`
only, the final hook constructs `$set: {name: person_display_name}` from that validated
config; arbitrary event-input person properties are always discarded. Existing people
gain labels on next authenticated use, without backfill, aliases or identity merges.
Name changes refresh the adapter but preserve the same HMAC ID. Sentry stays opaque.
There are no anonymous pageviews or persistent analytics cookies. SDK identity resets on observed
401/403, explicit logout links, and account changes during the 30-second auth/config refresh;
page reload starts with no identity. The refresh compares the complete validated configuration,
including per-SDK enablement, key/host/DSN, environment and release, even for the same identity.
Logout in another tab is observed at the next refresh. Retired PostHog instances discard retries
and fence transport dispatch and late failure callbacks; teardown never flushes old events.
Requests already sent before reset cannot be recalled. The transport fence uses private APIs
from pinned PostHog 1.438.11, so SDK upgrades must pass the real retry regression tests.
Rotating the secret changes historical person continuity. Other apps sharing the project use their own identity
contracts, so cross-app person joins are unavailable; compare app-level workflow aggregates.

## Suggested PostHog insights

If the project is shared, always filter by `app` and `environment` first. Suggested dashboards:

- Daily/weekly distinct authenticated humans from `route_viewed` and workflow events, split
  by app; route popularity and summed active-time deltas show which areas see engagement.
- `workflow_started` → `workflow_completed` funnels grouped by workflow/action, with failed
  event counts and failure rate. A missing completion may also mean tab closure or delivery
  loss; concurrent identical actions mean person funnels are an approximation, not an exact
  transaction ledger.
- Median and p90 `duration_ms` for completed submissions, grouped by workflow/action,
  alongside failure counts. Slow or repeatedly failing steps are candidates for investigation.
- High-frequency actions and large active-time totals suggest where automation or UI changes
  might help. Validate these hypotheses with teammates; this telemetry cannot explain intent
  or distinguish useful reading from unproductive effort on its own.

Sentry receives frontend uncaught errors/unhandled rejections, relevant API 5xx/unhandled
exceptions, scheduler failures and runner panics (see [Server and runner errors](#server-and-runner-errors)).
Bot chat sends also report `source=chat_send` when they cross five seconds while still pending,
then report their outcome when a slow send finishes or any send fails. The only send fields are
`outcome` (a fixed label), `duration_ms`, `retries`, and `status` (the last HTTP status, or zero
when no response arrived). No message text, bot name, attachment names, raw URL or error text is
included. A quick successful send generates no Sentry event. A send's own auth failure is reported before
it resets identity, unless another request already reset it. Callbacks from a retired account
are dropped and cannot report under a new identity.
This uses the existing per-install `TICO_SENTRY_DSN` and required browser settings above. Leave
them unset on installs outside an authorized rollout; adding this instrumentation does not enable
reporting on stock installs. Team and Tidy activation remains a separate operator rollout.
Defaults, request/context capture, logs, tracing, replay,
breadcrumbs and local/source variables are disabled. Events are reconstructed before sending:
fixed error labels, app/environment, status/source on the server and allowlisted stack
locations; frontend events include the same opaque identity. Exception messages, customer
values, URLs, headers, tokens, payload bodies and arbitrary SDK enrichment are discarded.
Server events intentionally have no person identity. Coarse error labels trade diagnostic
richness for privacy; stack locations are the primary grouping evidence.

## Server and runner errors

To report one install's server and runner errors, and no other install's, set on that install only:
`TICO_SENTRY_SERVER_DSN` (the Sentry project's public DSN), `TICO_OBSERVABILITY_ENVIRONMENT` (`production`), and
optionally `TICO_INSTANCE_NAME`; forward them with the [compose override](#docker-configuration) and recreate the
server. With either of the first two unset or empty, the Sentry SDK is never started and nothing is sent. Several
installs can share one Sentry project: every event carries `release` (the Tico version, or `TICO_RELEASE`) and an
`instance` tag. Runners need no setting: each sends the file and line of a panic to its own server with its next
heartbeat, and the server forwards it only when reporting is on (`source` `runner`, with the runner's version). A
runner reports the same file and line at most once an hour, and at most five panics between two heartbeats. Panics are
held in memory until the next pass, so a panic that stops the runner is not reported. The server also sends at most
one report per runner, file and line an hour, and at most 20 per runner an hour, whatever a runner posts.

To see that reports arrive, and what they hold, send a harmless test error from the server:

```bash
docker compose exec server python -m backend.observability test   # a native install: in the server's environment
```

It prints the event id to look for in Sentry, or says reporting is off. Each event holds only: the exception's class
(`KeyError`, `RunnerPanic`), its stack frames inside Tico's own code (file, function, line), the route's template
(`POST /api/v2/tasks/{tid}/comments`, never the address with its values), the status, `source`, `release`,
`instance` and environment. It never holds the exception's message, local variables, source lines, request bodies,
headers, cookies, query strings, addresses, emails, tokens or any message, task, document or comment text: each
event is rebuilt from those fields before the SDK sees it and again in its last hook, and breadcrumbs are off.

PostHog autocapture, replay, heatmaps, dead/rage clicks, error capture, surveys, performance,
remote flags and external dependency loading are disabled. The last-mile hook allows only
our named events and fixed properties, plus SDK event IDs/timestamps and the configured
public project token required for ingestion. GeoIP processing is disabled. SDK errors are
contained so analytics failures cannot fail an application operation.

## Deployment notes

The UI's `SERVICE_NAMES.posthog` entry names a bot tool; it is not application
analytics. `TICO_RELEASE` is reused as the release label. Cloud installs use the same Docker configuration below. Native checkout
installs put these variables in the environment's `server.env`, then use `scripts/tico -e <slug> server restart`. For the identity
secret, use 32-256 URL-safe letters, digits, underscores or hyphens. Keep it stable across releases and out of command arguments and logs.

### Docker configuration

Add this `compose.override.yaml` beside the install's `compose.yaml`:

```yaml
services:
  server:
    environment:
      TICO_OBSERVABILITY_ENVIRONMENT: ${TICO_OBSERVABILITY_ENVIRONMENT:-}
      TICO_OBSERVABILITY_ID_SECRET: ${TICO_OBSERVABILITY_ID_SECRET:-}
      TICO_POSTHOG_KEY: ${TICO_POSTHOG_KEY:-}
      TICO_POSTHOG_HOST: ${TICO_POSTHOG_HOST:-}
      TICO_SENTRY_DSN: ${TICO_SENTRY_DSN:-}
      TICO_SENTRY_SERVER_DSN: ${TICO_SENTRY_SERVER_DSN:-}
      TICO_INSTANCE_NAME: ${TICO_INSTANCE_NAME:-}
      TICO_RELEASE: ${TICO_RELEASE:-}
```

Store the selected values in the private `.env` (mode 0600). Recreate the API service to apply the new mapping:

```bash
docker compose up -d server
# Check presence only; never print the values or an expanded compose config.
docker compose exec server python -c 'import os; names = ("TICO_OBSERVABILITY_ENVIRONMENT", "TICO_OBSERVABILITY_ID_SECRET", "TICO_POSTHOG_KEY", "TICO_POSTHOG_HOST", "TICO_SENTRY_DSN", "TICO_SENTRY_SERVER_DSN"); print({n: bool(os.getenv(n)) for n in names})'
```

While signed in as a human, inspect `GET /api/v2/observability` in the browser: it returns validated public configuration,
never the identity secret. Configuration can be present but rejected if it does not meet the requirements above.

Run `npm run check:observability` for the browser source syntax checks. Backend reporting and privacy contracts are covered by
`python -m pytest -q backend/tests/test_observability.py`. The checkout does not ship a separate browser observability test command.
Release validation: `docker/smoke.sh` boots the server image with an
isolated database and telemetry disabled. It checks both browser scripts are served and Sentry
is never imported; arbitrary JavaScript assets remain excluded and all three observability
dependencies are required at build time.
Backend tests: `.venv/bin/python -m pytest backend/tests/test_observability.py
backend/tests/test_auth.py backend/tests/test_api.py backend/tests/test_scheduler.py -q`.

Official SDK references consulted: [PostHog configuration](https://posthog.com/docs/libraries/js/config),
[PostHog JavaScript SDK source/types](https://github.com/PostHog/posthog-js),
[Sentry Python API](https://getsentry.github.io/sentry-python/api.html), and
[Sentry JavaScript SDK](https://github.com/getsentry/sentry-javascript).

## The flight recorder

Separate from the optional services above, every server keeps its own performance history in its database, so a slow
or pinned server can be explained with one query and a release compared with the one before it. Nothing in it leaves
the server. It is on unless `TICO_FLIGHT_RECORDER=0`; the request path pays for two clock reads and a queue append, and a
background thread does the rest and writes once a minute in one short transaction (`backend/flight.py`), after writing
the same rows to a local file that can be read without the server (below).

| Table | What | Kept |
| --- | --- | --- |
| `flight_requests` | Per minute (`span` 60), per route template (`GET /api/v2/tasks`) and caller kind (`human`, `bot`, `runner`, `service`, `anonymous`): count, 5xx errors, response bytes, total, p50, p95 and max milliseconds. A caller that hangs up while its request body is read is recorded as 499 (client closed request), not as a 5xx. After two days, minutes become hours (`span` 3600). | 14 days |
| `flight_slow` | Each request over one second: route, caller kind, actor id, milliseconds, bytes, status, SQL milliseconds, write-lock wait milliseconds and query count (at most 50 a minute). | 14 days, 5,000 rows |
| `flight_process` | Per minute: CPU (% of one core), memory, threads, open files and sockets, event-loop lag (p95, max), SQL statements and time, write transactions with the longest lock wait and hold, `database is locked` errors, and `dropped`: samples lost because the background thread fell behind (a gap in that minute's numbers). | 14 days |
| `flight_sql` | Per hour, per statement shape (literals folded): count, total, p95 and max milliseconds, from the statement's start through its last row fetched (rows read by iterating, `fetchone`, `fetchmany` or `fetchall`; never the caller's own work between rows); the top 100 by time, the rest as `(other)`. | 90 days |
| `flight_db` | Hourly: file, WAL and free bytes, row counts of the growing tables and their oldest row. Table/index sizes are estimated from sampled rows (no page is walked) and refresh at most once per 24 hours, reused between passes, including across restarts. | 90 days |
| `flight_events` | `start`: release, commit, Python, SQLite, a hash of the installed packages and of the non-secret settings (with a short hash per setting, so two starts show which changed; no values), and `provenance`: `modified` when the code on disk differs from the hash the image build stored in `release-manifest.json` (files copied over a release image), `mismatch` when the image was not built from its release tag's commit on GitHub, otherwise `match`, `unverified` or `unknown`. `stall`: the event loop late by two seconds or more, with its class and every busy thread's stack (file, line, function; never values), at most once a minute (every stall is in the file). `long_hold`: a write transaction that held the lock five seconds or more: `route` (method and template, null for a background job), `site` (the code that held it, file, line and function), `hold_s`, `wait_s`, and `open` when it was still held at the minute's end (`site` is then where its thread is now); at most 20 a minute, also in the server log. `recorder_gap`: minutes whose rows could not be written (above). | 90 days |

Times are Unix seconds. A `modified` or `mismatch` start shows as **Release image** on the Health page. The integrity of the
database is checked by the daily verified backup (`PRAGMA integrity_check`), not here.

The `flight_db` columns remain `ts`, `bytes`, `wal_bytes`, `free_bytes` and `detail`. Within the detail,
`objects_at` dates the table/index sizes, while `seconds` measures the current hourly sampling pass.
Metrics expose `db.objects_at` beside `db.objects`; these sizes can be older than the hourly `db.at`.
Older records without `objects_at` use their snapshot timestamp. The sizes are estimates: a table is its row count
times the average record size of 300 sampled rows (three runs of 100 spread over its rowid range), an index is its
table's rows times its columns' average size, and a partial index counts every row, so it reads high. On the local
test databases the 40 largest estimates were within about 20% of dbstat, except a partial index. Object sizes are
null until the first pass; hourly totals and row statistics remain available. Size collection runs only in the
flight recorder's background thread through autocommit reads, finished before the short snapshot write transaction.
Opening Health or metrics reads recorded samples and does not trigger a page scan.

Owners, admins and the team's bots read it at `GET /api/v2/system/metrics?minutes=60&section=all` (a bot's slow requests
leave out who made them; `section`: any of `requests,slow,process,sql,db,events`), with `hub health metrics` or `hub_health_metrics`, and see the
last hour under **Performance** on the Health page. `GET /api/v2/ops/timing` still answers with the last 300 requests per
route since the start.

With the recorder enabled, `Server-Timing` includes `sql` and `wait` durations alongside `auth` and `app`.
Slow metrics retain `sql_ms`, `wait_ms` and `queries`; rows recorded before the upgrade have null values.
These are elapsed timings, so time waiting for CPU can also appear within SQL time. Compare the same
window's CPU and event-loop lag before attributing the remaining request time to a lock.

```sql
-- Where the last hour went, by route
SELECT route, sum(n) AS calls, round(sum(total_ms) / 1000) AS seconds, max(p95) AS worst_p95
FROM flight_requests WHERE ts > strftime('%s','now') - 3600 GROUP BY route ORDER BY seconds DESC LIMIT 10;
-- CPU and loop lag around an incident
SELECT datetime(ts,'unixepoch') AS minute, cpu, lag_max, wait_max, hold_max, locked FROM flight_process
WHERE ts > strftime('%s','now') - 3 * 3600 ORDER BY ts;
-- Stalls and what the event loop was doing
SELECT datetime(ts,'unixepoch'), detail FROM flight_events WHERE kind='stall' ORDER BY id DESC LIMIT 5;
```

Runner polls and writer admission use the same lock accounting: `wait_ms` includes the FIFO queue for
writers in this server process and SQLite's wait behind another process. Only the next writer is woken;
a scheduler's next transaction joins behind queued requests. SQLite WAL and FULL durability stay unchanged.
A renew with at least two thirds of its lease remaining reads its actual deadline without writing; its reply
reports the remaining seconds, never a fictitious extension. It still checks current assignment and credentials.
Input polls and renewals near their deadline use short queued transactions. Heartbeat prepares live-run provenance in a read snapshot and reuses it only when
`data_version` is unchanged under the write lock; otherwise it recomputes current state.

### The flight file

The tables live in the database a stall is usually about, so everything is written first to an append-only file beside
the database: `<folder of TICO_DB>/flight/flight.ndjson` (Docker: `/data/flight/`; a local install: its environment's
folder), rotated at 10 MB with ten files kept, by its own thread from a bounded queue that never makes a request wait (a
full queue drops the record and counts it). The tables are written after, each write waiting at most two seconds for the
lock; a write the lock kept out waits and goes in with the next one (up to 120), and minutes that still go unwritten are
named in a `recorder_gap` event (`minutes`, `count`), so the tables never lose a minute without saying so. `TICO_FLIGHT_RECORDER=0` turns off both. Read it with the server running, stopped or stuck:

```bash
python -m backend.flight summary --since 30m          # Docker: docker compose exec server python -m backend.flight summary
scripts/tico -e ENV server flight summary --since 2h   # a local install
```

The summary shows server starts, stalls with their class and stacks, the slowest routes (count, total, p95, max, SQL time,
lock wait, average and largest answer), slow requests, per-minute CPU, memory, loop lag, lock wait and hold, and drops,
and request percentiles across all routes (estimated from each route's per-minute p50, p95 and max);
`--json` prints the same as JSON. The support bundle carries the last 24 hours of it (routes, slow requests, the latest
stalls with stacks, hourly peaks), through the same allowlist and redactor as the rest of the bundle.

**Stall class.** The recorder samples `getrusage` (CPU, page faults, context switches, block operations), `/proc/self/io`
where there is one (Linux), resident memory and the write-lock wait four times a second, and a stall is classed from the
change between the loop's last wake and now (`stall`) and again over the whole stall once the loop wakes (`stall_end`).
The first rule that holds wins: `lock` when write-lock waits (finished, plus still waiting) cover half the window;
`memory` at 50 or more major page faults or 512 MB more resident memory; `cpu` when the process used 80% of a core, or
was switched out involuntarily over 200 times a second; `disk` at 50 MB read and written (or 2,000 block operations);
otherwise `unknown` (`backend/flight_file.py` `RULES`).

**Signals.** Send them to the pid in `flight/server.pid` (in Docker the first process is litestream, which passes on only
SIGINT and SIGTERM), or let the command do it: `python -m backend.flight stacks` sends SIGUSR1, and the server appends every
thread's stack to `flight/stacks.txt` (faulthandler: it works while the event loop is stuck); `python -m backend.flight
memory` sends SIGUSR2, which starts tracemalloc, and the next one writes the top 50 allocation sites to
`flight/memory-<time>.txt` (and a `memory` record) and stops it.

**Record format** (version 1; the Rust runner's recorder writes the same shape). One JSON object per line, each with
`v` (1), `ts` (Unix seconds, milliseconds as decimals) and `kind`:

| `kind` | Fields |
| --- | --- |
| `start` | `pid`, `version`, `commit`, `python`, `sqlite`, `platform` |
| `minute` | `ts` is the minute's start. `cpu` (% of one core), `rss` (bytes), `threads`, `fds`, `sockets`, `lag_p95`, `lag_max` (ms), `requests`, `queries`, `sql_ms`, `txns`, `wait_max`, `wait_ms` (write-lock wait, longest and total), `hold_max`, `hold_p95`, `locked` (`database is locked` errors), `dropped` (samples lost in memory), `file_dropped` (records lost to a full queue since the start), `db_failed` (table writes that could not get the lock since the start), `db_pending` (writes waiting for the next try) |
| `routes` | `ts` (the minute), `span` (60), `routes`: list of `route` (method and template), `caller`, `n`, `errors` (5xx), `bytes`, `bytes_max` (response bytes from Content-Length; 0 for streams), `total_ms`, `p50`, `p95`, `max_ms`, `sql_ms`, `wait_ms` |
| `slow` | A request over one second: `route`, `caller`, `actor`, `ms`, `bytes`, `status`, `sql_ms`, `wait_ms`, `queries` (its own SQL and lock wait, from the threads it ran on) |
| `stall` | `id`, `seconds` (since the loop last woke), `class`, `usage`, and `threads` (`busy`: `thread`, `loop`, `stack` innermost last; `idle`: a count) at most once a minute |
| `stall_end` | `id` (the `stall`'s), `seconds` (between the loop's two wakes), `class`, `usage` over the whole stall |
| `long_hold` | `route`, `site`, `hold_s`, `wait_s`, `open`: as the `long_hold` event above |
| `drops` | `n`: records the queue had no room for |
| `memory` | `state`: `started_at`, or `dumped` with `traced`, `peak` (bytes) and `top` (`where`, `bytes`, `count`) |

`usage` holds `window_ms`, `cpu_s`, `majflt`, `minflt`, `nivcsw`, `inblock`, `oublock`, `io_mb` (null without
`/proc`), `rss_mb` (growth) and `lock_ms`. Readers skip unknown kinds and fields, and a line cut short by a crash.

### The runner's flight file

The Rust runner keeps its own flight file, `flight.ndjson` in its state folder (`state-<runner id>/` next to the
registration file, `~/.config/tico/` on a Mac, the runner user's home in Docker, which needs `--config ~/runner.json`), rotated at 10 MB with four older
files (`.1` to `.4`), in the server's record format (version 1): one JSON object per line with `v` (1), `ts` (Unix
seconds, milliseconds as decimals) and `kind`, here always `runner.*`: `runner.start` (version, build,
pid), `runner.turn` (attempt, bot, runtime, outcome, `total_ms`, and `phases`: milliseconds since the turn was claimed
for `placed`, `harness_started`, `harness_spawned`, `first_output`, `first_delta_posted`, then the hand-in's
`model_done`, `scrubbed` (the secret scrub of the changed files), `finished_at`, `saved` (the result in runner.sqlite),
`flushed` (the last events sent), `complete_posted` and `done`), `runner.heartbeat` (round trips of a second or more, and every failure), `runner.git` (git commands of two
seconds or more and every timeout, the scrub's included: verb, checkout folder name, milliseconds, exit code; never arguments) and
`runner.process` once a minute (resident memory, CPU time, finished children's CPU, open turns, and `dropped`: records
lost because the writer fell behind). Turns never wait on the disk. `TICO_FLIGHT_RECORDER=0` turns it off.
`tico-runner flight summary --since 1h` (or `--dir <folder>` for a copied state folder) prints recent turns with their
phase timings, the slowest phases, slow heartbeats and slow git commands.

In-process writers wait FIFO for at most 30 seconds before reporting the existing database-locked error; SQLite's own 30-second busy timeout still covers a writer in another process. A request that exhausts both waits can therefore fail after about 60 seconds, rather than 30. Fresh lease polls can answer without a write. Diagnostic heartbeats are coalesced with lease writes, at most one minute apart while polling continues; a stalled diagnostic forces the next renewal through the write path to clear it.
