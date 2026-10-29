# Many companies on one host (cell mode)

`python3 -m setup cell` runs many companies' Tico servers on one shared Linux host. Tico uses it for its cloud
companies (`<slug>.tico.team`); Tico HQ drives it over AWS SSM. Each company gets the same server a
[hosted install](install-advanced.md) gets: the stock `ghcr.io/ticoteam/tico` image at a pinned release, the stock
entrypoint and `compose.yaml`, its own data folder, `.env` and Compose project, sign-in through its own OpenID
Connect client, the owner sign-in link kept as break-glass, and the recommended setup. It does not get its own Caddy,
cloudflared, updater or EC2 instance: its `.env` has `TICO_UPDATES=tico`, so the app shows no Update now and says
Tico updates the server (HQ runs `update`). Every company shares one router and one Cloudflare Tunnel. The host runs only Tico
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
   serving plain HTTP on `127.0.0.1:8080`. It sends each company's host name to that company's loopback port, and
   answers anything else with a 404. The tunnel sends `*.<domain>` to the router. Both containers use the host's
   network and listen on loopback only. The token is kept in `/srv/tico-cell/tunnel.env` (0600). Without
   `--tunnel-token-file`, the token from an earlier run is used, or only the router runs.

In Cloudflare, give the tunnel a wildcard DNS record `*.<domain>` (a CNAME to the tunnel). Set no Public Hostnames
of its own on the tunnel: cloudflared prefers routes held in the dashboard over the file `init` writes. Names that
already have their own DNS record, such as `hub` and `accounts`, are not affected.

## Companies

```sh
python3 -m setup cell add --company boatyard --domain boatyard.tico.team --owner-email owner@example.com \
  --auth oidc --issuer https://accounts.tico.team --client-id tc_... --client-secret-file /root/boatyard/client-secret \
  [--gateway-invite-file F] [--backup-url s3://bucket/prefix --backup-region us-west-2 --backup-credentials-file F] \
  [--company-file tico.company.yaml] [--company-name "Boatyard"] [--memory 2g] [--version vX.Y.Z] \
  [--hosted own|invited] [--signin-label "Tico account"] [--timeout 300] --json
python3 -m setup cell list --json
python3 -m setup cell status --company boatyard --json
python3 -m setup cell stop|start --company boatyard --json
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
| `bad_domain` | `--domain` is not `<slug>.<the cell's domain>` |
| `domain_taken` | Another company has this domain |
| `secret_file` | A secret file is missing, empty, readable by others or has the wrong content |
| `company_file` | The company file is not a valid company YAML |
| `not_initialized` | `init` has not run, or the metadata firewall rule is missing or not first |
| `not_found` | No such company, or it was removed |
| `busy` | Another command holds the lock too long |
| `full` | All 4,000 slots are in use |
| `use_update` | `add --version` names another release than the company runs; use `update` |
| `pull_failed` | The image could not be pulled |
| `stop_failed` | The server could not be stopped, or Docker still says it runs. From `update`: nothing changed and the old release runs again |
| `snapshot_failed` | `update` could not copy the database; nothing changed and the old release runs again |
| `docker_failed` | A Docker or Compose call failed |
| `router_failed` | The router refused its new config (the old config stays) |
| `firewall_failed` | The metadata firewall rule could not be set |
| `unhealthy` | `/healthz` did not answer through the router in time (the detail has the last log lines) |
| `update_failed` | The new release did not come up and the company is back on the old one and its database (`version` says which) |
| `rollback_failed` | The new release did not come up, and putting the old one back failed too; the detail says how far it got (`version` is the release in `.env` now) |
| `unsupported_host` | `init` was run somewhere other than the Linux host |
| `internal` | A bug. The detail has the cause, with secrets masked |

`add`, `start` and `update` answer like this:

```json
{"ok": true, "company": "boatyard", "url": "https://boatyard.tico.team", "port": 20000, "ready_ms": 8400,
 "version": "v0.5.1", "changed": true, "owner_link_file": "/srv/tico-cell/companies/boatyard/owner-link.txt"}
```

They return only once `/healthz` answers through the router. `ready_ms` is the time from the start of the command
until then. `changed` is false when nothing needed doing. The other commands answer like this:

- `list`: `{"ok": true, "companies": [{"company", "url", "port", "version", "memory", "state"}]}`.
- `status`: the same fields as a `list` entry, plus `healthy` (whether `/healthz` answers through the router now) and
  `container` (`{"state", "health"}`).
- `stop`: `{"ok": true, "company", "state": "stopped", "changed"}`.
- `remove`: `{"ok": true, "company", "removed", "kept_data"}`. `removed` is false when there was nothing to remove.

A company's `state` is one of `running`, `starting`, `unhealthy`, `stopped`, `missing` (no container) or `removed`.

### Rules

- **Secrets come only from files.** These are the client secret, the Gateway invite, the backup key and the tunnel
  token. Each file must be readable by its owner only (mode 0600 or stricter). Secrets never appear in argv, and never
  on stdout: every detail and log line is scrubbed. They are written only to the company's `.env` (0600).
- **Every command is idempotent.** Running `add` again with the same settings changes nothing and gives the same answer
  (`changed: false`). Running it with new settings, such as a rotated client secret, applies them. An optional input
  that is left out keeps its saved value: the release, memory, Gateway invite, backup settings and company file. A
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
  company stays stopped, and its next `start` (or `add`) starts the new release. Either way, while the update is
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

## Layout

| Path | What it is |
|---|---|
| `/srv/tico-cell/cell.json` | The cell's domain and the router's address |
| `/srv/tico-cell/edge.yaml`, `router/Caddyfile` | The router and tunnel. The Caddyfile is regenerated and swapped in one step on every change, then reloaded. A config the router refuses is replaced by the previous one |
| `/srv/tico-cell/tunnel/cloudflared.yml`, `tunnel.env` | The tunnel's routes, and its token (0600) |
| `/srv/tico-cell/companies/<slug>/.env` | The server's settings (0600), written by the same code as a hosted install's |
| `.../compose.yaml` | The stock `compose.yaml` from the checkout, unchanged |
| `.../compose.cell.yaml` | The memory limit, `data/` mounted as `/data`, `TICO_UPDATES`, the default network and the `tico.cell.company` label |
| `.../company.json` | The slot, port, release, memory, state, and a pending update (the release before it and its snapshot). No secrets |
| `.../data/`, `company-file/` | The server's `/data`, and the starter plan (both owned by user 10001) |
| `.../owner-link.txt` | The owner's break-glass sign-in link (0600). It is never printed |
| `.../snapshots/` | Database copies taken before each update |

Each company's Compose project is `tico-<slug>`. To look at one by hand:

```sh
d=/srv/tico-cell/companies/<slug>
docker compose --project-name tico-<slug> --project-directory $d --env-file $d/.env -f $d/compose.yaml -f $d/compose.cell.yaml logs server
```

Each server trusts the client address that Cloudflare puts in `CF-Connecting-IP`. It trusts that header only when the
request comes from loopback or its own network gateways.
