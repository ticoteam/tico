# Cell stats and debug tools

Every shared host ([cell mode](cell.md)) can report what it and each company on it is doing to Tico HQ, once a minute,
and run a short list of read-only debug tools when staff ask. Nothing needs AWS: no SSM, no CloudWatch. The host only
makes outbound HTTPS calls to HQ. It sends one POST with its numbers, and HQ's answer carries any debug requests waiting
for it. Nothing on the host listens. Any Linux box that ran `cell init` can report.

The reference implementation is `setup/cell_stats.py`.

## Enroll a host

1. In HQ, staff create the host's key: `POST /v1/staff/cloud/hosts/<host id>/stats-key` with the hosting key.
   HQ shows the key (`tch_…`) once and keeps only its hash. Calling it again rotates the key, and the old one stops
   working at once. `DELETE` on the same path revokes it.
2. On the host, as root, from the core release folder (`/opt/tico-cell/core-<version>`), pass the key on stdin.
   It is never passed as an argument:

```sh
install -m 600 /dev/stdin /root/stats-key <<<"$KEY"
python3 -m setup.cell_stats enroll --hq https://updates.tico.team --host-id cell-1 < /root/stats-key && rm /root/stats-key
```

`enroll` writes `/etc/tico-cell/stats.json` (`{"hq", "host_id", "key"}`, 0600, root). It points
`/opt/tico-cell/stats-core` at the newest `core-v*` folder that has the collector, then installs and starts two units:

- `tico-cell-stats.timer`: every 60 s (`OnUnitActiveSec=60s`, `AccuracySec=5s`, `Persistent=false`).
- `tico-cell-stats.service`: `Type=oneshot`, `WorkingDirectory=/opt/tico-cell/stats-core`,
  `ExecStart=/usr/bin/python3 -m setup.cell_stats push`, `TimeoutStartSec=50`, `Nice=10`, `IOSchedulingClass=idle`.

Each push moves the `stats-core` link to the newest `core-v*` folder that has `setup/cell_stats.py`. So once HQ installs
a new release, the next minute runs its collector. Older folders are never touched.

`python3 -m setup.cell_stats enroll --uninstall` stops the timer and removes the units, the key, the cache and the link.
A rotated key is enrolled again the same way.

## What a push does

`push` collects a snapshot, sends it gzipped to `POST {hq}/v1/cells/{host_id}/stats` with `Authorization: Bearer tch_…`,
and runs the debug requests in the answer. It never retries. A `429` (pushed under 20 s ago), `401` (bad key), `403`
(revoked, or another host's key) or no answer is reported once, as a JSON line on stdout (the unit's journal), and the
next tick tries again. The uncompressed body is at most 512 KB. Past that, the collector drops error lines first, then
flight detail (slow routes, SQL shapes), then whole flight blocks, then companies from the end, and sets
`"trimmed": true`.

The snapshot (`format: 1`) holds:

| Part | From |
|---|---|
| `host` | `/proc/stat` (two reads at least 0.5 s apart, around the Docker calls), `/proc/loadavg`, `/proc/meminfo`, `/proc/uptime`, `statvfs` of `/` (and of Docker's root when it is another disk: `docker_root_free_gb`), `docker system df`, and `oom_kills_1h` from the kernel's `oom_kill` counter in `/proc/vmstat` |
| `router`, `tunnel` | The router's own health answer on `127.0.0.1:8080`; the tunnel container's state and cloudflared's `/ready` on its metrics port (`127.0.0.1:8081`) |
| `companies[].containers` | One `docker ps -a`, one `docker inspect` of every company's containers and one `docker stats --no-stream` for the whole host. The inspect template asks only for the id, name, state, restart count, memory limit, log path and labels. It never asks for `Config.Env` or the mounts |
| `healthz` | `/healthz` through the router with the company's `Host`, 3 s timeout, all companies in parallel. `code` is null when nothing answered |
| `db` | Sizes of `hub.sqlite` and its `-wal`, and `freelist_pct` from the page and free-page counts in the database file's 100-byte header (pages still in the WAL are not counted), every push. No SQLite runs on the host. `files_mb` is the size of `data/blobs` |
| `backup` | the server's own backup loop's status (`/tmp/tico-backup-status.json` in the server container, read through `/proc/<pid>/root`, with no `docker exec`): `age_s` is the newest copy's age, `behind_s` how long the backup location has been behind the database (null when caught up), `checked_s` how long since the loop last read it, `stale` whether either is past the server's warning time, and `unsent_files` the local level-0 pile-up. The block is null when unknown |
| `flight` | The company's flight files (`data/flight/flight.ndjson*`), summarised over the last 5 minutes by the core's own `backend/flight_file.summary`, loaded by path (stdlib only). This works while the server is stuck. Request percentiles are estimated from each route's per-minute p50, p95 and max. `slow_routes` are routes with p95 at or over 500 ms or a request over 1 s. `sql.top` is the top 10 statement shapes by time over this hour and the last, from the `flight_sql` table (literals already folded by the server), read inside the server container (see below); null while the server is not running |
| `errors` | Up to 20 lines from the last 70 s of the server's and updater's logs that contain ERROR, CRITICAL, Traceback or Exception, scrubbed, 300 characters each. They are read from the tail of each container's json-file log (`cell init` sets that driver). `docker logs` runs only when a running container has no log file |

Memory numbers (`mem_mb`, `mem_limit_mb`, `rss_mb`, `memory_limit_mb`) are MiB. Network, disk and database sizes are
decimal MB and GB. Any number is null when it is unknown. Readers ignore keys they do not know.

The slow parts run at most every 15 minutes per host or company, and their results are kept in
`/var/lib/tico-cell/stats-cache.json` (0600): `docker system df`, the top SQL shapes and the `blobs` walk. A push on a
host with 50 companies takes a few hundred milliseconds of the collector's own CPU, plus the three Docker calls.

### What leaves the box, and what never does

Sent: counters, timings, sizes, container states, route templates, SQL statement shapes (literals and numbers folded
to `?` and `N` by the server), stall classes, and scrubbed log lines and debug output.

Never sent: a row of company data, a file from the data folder, a container's environment, a mount outside `/srv` and
`/var/lib/docker` (the `containers` tool shows those as hidden), the stats key, or any secret the scrubber recognises.

The collector runs as root, but a company's server writes its data folder and its container's `/tmp`, so it could
plant a symlink to `/etc/shadow`, to this host's stats key or to another company's files. Every read of what a company
controls (the flight files and `stacks.txt`, `server.pid`, the memory dumps, the backup status through
`/proc/<pid>/root`, the database and WAL sizes, the attachment total, the container log) starts from a folder the
host made and walks down one name at a time with `O_NOFOLLOW`, then takes only a regular file of a bounded size. A
symlink anywhere on the way, a FIFO or an oversized file is skipped: the number is left out, nothing is read.

SQLite never opens a company's database on the host: a check before opening could not stop a swap after it. What needs
SQLite (`sql.top` and the `db` tool's pragmas, largest tables and `quick_check`) runs inside the company's own server
container with `docker exec`, as that container's user (no `-u`), with the server's python
(`/opt/tico/.venv/bin/python -I -c`), a fixed script and a read-only URI. Its arguments are the mode, `/data/hub.sqlite`
and numbers the collector made; nothing from HQ. In there a symlink can reach only the container's own files. The
script stops itself at its time cap and its JSON answer is checked and scrubbed like any other company output. While
the server is not running, those parts say `server not running`.

The scrubber runs on every free-text string that leaves the host: error lines, SQL shapes, route names and every debug
output. It replaces these with `[redacted]`:

- everything after a `Cookie`, `Set-Cookie` or `Authorization` name, or a name ending in `key`, `token`, `secret`,
  `password`, `passwd` or `session` (such as `api_key`), and its `=` or `:`, to the end of that line. The name may be
  quoted, also with escaped quotes (`\"token\":`, escaped once or more, as in JSON a log line carries); the value is
  not parsed, so escaped quotes inside it cannot end it early
- `Bearer …`
- the key prefixes `tch_`, `tcs_`, `tgi_`, `tico_`, `sk-`, `sk_`, `ghp_`, `gho_`, `github_pat_`, `xox[abpr]-`, `AKIA`
  and `ASIA`
- `eyJ…` JWTs
- email addresses
- URL query strings (`?…` becomes `?[redacted]`)
- runs of 32 or more hex characters
- runs of 32 or more base64 characters that mix digits with upper- and lowercase letters

## Debug tools

Staff queue a request at HQ: `POST /v1/staff/cloud/hosts/<host id>/debug` `{"tool", "args", "by"}` with the hosting
key. HQ checks it against the table below, keeps at most 5 queued per host, and expires requests after 10 minutes.
It hands each request out once, in the answer to the host's next push. The host checks every request again against
its own copy of the table, with exact keys and exact types. HQ's check is not trusted. A refused request is answered
`{"ok": false, "output": "refused: …"}` and runs nothing. The host runs each tool with its own timeout. The tools get
at most 45 s per tick. Whatever is left waits in `/var/lib/tico-cell/stats-debug.json` for the next tick. Each result
is posted to `POST {hq}/v1/cells/{host_id}/debug/{id}` as `{"ok", "ms", "output"}`. The output is scrubbed and cut at
256 KB, with a note that it was cut.

| Tool | Args | Does |
|---|---|---|
| `host_top` | none | `top -b -n1` (first 40 lines), `free -m`, `df -h`, `uptime`, `docker system df` |
| `cell_status` | none | `python3 -m setup cell list --json` |
| `containers` | `company` (optional) | `docker inspect` of chosen fields (no environment; mount sources outside `/srv` and `/var/lib/docker` hidden; Compose and cell labels only) and `docker stats --no-stream` |
| `logs` | `company`, `role` (`server` or `updater`), `minutes` 1–60, `lines` 1–1000 | `docker logs --since … --tail … --timestamps` |
| `flight` | `company`, `minutes` 1–1440 | The full `flight_file.summary`: stalls with stacks, slow requests, routes, lock holders, per-minute rows |
| `db` | `company` | Read-only. On the host: sizes and the header's page size, page count and free pages. In the running server container: `page_size`, `page_count`, `freelist_count`, `journal_mode`, `wal_autocheckpoint` (that connection's), the 15 largest tables and indexes (`dbstat`, when SQLite has it, 20 s cap), `PRAGMA quick_check` (55 s cap, within the tick's budget); `server not running` otherwise |
| `stacks` | `company` | SIGUSR1 to the server process. Its host pid is found from `flight/server.pid` by `NSpid` in the container's pid namespace, and its command line must run `backend.server`. The tool returns what faulthandler appended to `flight/stacks.txt` |
| `tracemalloc` | `company`, `action` (`start` or `dump`) | SIGUSR2 to the same process. `start` turns tracemalloc on. `dump` returns the top 50 allocation sites (`flight/memory-<time>.txt`) and turns it off. Whether it is on is read from the flight file, so `start` is refused while it is on and `dump` while it is off |
| `journal` | `unit` (`docker`, `containerd`, `cloudflared`, `caddy` or `tico-cell-*`, optionally `.service`/`.timer`), `minutes` 1–60 | `journalctl -u … --since "<n> min ago"` |

There are no other commands, no shell strings and no paths in arguments. A company must be a slug of a company on this
host (not removed).

## On the box

```sh
python3 -m setup.cell_stats show                         # the snapshot as a table; nothing is sent
python3 -m setup.cell_stats show --company boatyard --json   # one company's block in full
python3 -m setup.cell_stats debug-run flight --args '{"company": "boatyard", "minutes": 30}'
python3 -m setup.cell_stats debug-run journal --args '{"unit": "tico-cell-stats", "minutes": 60}'
systemctl status tico-cell-stats.timer; journalctl -u tico-cell-stats -n 20     # the last pushes' one-line reports
```

`debug-run` runs a tool exactly as a staff request would (same checks, same scrubbing) and prints its output.
