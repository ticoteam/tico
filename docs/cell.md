# Many companies on one host (cell mode)

`python3 -m setup cell` runs many companies' Tico servers on one shared Linux host. Tico uses it for its cloud
companies (`<slug>.tico.team`); Tico HQ drives it over AWS SSM. Staff also use it to move an existing install onto the
host, with its own domain, settings and data ([Moving an install in](#moving-an-install-in)). Each company gets the same server a
[hosted install](install-advanced.md) gets: the stock `ghcr.io/ticoteam/tico` image at a pinned release, the stock
entrypoint and `compose.yaml`, its own data folder, `.env` and Compose project, sign-in through its own OpenID
Connect client, the owner sign-in link kept as break-glass, and the recommended setup. It does not get its own Caddy,
cloudflared, updater sidecar or EC2 instance. Every company shares one router, one Cloudflare Tunnel and one cell
updater, which speaks the sidecar's contract to each company on its own address, so Update now, update keys and Tico
support's idle-time updates work as on a hosted install ([Updates](#updates)). The host runs only Tico
servers. It never runs bots or a runner.

The reference implementation is `setup/cell.py`.

## The host

Use Ubuntu 24.04 on arm64 (Tico runs m8g.xlarge). Run the command as root from a checkout of the release (setup and
its `compose.yaml` come from that checkout). Before you run `init`, on the instance:

- **Require IMDSv2 with hop limit 1**:
  `aws ec2 modify-instance-metadata-options --instance-id <id> --http-tokens required --http-put-response-hop-limit 1`.
  With this setting, a container cannot get a metadata token. The firewall rule below is the second lock.
- Keep `10.208.0.0/12` and `10.224.0.0/16` free. The companies' networks use these ranges, so they must not overlap
  the VPC or a peered network.

```sh
install -m 600 /dev/stdin /root/tunnel-token <<<"$TOKEN"     # the tunnel's token, readable by root only
python3 -m setup cell init --tunnel-token-file /root/tunnel-token --domain tico.team --json
```

`init` is idempotent. Re-run it after you change anything. It does the following:

1. Installs Docker and Compose if they are missing.
2. Adds log rotation and `live-restore` to `/etc/docker/daemon.json`. With `live-restore`, restarting Docker leaves
   every company running. Settings already in that file are kept.
3. Adds a firewall rule so containers cannot reach the EC2 metadata service at `169.254.169.254`. The rule is a
   `DOCKER-USER` REJECT, set by `/usr/local/sbin/tico-cell-firewall`. It must be the chain's first rule, so no rule
   above it can accept the traffic first; the script inserts it at position 1 whenever it is not first. That script
   runs before Docker starts, from `tico-cell-firewall.service`, and again after every Docker start, from a drop-in.
   `init` checks it is first (`iptables -S DOCKER-USER`), and `add` refuses to start a company when it is missing or
   not first.
4. Starts the router and the tunnel in Compose project `tico-cell` (`/srv/tico-cell/edge.yaml`). The router is Caddy,
   serving plain HTTP on `127.0.0.1:8080`. It sends each company's host name to that company's loopback port (or, for
   a sleeping company, to the wake service), and answers anything else with a 404. The tunnel sends `*.<domain>` to the router, and before that wildcard, every
   company host it does not match (see [Moving an install in](#moving-an-install-in)). Both containers use the host's
   network and listen on loopback only. Both drop all Linux capabilities, set `no-new-privileges` and a read-only
   root filesystem. The router adds back only `NET_BIND_SERVICE` (Caddy's binary needs it to exec). The tunnel
   directory `/srv/tico-cell/tunnel` is 0700 and owned by uid/gid 65532, the user cloudflared runs as; every `init`
   sets that owner again. The token is kept in `/srv/tico-cell/tunnel.env` (0600). Without
   `--tunnel-token-file`, the token from an earlier run is used, or only the router runs.
5. Installs and starts `tico-cell-updater.service`, the companies' updater ([Updates](#updates)), running
   `python3 -m setup.cell_updater` from this checkout. It is restarted only when its unit changes, which is when
   `init` runs from another checkout, and started if it is not running.
6. Installs and starts `tico-cell-wake.service`, which starts a sleeping company on its first request
   ([Sleep, wake and the hold](#sleep-wake-and-the-hold)), running `python3 -m setup.cell_wake` from this checkout on
   `127.0.0.1:8082`. It is restarted and started the same way as the updater.

`--page-domain DOMAIN` (for example `tico.page`, not the cell's domain) sets the domain the companies' page hosts sit under.
`init` then adds one tunnel route, `*.<page-domain>`, beside the cell's own. `--page-domain ""` removes it. In Cloudflare,
the page domain needs the same wildcard record as the cell's, a CNAME to the tunnel, in its own zone.

In Cloudflare, give the tunnel a wildcard DNS record `*.<domain>` (a CNAME to the tunnel). Set no Public Hostnames
of its own on the tunnel: cloudflared prefers routes held in the dashboard over the file `init` writes. Names that
already have their own DNS record, such as `hub` and `accounts`, are not affected.

## Companies

```sh
python3 -m setup cell add --company boatyard --domain boatyard.tico.team --owner-email owner@example.com \
  --auth oidc --issuer https://accounts.tico.team --client-id tc_... --client-secret-file /root/boatyard/client-secret \
  [--gateway-invite-file F] [--backup-url s3://bucket/prefix --backup-region us-west-2 --backup-credentials-file F] \
  [--company-file tico.company.yaml] [--company-name "Boatyard"] [--memory 2g] [--version vX.Y.Z] \
  [--hosted own|invited] [--signin-label "Tico account"] [--timeout 300] \
  [--own-domain] [--alias HOST]... [--page-host HOST] [--env-file F] [--import-data FILE] [--sleep-minutes N] --json
python3 -m setup cell list --json
python3 -m setup cell status --company boatyard --json
python3 -m setup cell stop|start --company boatyard --json
python3 -m setup cell sleep --company boatyard [--wake-at 2026-10-30T18:00:00Z] --json
python3 -m setup cell set-sleep --company boatyard --minutes N|default --json
python3 -m setup cell wake --company boatyard [--timeout 300] --json
python3 -m setup cell update --company boatyard --version vX.Y.Z --json
python3 -m setup cell remove --company boatyard [--keep-data] --json
```

### Answers

Each command prints exactly one JSON object on stdout and exits 0 or 1. An error looks like this:
`{"ok": false, "error": "<code>", "detail": "..."}`. The error codes are:

| Code | Meaning |
|---|---|
| `bad_args` | A flag is missing or a value is invalid |
| `bad_slug` | The slug is not 1 to 40 lowercase letters, digits and single hyphens |
| `reserved_name` | The name is one of Tico's own (the list below) |
| `bad_domain` | `--domain` is not `<slug>.<the cell's domain>` (without `--own-domain`), or `--domain`/`--alias` is not a plain hostname, is the cell's domain, or repeats a host |
| `domain_taken` | Another company has this host as its domain or an alias |
| `secret_file` | A secret file is missing, empty, readable by others or has the wrong content |
| `company_file` | The company file is not a valid company YAML |
| `data_exists` | `--import-data`, but the company's data folder is not empty. Nothing was touched |
| `bad_archive` | `--import-data`'s file is missing, a symlink, not a .tar/.tar.gz, or has a member it refuses. Nothing was written |
| `import_failed` | Unpacking the archive failed (for example, the disk is full). Nothing is left of it |
| `not_initialized` | `init` has not run, or the metadata firewall rule is missing or not first |
| `not_found` | No such company, or it was removed |
| `not_sleeping` | `wake` on a company that staff stopped (`stop`); `start` starts it |
| `busy` | Another command holds the lock too long |
| `full` | All 4,000 slots are in use |
| `use_update` | `add --version` names another release than the company runs; use `update` |
| `pull_failed` | The image could not be pulled |
| `stop_failed` | The server could not be stopped, or Docker still says it runs. From `update`: nothing changed and the old release runs again |
| `snapshot_failed` | `update` could not copy the database; nothing changed and the old release runs again |
| `docker_failed` | A Docker or Compose call failed |
| `router_failed` | The router refused its new config (the old config stays) |
| `firewall_failed` | The metadata firewall rule could not be set |
| `unhealthy` | `/healthz` did not answer through the router in time (`wake`: on the server's own port), and the detail has the last log lines |
| `update_failed` | The new release did not come up and the company is back on the old one and its database (`version` says which) |
| `updater_failed` | `init` could not install or start the cell updater or the wake service |
| `rollback_failed` | The new release did not come up, and putting the old one back failed too; the detail says how far it got (`version` is the release in `.env` now) |
| `unsupported_host` | `init` was run somewhere other than the Linux host |
| `internal` | A bug. The detail has the cause, with secrets masked |

`add`, `start`, `wake` and `update` answer like this:

```json
{"ok": true, "company": "boatyard", "url": "https://boatyard.tico.team", "port": 20000, "ready_ms": 8400,
 "version": "v0.5.1", "changed": true, "owner_link_file": "/srv/tico-cell/companies/boatyard/owner-link.txt"}
```

`add` adds `"ignored"` (the `--env-file` keys it did not keep, names only) and `"unused"` (kept keys the stock
`compose.yaml` does not pass to the server, so they do nothing) when given `--env-file`, and `"imported": true` with
`"imported_bytes"` (the size of the files unpacked) when given `--import-data`.

They return only once `/healthz` answers through the router (`wake`: on the server's own port, since the route
changes only after that). `ready_ms` is the time from the start of the command
until then. `changed` is false when nothing needed doing. The other commands answer like this:

- `list`: `{"ok": true, "companies": [{"company", "url", "aliases", "port", "version", "memory", "state"}]}`.
- `status`: the same fields as a `list` entry, plus `healthy` (whether `/healthz` answers through the router now;
  always false for a sleeping company, which is not probed, since a probe would wake it) and `container`
  (`{"state", "health"}`).
- `stop`: `{"ok": true, "company", "state": "stopped", "changed"}`.
- `sleep`: `{"ok": true, "company", "state": "sleeping", "changed"}`.
- `remove`: `{"ok": true, "company", "removed", "kept_data"}`. `removed` is false when there was nothing to remove.

A company's `state` is one of `running`, `starting`, `unhealthy`, `stopped`, `sleeping`, `missing` (no container) or
`removed`.

### Rules

- **Secrets come only from files.** These are the client secret, the Gateway invite, the backup key and the tunnel
  token. Each file must be readable by its owner only (mode 0600 or stricter). Secrets never appear in argv, and never
  on stdout: every detail and log line is scrubbed. They are written only to the company's `.env` (0600).
- **Every command is idempotent.** Running `add` again with the same settings changes nothing and gives the same answer
  (`changed: false`). Running it with new settings, such as a rotated client secret, applies them. An optional input
  that is left out keeps its saved value: the release, memory, Gateway invite, backup settings, company file,
  aliases and carried settings (`--env-file`). A
  failed `add` leaves the company in place, so the same `add` resumes it. `add` never changes a company's release:
  `--version` with another release is refused (`use_update`), since a new release migrates the database.
- **Reserved names:** hub, admin, accounts, updates, preview, runner, www, api, app, mail, status, docs, help,
  support, hq, gateway, hooks, cloud, tico, team.
- **The backup key file** is either `KEY=VALUE` lines (`LITESTREAM_ACCESS_KEY_ID` and `LITESTREAM_SECRET_ACCESS_KEY`,
  or the `AWS_` names), or the JSON that `aws iam create-access-key` prints. Give each company its own key, limited to
  its own prefix of the bucket. Containers cannot use the host's role.
- **Ports and networks.** Each company gets the smallest free slot `i`. The slot sets its loopback port
  (`20000 + i`), its default network (the `i`-th /24 of `10.208.0.0/12`) and its edge network (the `i`-th /28 of
  `10.224.0.0/16`). The slot is saved in `company.json`, so it survives restarts. It is freed only by `remove`
  without `--keep-data`.
- **Updates.** `update` pulls the new image first: if the pull fails, nothing changes. It then stops the server and
  waits until Docker says it is stopped, copies its database to `snapshots/` (the last two are kept), and only then
  points `.env` at the new release, with the update marked pending in `company.json`. If the stop or the copy fails,
  nothing changes and the old release is started again. A running company starts the new release at once. A stopped
  company stays stopped, and its next `start` (or `add`) starts the new release; a sleeping one stays asleep, and its
  next request (`wake`) starts it. Either way, while the update is
  pending, a release that does not answer `/healthz` is rolled back: the server is stopped (Docker must say so before
  the database files are touched), the database copy and the old release are put back, and the old release must
  answer `/healthz`. `update` back to the old release while an update is still pending is that same rollback.
- **Remove.** `remove` deletes the containers, volumes, route and folder. With `--keep-data`, it deletes only the
  containers and the route. The folder (data, `.env`) and the slot stay, and a later `add` brings the company back.
  The slot is freed only after the router has reloaded without the company's route, under the cell lock, so its port
  never reaches another company while the old domain still points at it. If the router refuses the new config, the
  company stays marked `removed` with its slot, and running `remove` again finishes.
- **Locks.** Commands on different companies run in parallel. Only allocating or freeing a slot and rewriting the
  router config share a lock. A command reads a company's record only once it holds that company's lock.

## Sleep, wake and the hold

People and senders (webhooks, mail, runners) never get a 502 from the router while a company's server is down for a
moment or asleep.

- **The hold.** Each company's route retries a dial that fails (nothing listens on its port: the server is starting,
  restarting or updating) every 250 ms for up to 30 s (`lb_try_duration`, `lb_try_interval`), so the request waits
  and gets the server's real answer. Only a failed dial is retried for every method, since nothing reached the server;
  an error after the connection opened is retried for `GET` only, so a write is never sent twice. What still fails is
  answered with a plain `503` and `Retry-After: 5` ("Tico is starting. Try again in a few seconds."), never a 502
  page. Once a request reaches the server there is no time limit: long answers, SSE (`/api/v2/events`) and
  WebSockets work as before. A router reload (every `add`, `remove`, `stop`, `start`, `sleep` and `wake`) leaves open
  WebSockets 5 minutes before it closes them.
- **Sleep.** `sleep` points the company's hosts at the wake service, then stops its server (Docker must say it is
  stopped). The record's state is `sleeping`. If the stop fails, the company is left as it was. When to sleep a
  company (idle rules) is up to the caller.
- **Sleep requests and timed wakes.** A company's server can ask its updater to sleep it: `POST /sleep` on its updater
  address with its updater token ([Updates](#updates)), body `{"wake_at": "2026-10-30T18:00:00Z" | null, "reason":
  "idle"}`. The answer is `202 {"state": "sleeping", "wake_at"}` at once, and the sleep itself runs a second later, so
  the answer gets back before the server stops. `409 {"error": "busy"}` means an update or another cell command holds
  the company, and nothing changes. An update accepted in that second cancels the sleep (the server asks again later);
  one that comes once the server is stopping gets a 409. A later sleep request in that second replaces the earlier
  one. A `wake_at` that is not an ISO-8601 UTC time, or is more than 31 days out, is
  `422 {"error": "bad_wake_at"}`. `sleep --wake-at` does the same from the command line. The record keeps the time, and
  the wake service starts the company once that time is within a minute, checking every 20 s; a start that fails is
  logged and tried again at a later check, not at once. Each timed start checks the time again under the company's
  lock, so a time staff moved or cleared meanwhile starts nothing; a request always wakes the company. Starting,
  waking or stopping the company clears the time, and a sleep without one clears a time an earlier sleep set.
- **Sleep policy.** A company's record can carry `sleep_minutes`: idle minutes before it sleeps, 0 to 1440, where 0 means
  never sleeps and no value means no policy. Staff set it with `set-sleep --minutes N` (`--minutes default` removes
  it) or `add --sleep-minutes N`; a re-run of `add` without the flag keeps the saved value. The company's updater
  answers `GET /sleep` with `200 {"minutes": N | null}` from the record, behind the same token and network gate as
  `GET /status`, so a change is seen on its next read. Nothing here sleeps a company by itself: the caller decides.
- **Wake.** The wake service (`tico-cell-wake.service`, `setup/cell_wake.py`) listens on `127.0.0.1:8082`. The
  router reaches it for a sleeping company's hosts only, and the route itself names the company in a header
  (`X-Tico-Cell-Wake`; a copy the visitor sent is replaced). The service also checks that the request's `Host` is one
  of that company's own hosts, so a request never chooses which company starts. It holds the request, runs `wake`
  for that company (one start, however many requests arrive), then sends the request on to the server as it came
  (method, headers and body, streamed both ways) and returns the server's real answer. `wake` takes the company's
  lock, starts the server the same way `start` does (a pending update is finished or rolled back there too), waits
  for `/healthz` on the server's own port, then marks the company `running` and reloads the router, so later
  requests go straight to the server. Staff can run `wake` by hand too.
- **What the wake service answers itself.** All of these are short plain-text answers, never a 502 page:
  - A wake that fails, or does not finish within 30 s: `503` with `Retry-After: 5`. A start that does not come up is
    stopped again, so the company stays asleep. For 10 s after a failed wake, requests get the 503 at once, so a flood
    is not a start loop. A start that is still running when its request gives up carries on, and later requests use
    it.
  - `GET` or `HEAD /healthz`: `503`, and the company is not woken. Cell stats and monitors probe every company, and a
    probe must not undo a sleep.
  - A WebSocket upgrade wakes the company, then gets `503` with `Retry-After: 1`. The client's reconnect goes straight
    to the server.
  - Caps: at most 512 connections at once, 256 requests waiting for a wake in all and 64 for one company. Past a cap,
    `503` at once. A body is streamed, never stored, and refused with `413` when it is larger than the server accepts
    (`TICO_UPLOAD_MAX_BYTES`, 2 GiB by default, plus 11 MB).
- **Stopped is not asleep.** A company staff stopped (`stop`) is answered `503` at once ("This Tico company is
  stopped."), and nothing wakes it. `start` routes it back to its server.
- **Stopping a server is quick.** On `docker stop` (a restart, an update, `sleep`), the server's entry point gives the
  server 1 s to finish what it is answering (`TICO_STOP_GRACE` in the image), then kills it. Only then does it stop
  Litestream, so Litestream's last sync covers every write (at most 5 s, `shutdown-sync-timeout`) and its local state
  is current for the next start. Before, the server's own shutdown outlasted Docker's 10 s stop timeout, and the
  SIGKILL that followed also cut off Litestream's last sync.

## Moving an install in

Staff only; HQ never passes these. To move a self-hosted install (for example Tico's own at `hub.tico.team`) onto the
cell:

1. Stop the old server, then archive its data folder's contents: `tar -C /data -czf /root/data.tar.gz .` (0600, or
   owned by root). Copy its `.env` keys that the cell does not write to a 0600 file.
2. Run `add` with the old install's sign-in settings and these flags:

```sh
python3 -m setup cell add --company ticoteam --own-domain --domain hub.tico.team \
  --alias runner.tico.team --alias hub.ticofiles.com --company-name "Tico Team" --signin-label "..." \
  --env-file /root/ticoteam/extra.env --import-data /root/data.tar.gz \
  --owner-email ... --auth oidc --issuer ... --client-id ... --client-secret-file F --version vX.Y.Z --json
```

3. Point DNS at the tunnel (below), then check the answer's `ignored` and `unused`.

- **`--own-domain`**: `--domain` may be any plain hostname (lowercase DNS labels with at least one dot, not an IP, not
  the cell's domain) instead of `<slug>.<the cell's domain>`. Reserved names limit only slugs, so the slug is
  `ticoteam` and the domain can still be `hub.tico.team`. Pass the flag on every `add` for that company.
- **`--alias HOST`** (up to 5): more hostnames for the same server, such as the runner address (`TICO_RUNNER_URL`'s
  host) and the visual address (`TICO_VISUAL_ORIGIN`'s host). The same rules as an own domain; not the domain itself
  and not twice. No host (domain or alias) belongs to two companies that are not removed. Passing `--alias` replaces
  the saved list; `--alias ""` alone clears it; leaving it out keeps it. The router sends the domain and every alias to
  the server's port.
- **The tunnel and DNS.** Cloudflare's `*.<domain>` matches exactly one label: `hub.tico.team` and `runner.tico.team`
  yes; `a.b.tico.team` or `tidy.rocks` no. `add` and `remove` write every host it misses into the tunnel's routes,
  ahead of the wildcard, and restart the tunnel when that list changes (cloudflared reads its routes only when it
  starts, so every company drops for a few seconds; a restart that fails puts the old routes back, and the next run
  tries again). DNS is staff's job: give each such host a proxied CNAME to the tunnel (`<tunnel id>.cfargotunnel.com`),
  in its own zone if it has one (`hub.ticofiles.com`). A host under the wildcard that has a DNS record of its own
  (`hub.tico.team` and `runner.tico.team` point at the old instance today) must be changed to that CNAME or deleted.
  Cloudflare's universal certificate covers one label below the zone, so a deeper name needs a certificate of its own.
- **`--page-host HOST`** (for example `acme.tico.page`): the company's [Company page](company-page.md) host. The cell
  serves its files from `pages/<slug>/current` in the company's folder, without waking the company: the home page,
  `data.json`, `feed.xml` and `/assets/*`, for GET and HEAD only, and never with a reader's cookie or a query string.
  Every other request on that host goes to the company's server, as for its domain. The host is refused with
  `bad_domain` when it is the company's own domain or an alias, and with `domain_taken` when another company has it.
  Leaving the flag
  out keeps the host; `--page-host ""` clears it and keeps the folder. `remove` deletes the folder unless
  `--keep-data` is given. `list` and `status` show `page_host` and `page_url` when one is set.
  A host under the page domain needs no route of its own; any other host gets one, as an alias does.
- **`--env-file F`** (0600, `KEY=VALUE` lines, keys `[A-Z][A-Z0-9_]*`, values on one line): settings the cell does not
  write, such as `TICO_RUNNER_URL`, `TICO_VISUAL_ORIGIN`, `TICO_BLOB_BUCKET`, `TICO_BLOB_REGION`, `TICO_INBOUND_KEY`,
  `TYPESAFE_API_KEY`, `TICO_STALE_REVIEW`, `TICO_SUPPORT_ACCESS`, `TICO_OIDC_ALLOWED_DOMAINS`. They are kept in
  `extra.env` (0600) and added to `.env` on every `add` and `update`. The cell refuses (lists in `ignored`, never
  keeps) every key it writes with a value, the keys a later `add` reads back from `.env` (team name, owner, Gateway
  invite, owner link, backup, company file), its sign-in, network, preset and support keys, and the ones that belong
  to a single-company install: `CLOUDFLARE_TUNNEL_TOKEN`, `COMPOSE_PROFILES`, `COMPOSE_PROJECT_NAME`,
  `TICO_UPDATER_URL`, `TICO_UPDATES`, `TICO_CELL`, `TICO_TAG`, `TICO_PORT`, `TICO_EXPECTED_AWS_ACCOUNT`,
  `TICO_EXPECTED_AWS_ROLE`, `TICO_CADDY*`, `TICO_TUNNEL*`, and the page's `TICO_READER_ORIGIN` and
  `TICO_COMPANY_PAGE_DIR`. So pass the team name and sign-in button as `--company-name` and `--signin-label`,
  not in the file. The values are secrets: never printed, masked in every detail. Passing `--env-file` again replaces
  `extra.env`; leaving it out keeps it.
- **`--import-data FILE`** (a `.tar` or `.tar.gz` of the contents of the install's `/data`: `hub.sqlite` and its
  `-wal`, `credential.key`, `github-app.key`, `tico-session-secret`, `visual-secret`, `registry/`, `blobs/` and the
  rest): only into an empty or missing data folder (else `data_exists`). The whole archive is checked first and refused
  (`bad_archive`) for an absolute name, a `..`, any symlink (the server makes none), a hard link to anything but a file
  earlier in it, a device, fifo or other special file, or no `hub.sqlite` at its top (an archive of the folder rather
  than its contents). It is unpacked into `.data-import/` beside `data/`, with Python's `tarfile` data filter as a
  second check, owned by user 10001, and renamed to `data/` in one step before the server first starts, so a failed
  import leaves nothing. Once imported, run `add` again without `--import-data`.

## Updates

A company updates through the same contract as a hosted install's updater sidecar (`docker/updater.py`): its server
calls `POST /update` and `GET /status` at `TICO_UPDATER_URL` with the token in `/control/updater-token`. So Update now,
an update service key (the Release Manager's rollout), Tico support's idle-time target, the `system.update` events and
Health's Version line work unchanged. The answers, states and 409s are the sidecar's; `GET /diagnostics` has
`"mode": "cell"` and a `note` when the host's core lags. The reference implementation is `setup/cell_updater.py`.

- **One service for the host.** `tico-cell-updater.service` (installed by `init`) holds one listener per company. It
  runs as root because the work is `update`: it runs Docker and writes the company's folder, as staff's commands do.
  It is a systemd unit, not a container, so it needs no Docker socket mounted into anything. It never changes its own
  code: it runs the checkout `init` last ran from, and it never runs `init` (that restarts it, and the router and
  tunnel stay with staff). A stop lets an update under way finish first.
- **The work is `update`.** An accepted request runs this host's own `cell update` for that one company: pull first,
  stop, snapshot, the new release health-checked through the router, and the old release and database put back when
  it does not come up (state `rolled_back`, `restored: true`). It takes the company's lock, so a staff `update` (or
  `add`, `stop`) running on that company makes the request a 409, and a request under way makes staff's command wait,
  then answer `busy`. Each company's last status is kept in `updater-status.json`, so a restarted service still
  answers it.
- **Which releases.** `latest` or a stable `X.Y.Z` (else 422), published on GitHub (its `SHA256SUMS` downloads), and
  no older than the company runs (409, as the sidecar refuses it). A release it may not run fails with a message
  saying why, and nothing changes.
- **A release newer than the host's core** runs with its own core, since a company's `compose.yaml` is the core's
  own file and a later release may need settings or mounts an older one does not pass. The updater installs that
  release's core beside the others, as HQ does: `/opt/tico-cell/core-<vX.Y.Z>`, from GitHub's tag archive (at most
  256 MB, 5 minutes), unpacked into a staging folder `.core-<vX.Y.Z>.<random>` with its top folder stripped, then
  renamed into place. An archive with an absolute name, a `..`, a link out of the tree, a device or a FIFO installs
  nothing. A folder already there is used as it is (it must have `setup/cell.py`). Then it runs that folder's own
  `python3 -m setup cell update --company <slug> --version <tag> --json` as a child, which takes the company's lock
  itself, and maps its answer to the same states. The host's `init` stays at the older core until staff move it;
  the status message and `note` say so ("updated with core vX; host init still at vY"). A core with no
  `.tico-installer.json` is known by its folder's name (`core-vX.Y.Z`); one that says neither treats every release
  as newer.
- **Isolation.** Company `i`'s listener is bound to its default network's gateway, `10.208.i.1:8090`, never a public
  or loopback address, so nothing off the host and nothing through the router reaches it. Linux accepts a packet for
  any host address from any bridge, so another company's container can open a connection there. So the listener
  answers only a peer inside company `i`'s own subnet (only its containers are on it, and servers drop every
  capability, so they cannot fake an address), then only that company's own token, compared in constant time. The
  token is in the company's `control/` folder, which only its server mounts; a symlink or a short file is no token.
  The company comes from the cell's record (its slug and slot, checked again on each request), never from the
  request; the body is read for a version and the running release only, at most 1 KB. So a company can update only
  itself, and only to a release the sidecar would also let it run. Tokens are never logged or printed.

Each company's `.env` has `TICO_UPDATER_URL=http://10.208.<i>.1:8090` and `TICO_CELL=1` (which marks a cloud company
for cloud access), and no `TICO_UPDATES`. Its `compose.cell.yaml` mounts `control/` as `/control`.

**Picking it up on an older host.** A company an older core added has `TICO_UPDATES=tico`, no token and the stock
control volume. Run `init` from the new checkout once (it installs the service), then
`cell update --company <slug> --version <the release it runs>` for each company, or let its next `update` do it.
`update` rewrites the company's files to the new form (token, `control/` mount, `.env`). With the same release it
takes no snapshot, and it restarts the server only when a file changed, so a second run changes nothing.

## Layout

| Path | What it is |
|---|---|
| `/srv/tico-cell/cell.json` | The cell's domain and the router's address |
| `/srv/tico-cell/edge.yaml`, `router/Caddyfile` | The router and tunnel. The Caddyfile is regenerated and swapped in one step on every change, then reloaded. A config the router refuses is replaced by the previous one |
| `/etc/systemd/system/tico-cell-updater.service` | The cell updater, run from the checkout `init` last ran from |
| `/etc/systemd/system/tico-cell-wake.service` | The wake service, run from the same checkout, on `127.0.0.1:8082` |
| `/srv/tico-cell/tunnel/cloudflared.yml`, `tunnel.env` | The tunnel's routes (owned by 65532, the cloudflared user), and its token (0600) |
| `/srv/tico-cell/companies/<slug>/.env` | The server's settings (0600), written by the same code as a hosted install's |
| `.../compose.yaml` | The stock `compose.yaml` from the checkout, unchanged |
| `.../compose.cell.yaml` | The memory limit, `data/` mounted as `/data` and `control/` as `/control`, `TICO_CELL`, the default network and the `tico.cell.company` label |
| `.../extra.env` | Settings carried from a moved install (`--env-file`, 0600) |
| `.../company.json` | The slot, port, release, memory, state, aliases, the sleep policy (`sleep_minutes`), and a pending update (the release before it and its snapshot). No secrets |
| `.../data/`, `company-file/` | The server's `/data`, and the starter plan (both owned by user 10001) |
| `.../control/updater-token` | The company's updater token (0600, owned by 10001); its server reads it as `/control/updater-token` |
| `.../updater-status.json` | The cell updater's last status for the company (0600) |
| `.../owner-link.txt` | The owner's break-glass sign-in link (0600). It is never printed |
| `.../snapshots/` | Database copies taken before each update |
| `.../.data-import/` | An archive being unpacked (`--import-data`); renamed to `data/` when done |

Each company's Compose project is `tico-<slug>`. To look at one by hand:

```sh
d=/srv/tico-cell/companies/<slug>
docker compose --project-name tico-<slug> --project-directory $d --env-file $d/.env -f $d/compose.yaml -f $d/compose.cell.yaml logs server
```

Each server trusts the client address that Cloudflare puts in `CF-Connecting-IP`. It trusts that header only when the
request comes from loopback or its own network gateways.
