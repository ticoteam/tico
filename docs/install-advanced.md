# Advanced installs

> **Advanced.** Docker is the only way to install and run the Tico server, and [install.md](install.md) is that path: one
> command on a Linux server. This page is for driving the wizard from your laptop, pasting cloud-init by hand, and
> putting your own front door (an AWS load balancer, say) in front of the server. Nothing here is required.

## Drive the wizard from your laptop

`tico setup` normally runs on the server (`install.sh` starts it there). It can instead run on your laptop and reach the
server for you. It needs `python3` (3.10+) and a clone of the repository; the AWS option also needs `pip install boto3`
and your AWS credentials. Run it from the clone:

```
python3 -m setup            # or scripts/tico-setup
```

It asks **Where should Tico run?** and the rest of the questions are the same as in [install.md](install.md#what-the-wizard-asks):

- **A server I already have** (`--target ssh --ssh user@host`): any Linux computer you can SSH into; it installs Docker over SSH.
- **A new EC2 server on AWS** (`--target aws`, or `--cloud aws`): it creates and tags everything and prints a cost estimate.
- **A new server on Hetzner Cloud or DigitalOcean** (`--cloud hetzner|digitalocean`), through your own `hcloud` / `doctl`.
- **A one-line install command** (`--target command`): written to a private file for you to paste on any Debian or Ubuntu server.

Answers and credentials are kept in `~/.config/tico-setup/<domain>/` so a re-run resumes. `--dry-run` prints the plan. Further
flags for this mode: `--target ssh|aws|command`, `--ssh`, `--ssh-port`, `--ssh-identity`, `--aws-region`, `--aws-profile`,
`--aws-instance-type`, `--aws-os`, `--cloud-location`, `--cloud-size`, `--cloud-ssh-key`, `--compose-ref`.

### The AWS server

`t4g.small` (2 GiB) is enough for the server. Ubuntu 24.04 or Amazon Linux 2023 on arm64, a 30 GiB encrypted disk
(100 GiB on `m8i-flex.xlarge` or 200 GiB on `m8i-flex.2xlarge`, both x86_64),
a security group with only 80 and 443 for Caddy (nothing for a tunnel), IMDSv2 with hop limit 1 (containers cannot
reach the instance role), an Elastic IP for Caddy, and SSM instead of SSH. The `.env` is stored as an SSM SecureString
that the instance reads at boot, so it is not in the user-data. Every resource is tagged `ManagedBy=tico-setup` and
`tico-setup-name=<name>`, where the name is `--name` (lowercase letters, digits and dashes, up to 40) or, without it, made
from the domain: the role, security group and instance are `tico-<name>` and the `.env` is `/tico/<name>/env`. In an
account that only lets setup create IAM roles under a permissions boundary, pass its ARN with
`--aws-permissions-boundary`: the role is created under it, with both tags, in one call. Because the hop limit is 1, backups from the container need explicit keys: an IAM user (or R2
token) limited to the backup bucket, in `LITESTREAM_ACCESS_KEY_ID` and `LITESTREAM_SECRET_ACCESS_KEY`.

A self-install's `--company-file` goes through a private, encrypted bucket setup creates in your AWS account, with
public access blocked and a one-day expiry rule. Setup keeps its bucket name and chart for retries. The server role
can read only this install's plan prefix. Creating the bucket needs permission to create/tag S3 buckets and configure
public access blocking, ownership controls, encryption and lifecycle rules. A failure stops before the chart upload.
Setup refuses an existing bucket without this install's ownership tags. The empty bucket remains after the chart is
deleted. With an explicit `--plan-bucket BUCKET`
(and `--plan-bucket-region` when it differs from `--aws-region`), as Tico's hosted servers use, setup puts the packed
plan at `plans/<name>/<sha256>` in that bucket before launch, grants the server's role `s3:GetObject` on
`plans/<name>/*` only, and names the key and its sha256 in the `.env`. At first boot the server copies it to
`/opt/tico/company-file/plan.b64` (0400, the server's user), refuses to start on a sha256 mismatch, and the app reads it
from `TICO_COMPANY_FILE` (the server mounts that folder by its absolute path, `TICO_COMPANY_FILE_DIR`). A copy the app can't use (missing, unreadable, empty or too big)
starts the server with the built-in bots only and shows the owner why on first run and on Health, never another
team in its place. Setup deletes the
object once the running app reads its copy; if S3 refuses, setup says so and the object stays until you delete it or
the bucket's lifecycle rule, if it has one, expires it. If the first boot stops (a copy or sha256 failure), fix the
cause and re-run the same command: it finishes that boot. A server set up before `--plan-bucket` gets this release's
`compose.yaml` and image on that re-run. Charts up to 300 seats fit.

```
python3 -m setup destroy --domain tico.example.com --dry-run   # list what carries both tags
python3 -m setup destroy --domain tico.example.com             # asks you to type the name
```

Destroy deletes the instance and its data with it, and leaves your DNS records alone. With `--dns` (and `--domain`) it
first deletes the domain's A and AAAA records in Route 53 too, but only those that still point at this server's
addresses; a record someone has pointed elsewhere is left and named. If Route 53 can't be read, nothing at all is
deleted, so running it again is safe.

### A Linux runner computer from your laptop

```
python3 -m setup runner --server-url https://tico.example.com
```

It uses the same targets (an existing server over SSH, a new EC2 computer with no inbound ports at all, or a paste command) and
runs `ghcr.io/ticoteam/tico-runner` with a volume for logins and repositories. The one-time join code lasts 15 minutes and works
once: set `TICO_OWNER_TOKEN` (the owner's personal token) and it mints one at the last moment, or set `TICO_ENROLL_CODE`, or
paste one when asked. Plan for about 0.5-1 GiB of RAM per bot working at the same time (`t4g.medium` is the default).
Resuming an AWS install with the same name reuses its computer and waits up to five minutes for Systems Manager
before sending recovery. A registration race is retried only before the command is accepted; a failed or timed-out
command is not replayed automatically. If recovery cannot finish, setup exits unsuccessfully and keeps the computer.

Stopping or restarting the runner container (`docker stop`, `docker restart`, an update) lets running turns finish first:
it takes no new work and exits once nothing runs, interrupting what is left after 14 minutes. Stop it twice to stop at once.

```
python3 -m setup doctor --domain tico.example.com            # re-runs the checks, with a fix for each failure
python3 -m setup doctor --domain tico.example.com --runner   # also checks the runners set up from this computer
```

## Paste cloud-init by hand

`infra/cloud-init/tico-server.yaml` and `tico-runner.yaml` work on any Ubuntu 24.04 cloud that accepts cloud-init user data
(credentials and behaviour: [install.md](install.md#automation)). Edit only the block between the two `inputs` markers.

**Hetzner Cloud.** Servers > Add Server, Ubuntu 24.04, your type and location, your SSH key, a firewall allowing inbound 80
and 443 (and 22), and under **Cloud config** paste the edited file. Point the domain's `A` record at the server's IPv4; the
server waits up to 30 minutes for that record before it starts Caddy. Progress is in `/var/log/tico-setup.log`.

**DigitalOcean.** There is no "Deploy to DigitalOcean" button for this: the app-platform button deploys a repository as an App
Platform app, and no create-Droplet link accepts user data. Create > Droplets, Ubuntu 24.04, Basic, `s-1vcpu-2gb`, your SSH key,
tick **Add Initialization scripts** and paste the edited file, then Networking > Firewalls (inbound 80, 443, 22) and Reserved
IPs (assign one). Or `doctl compute droplet create ... --user-data-file`.

**A runner computer.** Edit `tico-runner.yaml` (version, `TICO_URL`, `TICO_CODE`, label), create a server of 4 GB or more with it
(`cax21` or `cx33` on Hetzner, `s-2vcpu-4gb` on DigitalOcean), and attach a firewall with no inbound rules. The join code
works once and expires after 15 minutes, so create the server right after copying it.

## Apply hosted visual configuration on the same installation

The optional operator protocol `tico-hosted-assets-v1` changes only `TICO_VISUAL_ORIGIN`,
`TICO_CADDY_VISUALS` and `TICO_VISUAL_SAME_ORIGIN` on an existing hosted EC2 installation.
The trusted Hosting owner supplies the persisted operation/revision/nonce, company/workspace/app-attempt,
asset lease and DNS record receipt, public and operational hosts, EC2 identity and census UUID. It uses
the existing instance-scoped SSM authority; there is no customer API, generic executor or setup fallback.

Both the selected installer bundle and running server must contain this protocol from the **same source
commit**. The bundle's `installer-manifest.json` verifies its actual setup modules and Compose bytes;
the running image's existing `release-manifest.json` and code digest verify runtime content, with separate
exact supervisor/module hashes. A branch name, tag, environment switch or bare commit label grants no support.
Old releases, missing/modified manifests and mismatched releases stay pending. The regular release bundle
and existing updater carry these files. Fresh AWS bootstrap also obtains the bundle for the actual running
image's pinned release and commit, verifies `SHA256SUMS`, runtime hashes and the existing Compose bytes,
then installs only the matching setup modules and manifest. Missing/corrupt/old artifacts leave this optional
capability pending while ordinary app startup proceeds. Earlier compose-only installations need a separately
approved normal bundle update before this command is available. An HQ
installer selected from a source checkout also needs the verified installer bundle, rather than an invented manifest.

From the installed `/opt/tico` directory, the fixed commands are:

```sh
python3 -B -m setup.visual_config capabilities
sudo python3 -B -m setup.visual_config status
sudo python3 -B -m setup.visual_config apply --request "$persisted_request_base64url"
```

Discovery verifies selected source, the running server's generated configuration and shared supervisor,
and the existing census in read-only mode. It does not initialize the backend, allocate census state,
create locks or write data. A successful capability object contains exactly `protocol`,
`installer_revision`, `runtime_revision`, `initial_flags`, `same_instance_apply` and `applied_receipt`.
The booleans describe supported operations; they do not assert current Caddy health or prove application.
An existing stopped or unhealthy Caddy can remain a recovery target. Actual applied-generation, health
and TLS checks are still required before a receipt, and an absent Caddy service remains unsupported.
Runtime verification requires an existing instance; initial-install flags remain pending before that target
can be proved. `apply` accepts only the exact versioned, nonsecret request as unpadded base64url of compact
ASCII JSON (maximum 4096 decoded bytes). It independently reads EC2 IMDSv2 and the persisted census UUID.
Successful output is a challenge-bound asset receipt of at most 2048 bytes. Pending returns exit 2 and a
fixed reason, without a receipt, key, signed address, body, credentials or provider diagnostics.

### Prepare existing local probe material

The operator supplies `/opt/tico/.visual-probe.json`, a regular root-owned file, mode `0600`, at most
4096 bytes, without symlinks. Its exact fields are:

```json
{"install_id":"<the existing census UUID>","token":"<an existing authorized visual token>","sha256":"<64 lowercase hex characters>"}
```

Use an existing visual that an authorized reader can currently open. For a small PNG/JPEG/GIF/WebP
attachment, `sha256` is the SHA256 of its exact served image bytes; HTML and SVG may be transformed and
need the digest of the served response. Obtain the current token from that reader's existing visual `src`,
keeping just the `/visuals/` token component. Prepare this file privately on the installation using a local
editor or private stdin; keep its content out of SSM commands, shell arguments, tickets and logs. Use the
actual census UUID already bound by the trusted Hosting operation. Do not mint a token for another reader,
create a synthetic artifact, generate/export a signing key or loosen file permissions to make a probe pass.

The runtime verifies the existing token with the existing private key and requires at least 30 seconds
of remaining validity. It connects only to that installation's private Caddy service using the exact asset
hostname for SNI/Host and ordinary CA verification. ON requires authorized GET/HEAD, matching served-content
digest, unsigned refusal, app/auth-route refusal and no response cookies. A second signed request carries
inert credential markers. Exact observed renderer bytes, supervisor file digest/generation and health also
prove that the running policy deletes authentication headers and response cookies; the request alone does
not assert stripping. No probe token, key or body leaves the installation.

OFF requires the app-only applied generation, a healthy app front door, and refusal of the **still-valid**
former signed token and app/auth paths on the asset hostname. Its private material fingerprint must match
the preceding successful ON probe. Refresh and verify that existing authorized probe while ON before
requesting OFF; an arbitrary replacement token after OFF cannot manufacture former-read evidence.
An explicit peer TLS name/handshake refusal
can prove retirement with those other barriers; a timeout, invalid certificate or unreachable front door
cannot. Missing, expired or unauthorized material remains pending even after configuration was applied.
For ON, refresh only the private probe file from the existing authorized workflow, then retry the same
persisted operation. An expired former OFF probe remains pending and requires explicit operator recovery;
the command never re-enables serving to obtain evidence. DNS removal, an unsigned 404 and an expired token
do not prove retirement.

### Reconcile and recover

`apply` records intent before changing `.env`, preserving every unrelated byte and refusing ambiguous
multiline/duplicate assignments. Its private `.visual-config.lock` and versioned `.visual-config.json`
serialize operations, preserve their exact tuples and fence stale ON/OFF. Re-enable after OFF requires a
fresh lease on the same app attempt, EC2 instance and census identity. The journal has a one-MiB limit;
it refuses additional intent rather than discard stale-operation tombstones.

A finite local owner holds that lock while recreating only the existing server and Caddy, using fixed
Compose arguments, the installed release and `--pull never --no-build`. Its Docker command budget is
150 seconds; the bounded SSM call may return pending sooner. The operation's process identity, deadline,
stage and any uncertain Docker client identity are visible in `status`. Repeat the **same** request after
the owner settles; do not allocate another operation, lease, server or identity because a response was lost.
If the server restart is durably complete and the Caddy restart has not been dispatched, that same
operation can reach its owed fixed Caddy restart even when Caddy is stopped or its applied marker is
missing. Source/instance/census and journal continuity are checked before the restart; missing applied
health is not proof of success and cannot bypass uncertain dispatch fencing.

After a lost Docker response, that same operation can advance without issuing the mutation again only
when its exact recorded client has exited and a fresh healthy target container proves the expected current
configuration/generation. Unknown dispatch cuts or an unchanged/unhealthy container retain uncertainty.
New revisions cannot overtake it. Use `status` to inspect saved/runtime configuration and applied-generation
observations. A concurrent editor, source update, unrelated environment change or rollback stays pending;
no cached receipt or automatic ON replay repairs it. Resolve the actual install through the existing
operator recovery/update procedure before repeating the same request. If the dispatch identity or target
outcome still cannot be established, retain the journal and seek explicit operator recovery; never delete
the lock/journal, copy another install's receipt or edit uncertainty into success.

## Advanced: your own front door

The wizard sets up Caddy (automatic HTTPS) or a Cloudflare Tunnel, and the sign-in options in
[install.md](install.md#sign-in). If your team already has a front door, keep the Docker server and put yours in front of it.
Tico verifies who the proxy says the human is; the email must still be on the roster. Every option and its exact
behaviour is in [environments](environments.md#sign-in-options).

- **Cloudflare Access in front of the tunnel:** `TICO_AUTH_PROXY=cloudflare`, `TICO_ACCESS_ISSUER`, `TICO_ACCESS_AUDIENCE`.
  Steps: [Cloudflare Tunnel](install.md#cloudflare-tunnel).
- **An AWS Application Load Balancer with Cognito:** run the server from the same compose file with no Caddy or tunnel profile
  (leave `COMPOSE_PROFILES` without `caddy` and `cloudflared`), the server publishes 8765 on `127.0.0.1` only, so add a `compose.override.yaml` that maps it to an address the load balancer can
  reach (a private IP, not `0.0.0.0` on a public interface), and give the ALB an
  authenticate-cognito action in front of a target group for that port. Set `TICO_AUTH_PROXY=aws-alb`,
  `TICO_ALB_ARN` (the load balancer's ARN), `TICO_ALB_REGION` and `TICO_PUBLIC_URL=https://<your hostname>`, and optionally
  `TICO_COGNITO_LOGOUT_URL`. Tico checks the ES256 signature of `x-amzn-oidc-data` against the ALB's ARN. Keep the server
  reachable only from the load balancer's security group; anything that can reach it directly can send that header.

Tico does not build or manage the load balancer, Cognito or the network around them.

Visuals in replies are always on. For pages and SVG to show inline they need an address of their own, apart from
the app's: `TICO_VISUAL_ORIGIN`. Without it, the server shows pictures, video and audio, and pages and SVG open in
the file viewer. See [Visuals in replies](visuals.md#self-hosting).

## Company page on its own domain

The [Company page](company-page.md) is served from an origin of its own, apart from the app. Set it with
`--reader-origin https://open.acme.example` (the wizard's flag, written as `TICO_READER_ORIGIN`):

- It must be an HTTPS origin on the standard port, with a plain DNS name that is none of the app, runner, visual or alias
  hosts.
- It needs the Caddy front door (`--front-door caddy`). The Cloudflare tunnel does not route it yet, so a tunnel install
  cannot serve the page.
- Point the name's DNS at the server, as for the app. Caddy gets its certificate for it.
- Register `https://open.acme.example/auth/callback` as a callback with your sign-in provider, beside the app's. Private
  audiences need that sign-in; without a sign-in provider they are off.

Only the page's routes reach the server on that name; everything else there is a 404. Public files are served from the
reader origin with no cache beyond a minute. Hosted installs need none of this; see [Company page](company-page.md#where-it-is-hosted).


## Run recovery

Each claim includes `busy_lanes`: the `bot|lane` of every live turn process on that computer
(`main`, or `task:<id>` for a task's own session), and `busy_bots` for bots whose task
worktrees are being maintained. A turn that outlives its lease stays busy until its process exits,
so the server skips new work in that lane on that computer. A server from before lanes refuses
`busy_lanes`; the runner then sends every bot with a live turn in `busy_bots`. The list is kept by the runner supervisor, with no checkout lock files
or durable server fence. Older runners omit the field. When an older server refuses it,
the runner retries the original claim request. No upgrade order is required; both updated
parts are needed for this protection. Separate runner installs sharing a workspace and
harness processes surviving a supervisor crash are outside this protection.

Background row failures appear in Health with their reason; logs repeat a short line at most
hourly for each row, with a traceback on the first failure. A refused due reminder is recorded
once rather than retried on every check. Row-specific SQL errors roll back that row; disk,
I/O, corruption and read-only database failures stop the batch and keep the original error.
