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

Visuals in replies need an address of their own, apart from the app's: `TICO_VISUAL_ORIGIN`. Without it, a
production server shows them as file chips. See [Visuals in replies](visuals.md#self-hosting).


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
