# The load gate

A repeatable mixed load of people and runners against one Tico server, pinned to a commit, whose report tells the
server's limits from the sender's own lag. It is how the stream, writer and runner changes are verified, and the
only basis for a statement like "Tico holds N people and M bots" on a given host. It runs only fictional data
(Acme, `example.test`) on its own throwaway server and database, never against production or a shared system.

## Run it

One command, from a checkout of the commit to measure (the sender's checkout names the commit):

```sh
# Two hosts: the server over ssh (its checkout at --remote-dir, set up with scripts/dev_setup.sh), the sender here.
python -m bench.load_gate run --seed 7 --server-ssh gate-server --remote-dir /srv/tico --listen 0.0.0.0:8800 \
    --out reports/gate-$(git rev-parse --short HEAD)

# The gate host's port must be open to the sender host only: the server answers to the fixture's fixed tokens.

# One machine (the fallback when no second host is available; the report says so): pin server and sender apart.
python -m bench.load_gate run --seed 7 --server-cpus 0-3 --sender-cpus 4-7 --out reports/gate-local
```

`run` checks the server's checkout out at the sender's commit, builds the synthetic company once per seed and size
(cached under `--cache`, copied before every run), serves it, sends the levels 30, 60, 120 and 300 turns at once
(each ramps for `--ramp` seconds, then holds `--hold` seconds, 600 by default, steady), reads the server's flight
recorder for exactly each steady window, writes `report.json` and `report.md`, and stops the server. A full run takes
about an hour; building the full company the first time takes several minutes more.

The steps can also run apart: `build` (the company only), `serve` on the server host (its first stdout line is the
manifest the sender needs: save it), `send --target URL --manifest FILE` on the sender host. `--size smoke` is the
small company the test suite runs (`backend/tests/test_load_gate.py`, a few seconds of load against an in-process
server).

Compare two reports per route (repeatability, Measure 3: two runs of one commit within 15% for routes with at least
1,000 requests, judged on the sender's p95, with the server's p95 allowed one recorder bucket; a deliberate change,
Measure 4: the slowed route named):

```sh
python -m bench.load_gate compare reports/a/report.json reports/b/report.json
```

The sender's p95 is exact; the server's comes from the flight recorder's histogram, whose buckets are 25% apart, so
`compare` counts two server values one bucket apart as within (the recorder cannot tell them closer). Judge
repeatability on routes with at least 1,000 requests, on a dedicated host: on a shared machine, other work moves the
small routes' p95 by a bucket or two.

## What it sends

The company (`company.py`, sizes in the report): 50 people, 300 bots on 8 computers, 900 routines (one in ten fires
every 15 minutes, the rest once a day), 600 live tasks, 1,500 company threads, and a history of 40,000 closed tasks,
400,000 messages, 60,000 finished runs with 1,000,000 run events and 90 days of daily updates. Production on
2026-11-12 grew by about 340 tasks, 2,700 messages, 1,100 runs and 170,000 run events a day with 49 bots: the history
is a year of tasks and messages at that rate, and fewer run events than a year would hold.

The load (`sender.py`, proportions in the report): every person holds the live stream open and, about every 2.5
seconds at random (Poisson) times, opens Home, Needs you, a task list, search, a task, its comments, a thread or a run
view, or writes a comment, a thread message, a move or a chat. Each computer sends its heartbeat every 30 seconds.
Each turn slot claims a run, takes its place, starts, posts events every 2 seconds (sizes from 80 to 16,000
characters), polls inputs every second, renews every 20 seconds and completes after 20 to 120 seconds; no model is
called. Routines the server fires are claimed the same way. Not sent yet: approvals (the company has no asks).

People's actions and heartbeats are open-loop: every one has a planned time and goes out then, whether or not
earlier ones have answered, so a slow server shows as slow answers and not as less load. A turn's steps are chained,
as a runner's are: each goes out at its slot or after the previous step's answer, whichever is later. So the sender's
p95 on runner routes leaves out time spent queued behind a slow step; the server's own p95 does not depend on it.
People sign in with fixed test tokens, not the session sign-in browsers use; runners use their real enrollment tokens.
The report's `plan` says both. The sender runs in several processes
(`--workers`); each request records how late it went out against its plan.

## Read the report

Per level, the run is **valid** only when the sender kept its schedule: p95 lateness at most 50 ms, no send more
than a second late, the sender loop's own oversleep (sampled every 100 ms) at most 50 ms at p95, and no sender process
above 90% of a CPU in the steady window. The loop's oversleep matters at high levels, where most requests are chained
turn steps that show no lateness of their own. A level marked **not valid** measured the sender, not
the server: do not use its numbers; add sender processes or a second sender host, never less load.

Per route: requests, 5xx errors, 4xx refusals, the sender's p50, p95 and p99, and the server's own p50 and p95 from
its flight recorder for the same window. The sender's time minus the server's is the network and the sender; the
server's own time is what a change to the server moves. Per level: the server's CPU and memory, the write lock's
longest wait, longest hold and p95 hold (the flight recorder's per-minute rows), and live delivery delay: the time
from a person's write (its marker carries the send time) to the event reaching any person's live stream.

A warning under a level says the server's flight recorder holds under 90% of the requests the sender counted: a
recorder minute is probably missing, so read its server numbers with care.

The head names the commit that built the cached company, the commit the sender ran, the commit the server reported at its start, and whether they match with
no local changes (`pinned`); the host sizes, data sizes, seed and plan.

## Budget

`--budget FILE` checks each level against budget lines and marks each met, missed or no data. The file holds the
lines of the performance budget the Performance Owner keeps, as JSON:

```json
{"lines": [
  {"id": "Needs you p95", "metric": "route_server_p95_ms", "route": "GET /api/v2/needs-you", "max": 300,
   "source": "performance-budget.md: Needs you"},
  {"id": "write lock wait", "metric": "lock_wait_max_ms", "max": 1000}
]}
```

Metrics: `route_server_p95_ms`, `route_sender_p95_ms` (with `route`), `server_p95_ms`, `lock_wait_max_ms`,
`lock_hold_p95_ms`, `server_cpu_avg`, `live_delivery_p95_ms`, `error_share`. A missed line blocks nothing: the
report only says so.
