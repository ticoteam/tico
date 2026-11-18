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
python3 -m setup cell init --tunnel-token-file /root/tunnel-token --domain tico.team \
  [--kms-key arn:aws:kms:<region>:<account>:key/<id>] --json
```

`--kms-key` saves the cell's one KMS key in `cell.json`, for every `add` that gives none
([Stored secrets and KMS](#stored-secrets-and-kms)). Left out, the saved key is kept; `--kms-key ""` clears it.

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

`--meet-domain HOST` turns on Tico Meet calls for every company on the host ([Tico Meet](#tico-meet)).

In Cloudflare, give the tunnel a wildcard DNS record `*.<domain>` (a CNAME to the tunnel). Set no Public Hostnames
of its own on the tunnel: cloudflared prefers routes held in the dashboard over the file `init` writes. Names that
already have their own DNS record, such as `hub` and `accounts`, are not affected.

## Companies

```sh
python3 -m setup cell add --company boatyard --domain boatyard.tico.team --owner-email owner@example.com \
  --auth oidc --issuer https://accounts.tico.team --client-id tc_... --client-secret-file /root/boatyard/client-secret \
  --kms-key arn:aws:kms:us-west-2:<account>:key/<id> --kms-credentials-file /root/boatyard/kms-key \
  [--gateway-invite-file F] [--backup-url s3://bucket/prefix --backup-region us-west-2 --backup-credentials-file F] \
  [--company-file tico.company.yaml] [--company-name "Boatyard"] [--memory 2g] [--version vX.Y.Z] \
  [--hosted own|invited] [--signin-label "Tico account"] [--timeout 300] \
  [--own-domain] [--alias HOST]... [--page-host HOST] [--env-file F] [--import-data FILE] [--sleep-minutes N] --json
python3 -m setup cell list --json
python3 -m setup cell status --company boatyard --json
python3 -m setup cell stop|start --company boatyard --json
python3 -m setup cell sleep --company boatyard [--wake-at 2026-10-30T18:00:00Z] --json
python3 -m setup cell set-sleep --company boatyard --minutes N|default --json
python3 -m setup cell set-page-host --company boatyard --host boatyard.tico.page|"" --json
python3 -m setup cell set-slack --company boatyard on|off --json
python3 -m setup cell wake --company boatyard [--timeout 300] --json
python3 -m setup cell update --company boatyard --version vX.Y.Z [--kms-key ARN] [--kms-credentials-file F] --json
python3 -m setup cell kms-policy --company boatyard [--kms-key ARN] --json
python3 -m setup cell rewrap --company boatyard [--timeout 300] [--step-timeout 1800] --json
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
| `stop_failed` | The server (or its Slack gateway) could not be stopped, or Docker still says it runs. From `update`: nothing changed and the old release runs again |
| `snapshot_failed` | `update` could not copy the database; nothing changed and the old release runs again |
| `docker_failed` | A Docker or Compose call failed |
| `router_failed` | The router refused its new config (the old config stays) |
| `firewall_failed` | The metadata firewall rule, or Tico Meet's `INPUT` rules for 7880, could not be set |
| `meet_failed` | Tico Meet did not come up within 60 s of `init` (the detail says what is missing), or it is on but its key file holds no key pair |
| `port_taken` | `init --meet-domain` while a route uses one of Tico Meet's ports |
| `unhealthy` | `/healthz` did not answer through the router in time (`wake`: on the server's own port), and the detail has the last log lines |
| `update_failed` | The new release did not come up and the company is back on the old one and its database (`version` says which) |
| `updater_failed` | `init` could not install or start the cell updater or the wake service |
| `rollback_failed` | The new release did not come up, and putting the old one back failed too; the detail says how far it got (`version` is the release in `.env` now) |
| `kms_required` | `add` of a new company without `--kms-key` (and no key from `init`) or `--kms-credentials-file`; `update` with one of the two and neither saved; `rewrap` of a company without KMS settings. The detail names the missing flags. Nothing changed |
| `public_access` | `rewrap`: the company's backup bucket does not block public access with all four settings. The detail names the bucket. Nothing changed |
| `rewrap_failed` | `rewrap`: the backend step did not finish (`step`, `exit`, and `failed`: the kinds and counts of secrets that do not open under KMS). Nothing was deleted, and a running company was started again |
| `s3_failed` | `rewrap`: an S3 call with the host's role failed (reading the public access block, or deleting the key copies) |
| `unsupported_host` | `init` was run somewhere other than the Linux host, or `rewrap` on a host without boto3 |
| `internal` | A bug. The detail has the cause, with secrets masked |

`add`, `start`, `wake` and `update` answer like this:

```json
{"ok": true, "company": "boatyard", "url": "https://boatyard.tico.team", "port": 20000, "ready_ms": 8400,
 "version": "v0.5.1", "changed": true, "owner_link_file": "/srv/tico-cell/companies/boatyard/owner-link.txt"}
```

`add` and `update` add `"kms": true` and `"kms_key"` when the company's stored secrets are on KMS. A company made
before KMS gets `"kms": false` and a `"warning"` instead; it runs as before until `update --kms-key
--kms-credentials-file` and `rewrap` ([Stored secrets and KMS](#stored-secrets-and-kms)).

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
- `set-slack`: `{"ok": true, "company", "slack", "changed", "ready_ms"}`. A company with the gateway on also has
  `"slack": true` in `list` and `status`, and `status` adds `slack_container` (`{"state", "health"}`).
- `stop`: `{"ok": true, "company", "state": "stopped", "changed"}`.
- `sleep`: `{"ok": true, "company", "state": "sleeping", "changed"}`.
- `remove`: `{"ok": true, "company", "removed", "kept_data"}`. `removed` is false when there was nothing to remove.

A company's `state` is one of `running`, `starting`, `unhealthy`, `stopped`, `sleeping`, `missing` (no container) or
`removed`.

### Rules

- **Secrets come only from files.** These are the client secret, the Gateway invite, the backup key, the KMS access
  key and the tunnel token. Each file must be readable by its owner only (mode 0600 or stricter). Secrets never appear in argv, and never
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

## Stored secrets and KMS

A company's stored secrets (API keys, model subscriptions, Slack tokens, GitHub App secrets) are sealed with a data
key ([docs/credential-vault.md](credential-vault.md)). Without KMS that key is a file in `data/`, `credential.key`,
and the backups carry a copy of it, so whoever reads a backup opens every secret. On a cell the data key is wrapped
with the cell's one AWS KMS key instead, with the company's slug as its encryption context, and no key file is kept.

**A person's step, once per cell and once per company** (bots cannot do it):

1. Create the cell's KMS key: a symmetric encryption key in the cell's region, with a deletion waiting period (30
   days). A deleted key, or a lost access key, loses every company's stored secrets: keep the waiting period on. Pass
   its ARN to `init --kms-key` (or to each `add` and `update`).
2. For each company, an IAM user of its own with one access key and exactly the policy that
   `python3 -m setup cell kms-policy --company <slug> --kms-key <arn> --json` prints (`policy`): `kms:Encrypt`,
   `kms:Decrypt` and `kms:GenerateDataKey` on the cell's key, only when the encryption context is
   `company=<slug>` and `application=tico-credentials`. So one company's key can never unwrap another's data key.
   Containers cannot use the host's role, so each company needs this key of its own.
3. Save the access key in a 0600 file (`KEY=VALUE` lines with `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, or the
   JSON `aws iam create-access-key` prints) and pass it as `--kms-credentials-file`.

**The flags.** `add` and `update` take `--kms-key ARN` (a key ARN, `arn:aws:kms:<region>:<account>:key/<id>`, not an
alias) and `--kms-credentials-file F`. They write five lines to the company's `.env`: `TICO_CREDENTIAL_KMS_KEY` (the
ARN), `TICO_CREDENTIAL_KMS_CONTEXT` (the slug), `TICO_CREDENTIAL_KMS_ACCESS_KEY_ID`,
`TICO_CREDENTIAL_KMS_SECRET_ACCESS_KEY` and `TICO_CREDENTIAL_KMS_REGION` (the ARN's region). Either flag left out
keeps the saved value. The secret is never printed. **A new company needs both** (`--kms-key` may come from `init`):
without them `add` is refused with `kms_required`, naming the missing flag, so its first secret is never sealed by a
key file. A company made before this keeps running as it did, with a warning in the answer.

**Moving an existing company onto KMS:** `update --company <slug> --version <its release> --kms-key ARN
--kms-credentials-file F` (the server restarts with the settings and wraps its data key on next use), then
`python3 -m setup cell rewrap --company <slug> --json`, which does, in this order, each step stopping the run before
the next:

1. Reads the company's backup bucket's public access block with the host's role (read-only `GetPublicAccessBlock`).
   A bucket that does not block public access with all four settings stops it (`public_access`, naming the bucket)
   before anything else.
2. Refuses (`kms_required`) a company without its KMS settings.
3. Stops the server (and its Slack gateway; Docker must say they are stopped; a sleeping or stopped company already
   is) and runs the backend step `python -m backend.manage vault-rotate --json` in a one-off container of the same
   image, `.env` and `/data`. The step wraps the data key with KMS, re-seals what `github-app.key` seals, moves every
   secret to a fresh data key, checks that every one opens under KMS, and only then deletes `credential.key` and
   `github-app.key` from `data/`. A secret that does not open stops it with nothing deleted: the answer is
   `rewrap_failed` with `failed`, the kinds and counts. A company that was running is started again, also when the
   step failed; a sleeping one sleeps on.
4. With the host's role, deletes every version and delete marker of the key copies under
   `<backup prefix>/credential-key/` in the bucket.
5. With the host's role, deletes every version of a bundle backup's key copy in the same bucket
   (`backups/**.sqlite.keys/credential.key` or `github-app.key`, from a hand-run `manage.py backup`) whose SHA-256,
   as S3 stored it, is one of this company's key files', and the copy's delete markers. Another company's copy in a
   shared bucket, or one S3 holds no SHA-256 for, is left alone and counted in `kept_bundle_key_copies`. The key
   files' SHA-256s are written to `.key-digests.json` (0600) beside `.env` before step 3, so a run after an
   interruption still finds the copies. A bundle written to another bucket is not looked for.

It answers `{"ok": true, "company", "public_access_blocked": true, "step": "done", "resealed", "deleted",
"deleted_key_copies", "deleted_bundle_key_copies", "kept_bundle_key_copies", "state", "ready_ms"}` (`public_access_blocked` is null for a company without a backup bucket).
It is idempotent: run it again after a failure or an interruption, and the step answers `already_done` and nothing is
left to delete. It needs boto3 on the host (`pip install boto3`) and a host role that may read the bucket's public
access block, list object versions, read object checksums (`HeadObject`) and delete object versions under the
companies' prefixes and `backups/`.

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

## The Slack gateway

A company's [Slack gateway](slack.md) (the stock `compose.yaml`'s `slack` service) is off unless staff turn it on
for that company: `set-slack --company <slug> on`, or `off`. The choice is kept in `company.json` (`"slack": true`),
so every later `add` and `update` writes it again; a hand edit of `.env` does not survive. A company that was never
turned on is exactly as before: no `COMPOSE_PROFILES` and no gateway.

- **On.** `.env` gets `COMPOSE_PROFILES=slack`, and `compose.cell.yaml` gives the gateway what it gives the server:
  a memory limit of `1g` (`SLACK_MEMORY`), the company's own `data/` folder as `/data` in place of the stock volume, the
  `tico.cell.company` label, and `TICO_CELL`. It never mounts `control/`, so it cannot read the updater token, and it
  has no port. It joins only the company's own default network, where nothing else lives; it needs only Slack over the
  internet and the company's database. A running company starts it at once, and its server is not recreated (its own
  settings do not change). A stopped or sleeping company starts it with its server.
- **Off.** The gateway's container is stopped and removed first (a failure answers `docker_failed` and changes
  nothing), then the files go back to what any company has.
- **It writes the database.** So `update`, `sleep`, `stop` and a rollback stop it together with the server, and Docker
  must say every container of the company is stopped before a database file is copied or replaced (`stop_failed`
  otherwise). A sleeping company's gateway sleeps with it and does not see Slack until the company wakes, so give a
  company that uses Slack `set-sleep --minutes 0`.
- The company's `state` is still its server's. Cell stats' `logs` tool takes `role` `slack`
  ([cell-stats.md](cell-stats.md)).

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
  For a company that already runs, `set-page-host --host HOST` sets the same host without add's sign-in settings:
  the server restarts with it. `--host ""` clears it; the same host again changes nothing.
- **`--env-file F`** (0600, `KEY=VALUE` lines, keys `[A-Z][A-Z0-9_]*`, values on one line): settings the cell does not
  write, such as `TICO_RUNNER_URL`, `TICO_VISUAL_ORIGIN`, `TICO_BLOB_BUCKET`, `TICO_BLOB_REGION`, `TICO_INBOUND_KEY`,
  `TYPESAFE_API_KEY`, `TICO_STALE_REVIEW`, `TICO_SUPPORT_ACCESS`, `TICO_OIDC_ALLOWED_DOMAINS`. They are kept in
  `extra.env` (0600) and added to `.env` on every `add` and `update`. The cell refuses (lists in `ignored`, never
  keeps) every key it writes with a value, the keys a later `add` reads back from `.env` (team name, owner, Gateway
  invite, owner link, backup, company file), its sign-in, network, preset and support keys, and the ones that belong
  to a single-company install: `CLOUDFLARE_TUNNEL_TOKEN`, `COMPOSE_PROFILES`, `COMPOSE_PROJECT_NAME`,
  `TICO_UPDATER_URL`, `TICO_UPDATES`, `TICO_CELL`, `TICO_TAG`, `TICO_PORT`, `TICO_EXPECTED_AWS_ACCOUNT`,
  `TICO_EXPECTED_AWS_ROLE`, `TICO_CADDY*`, `TICO_TUNNEL*`, the page's `TICO_READER_ORIGIN` and
  `TICO_COMPANY_PAGE_DIR`, and the KMS settings (`TICO_CREDENTIAL_KMS_KEY`, `_CONTEXT`, `_ACCESS_KEY_ID`,
  `_SECRET_ACCESS_KEY`, `_REGION`: only `--kms-key` and `--kms-credentials-file` set them). So pass the team name and sign-in button as `--company-name` and `--signin-label`,
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

## Routes

A route is something on the host that is not a company, such as Tico's public demo (docs/demo.md): its hosts go to
its own loopback ports.

```sh
python3 -m setup cell route set --name demo --host demo.tico.team --host demo.tico.dev \
  --port 19081 --port 19082 --noindex [--router-only] --json
python3 -m setup cell route remove --name demo [--router-only] --json
python3 -m setup cell route list --json
```

- The record is `routes/<name>.json` (hosts, ports, noindex). Every later router or tunnel write (`add`, `remove`,
  `sleep`, `wake`, ...) keeps it. A route's hosts and a company's never overlap (`domain_taken` either way).
- Up to 5 hosts and 2 ports, from 1024 to 19999 and not the cell's own (2019, 8080, 8081, 8082, 8090). With two ports
  the router sends each request to the first one whose `/healthz` answers (checked every 2 s), so a second copy can
  start beside the first and take over when the first stops, with no reload. The hold and the 503 are a company's.
- `--noindex` adds `X-Robots-Tag: noindex, nofollow` to every answer and serves a `robots.txt` that disallows
  everything.
- A host the tunnel's wildcard misses (`demo.tico.dev`) goes into the tunnel's routes as an alias does, and restarts
  the tunnel when the list changes. `--router-only` leaves the tunnel alone; the answer's `tunnel_pending` says a
  restart is still due.
- A core older than routes drops them when it rewrites the router. The demo's own timer puts its route back
  (`deploy/demo/tico-demo route`).

## Updates

A company updates through the same contract as a hosted install's updater sidecar (`docker/updater.py`): its server
calls `POST /update` and `GET /status` at `TICO_UPDATER_URL` with the token in `/control/updater-token`. So Update now,
an update service key (the Release Owner's rollout), Tico support's idle-time target, the `system.update` events and
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

## Tico Meet

Tico Meet calls ([meet.md](meet.md)) need a LiveKit media server. A cell runs **one LiveKit for every company on the
host, with one key pair**. LiveKit OSS lets any key mint a token for any room, so a key pair per company would add no
isolation, and adding a company would restart LiveKit and drop every call. Rooms are `tico-call-<uuid>`, and each
server lists and ends only its own rooms by name.

Turn it on with `init --meet-domain <host>`, where `<host>` is the TURN hostname, such as `meet-cell-1.preview.tico.team`. The
setting is saved in `cell.json` and kept when the flag is left out. `--meet-domain ""` turns it off. `init` then:

- Makes the key pair once, in `/srv/tico-cell/meet/keys/keys.yaml` (0600, `<key>: "<secret>"`, LiveKit's own
  `key_file`). LiveKit reads it from a read-only mount, so the secret is in neither `docker inspect` nor
  `docker compose config`. The secret is never printed, and it is scrubbed from every answer and error. Turning Tico
  Meet off keeps the file, so turning it on again reuses the same pair.
- Refuses to turn it on while a route (`cell route set`) uses 7880, 7881, 3478 or 8443 (`port_taken`). Routes can never
  take those ports, whether Tico Meet is on or not.
- Adds two services to the edge project, both pinned by digest, on the host's network, with all capabilities dropped
  except `NET_BIND_SERVICE`, `no-new-privileges` and a read-only root filesystem:
  - `certs` (`tico-cell-certs`): Caddy, the router's image, which only gets and renews the TURN certificate. It uses
    HTTP-01 on port 80 and nothing else, so no DNS credential is ever on the host. Its admin API is off (the router
    uses `localhost:2019`), and its own HTTPS port is 8443, which stays closed. Its storage is
    `/srv/tico-cell/meet/certs` (0700).
  - `livekit` (`tico-cell-livekit`): `compose.yaml`'s LiveKit image (v1.13.11: TURN credentials that expire, and
    TURN refuses relays to private addresses and to this host's other ports). Its API and signal are on 7880, media on
    7881/tcp and 50000-50100/udp, TURN on 3478/udp, and TURN over TLS on **443/tcp** with that certificate. Nothing
    else on a cell binds 443, because the tunnel is outbound only. It uses `use_external_ip`.
- Keeps 7880 to this host: the firewall script (the one that blocks the metadata service, run at boot and after every
  Docker start) puts three rules tagged `tico-cell-meet` first in `INPUT`. They accept 7880/tcp from `127.0.0.0/8` (the
  router) and from the companies' default networks (`10.208.0.0/12`, their servers), and drop it from anywhere else.
  The security group also keeps 7880 closed. Turning Tico Meet off removes the rules.
- Waits up to 60 s for LiveKit's API to answer on `127.0.0.1:7880` and for the TURN certificate to be there. Only then
  does Tico Meet count as on. If either is missing, `init` fails with `meet_failed`, naming what is missing. A Tico Meet
  that was off is taken down again (services, firewall rules), and nothing below happens. One that was already on stays
  on, as it would if LiveKit stopped by itself.
- Then sends `/rtc` and `/rtc/*` on every running company's hosts to `127.0.0.1:7880`, ahead of the company's server, with
  the same hold and WebSocket grace as the server. Browsers connect to `wss://<company host>/rtc` through the tunnel,
  so the signal needs no public port.

Each company's `.env` gets five keys at `add`, or at its next `update`, including one to the release it already runs:

| Key | Value |
|---|---|
| `TICO_MEET_URL` | `wss://<company domain>` |
| `TICO_MEET_INTERNAL_URL` | `http://10.208.<i>.1:7880`: the company's default-network gateway, as for its updater |
| `TICO_MEET_DOMAIN` | the TURN hostname |
| `TICO_MEET_API_KEY`, `TICO_MEET_API_SECRET` | the cell's key pair |

While Tico Meet is off, `add` and `update` remove these keys, and calls answer `meet_unavailable`. They are the cell's
own: an `--env-file` that carries them gets them back in `ignored`. A company on a release older than v0.5.54 (the
first with calls) ignores them. A company host can never be the TURN host, and the reverse is also refused
(`domain_taken`).

**Certificate renewal.** LiveKit reads its certificate only when it starts. Every 10 minutes the cell updater
compares the certificate file with LiveKit's start time. When the file is newer, it asks LiveKit's `ListRooms` with a
one-minute token that may only list rooms. If no room is open, it restarts LiveKit. When rooms are open or LiveKit does
not answer, it waits and tries again 10 minutes later. Caddy renews about 30 days before the certificate expires, so
there is time to find an empty moment. While the certificate LiveKit started with runs out in under 14 days (renewal
failed, or no empty moment came), each check logs `tico meet: warning: cert_expiring: ... <days> days` to the updater's
journal (`journalctl -u tico-cell-updater`).

**Restarts.** Each server restarts once, when its `.env` gains or loses the keys. The router holds requests for 30 s
while it does. A LiveKit restart drops live calls. That happens on a renewal (only when the cell is empty), on a new
TURN host or LiveKit image (an `init` from a core that changes them), and when Tico Meet is turned off.

**Trust.** All the companies on a host share one key, so a company's server that leaks it could list rooms and join
another company's call. Each server also has the secret in its own environment (`TICO_MEET_API_SECRET`). Every company
on cell-1 is ours, so the cell is one trust boundary and that is acceptable. Before untrusted
sign-ups share a cell, revisit this with a LiveKit per company or a proxy that checks tokens.

### Turning it on in production

The AWS piece is `infra/cell-meet/cell-meet.yaml`: an Elastic IP associated with the host, and the inbound rules
80/tcp, 443/tcp, 3478/udp, 7881/tcp and 50000-50100/udp on the host's security group. It keeps 22, 7880 and 8443
closed. It is a stack of its own, so the host's own stack is never redeployed. For cell-1 (`i-027126437a47a24f4`,
`sg-0ee5245bf7bc7a276`), in this order:

1. **Install the core** for the release that has this, through HQ (`POST /v1/staff/cloud/hosts/cell-1/core`), at a
   quiet hour, and check that every company is healthy. That install runs a plain `init`. If the host's edge still
   runs older router or tunnel images than this core pins, the init recreates the router and the tunnel, so every
   company drops for a few seconds. Keep that apart from the steps below.
2. **Create the stack** `tico-cloud-cell-1-meet` from `infra/cell-meet/cell-meet.yaml` with a change set
   (`InstanceId=i-027126437a47a24f4`, `SecurityGroupId=sg-0ee5245bf7bc7a276`). Do not redeploy `tico-cloud-cell-1`.
   Associating the IP changes the host's public IP, which resets outbound connections once. cloudflared reconnects
   within seconds. Note the `PublicIp` output.
3. **Add the DNS record in Route53**, in the `preview.tico.team` hosted zone (the HQ AWS account, `--profile tidy`): an
   A record `meet-cell-1.preview.tico.team` pointing to that IP. tico.team itself is on Cloudflare behind a proxied
   wildcard, which can't carry LiveKit's UDP and TURN; the delegated Route53 zone answers the IP directly. Check that
   `dig +short meet-cell-1.preview.tico.team` answers the IP.
4. **Run init over SSM**: `cd /opt/tico-cell/core-<vX> && python3 -m setup cell init --meet-domain meet-cell-1.preview.tico.team --json`.
   It answers only once LiveKit is up with its certificate (or fails with `meet_failed` and leaves Tico Meet off).
   Check `iptables -S INPUT | head -4` shows the three `tico-cell-meet` rules first.
5. **Canary on one company**: `python3 -m setup cell update --company co-tico-team --version <its current release>`.
   This rewrites its `.env` and restarts only that server. On co-tico-team.tico.team, test a two-person call with a bot
   speaking, one client on a network that blocks UDP (to use TURN over TLS on 443), and that the server's recorder
   joins.
6. **Run the same `update`** for each other company, at its own current release. New companies get the keys at `add`.

**Rollback**, from least to most:

- One company: there is no per-company switch. Re-running `update` while Tico Meet is on keeps the keys, so stop that
  company's calls in Tico.
- The whole cell: `cell init --meet-domain ""` removes `certs` and `livekit` (`--remove-orphans`), the `/rtc`
  routes and the `INPUT` rules. Then `cell update --company <slug> --version <its current release>` for each company removes the
  `TICO_MEET_*` keys, and calls answer `meet_unavailable` again.
- AWS and DNS: delete the `tico-cloud-cell-1-meet` stack. That removes the rules and releases the IP, and the host
  gets a new auto-assigned IP, which the tunnel does not mind. Then delete the Route53 A record.

No database migration or company data is involved.

## Layout

| Path | What it is |
|---|---|
| `/srv/tico-cell/cell.json` | The cell's domain, the router's address, the page and Tico Meet domains when set, and the cell's KMS key (`init --kms-key`) |
| `/srv/tico-cell/routes/<name>.json` | A route ([Routes](#routes)): its hosts, ports and noindex |
| `/srv/tico-cell/edge.yaml`, `router/Caddyfile` | The router and tunnel. The Caddyfile is regenerated and swapped in one step on every change, then reloaded. A config the router refuses is replaced by the previous one |
| `/etc/systemd/system/tico-cell-updater.service` | The cell updater, run from the checkout `init` last ran from |
| `/etc/systemd/system/tico-cell-wake.service` | The wake service, run from the same checkout, on `127.0.0.1:8082` |
| `/srv/tico-cell/meet/` (0700): `keys/keys.yaml`, `caddy/Caddyfile`, `certs/`, `livekit-cert.json` | Tico Meet ([Tico Meet](#tico-meet)): the cell's LiveKit key pair (0600), the certificate Caddy's config, its certificates (0700), and the expiry of the certificate LiveKit started with |
| `/srv/tico-cell/tunnel/cloudflared.yml`, `tunnel.env` | The tunnel's routes (owned by 65532, the cloudflared user), and its token (0600) |
| `/srv/tico-cell/companies/<slug>/.env` | The server's settings (0600), written by the same code as a hosted install's |
| `.../compose.yaml` | The stock `compose.yaml` from the checkout, unchanged |
| `.../compose.cell.yaml` | The memory limit, `data/` mounted as `/data` and `control/` as `/control`, `TICO_CELL`, the default network and the `tico.cell.company` label |
| `.../extra.env` | Settings carried from a moved install (`--env-file`, 0600) |
| `.../company.json` | The slot, port, release, memory, state, aliases, the sleep policy (`sleep_minutes`), whether its Slack gateway is on (`slack`), and a pending update (the release before it and its snapshot). No secrets |
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
