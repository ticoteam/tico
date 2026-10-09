# Install Tico

Tico has two parts. The **server** holds the app, the team's data and the sign-in; it runs no bots. The
**computers** run the bots: a Mac, or any Linux or cloud computer, each joined to the server with a one-time code.
Both parts are Docker images (linux/amd64 and linux/arm64):

| Image | Holds | Size (pull / on disk) |
|---|---|---|
| `ghcr.io/ticoteam/tico` | the server and Litestream backups | 106 MB / 450 MB |
| `ghcr.io/ticoteam/tico-runner` | a runner with git, gh, node, python, build tools, ripgrep, jq and curl; no model CLIs (it installs them itself, see [harnesses](harnesses.md)) | 268 MB / 1.1 GB |
| `ghcr.io/ticoteam/tico-updater` | the one-click updater | 74 MB / 310 MB |

```
browser -> caddy (HTTPS) or cloudflared --> server :8765 (data volume)
                                                 ^
                    Mac (native) and Linux runners join from anywhere over https://<your domain>
```

## Quick start on your own computer

Nothing to set up first: no domain, no DNS, no sign-in provider. Tico runs on this computer only, at
`http://127.0.0.1:8765`, and you sign in as the owner with a token kept on this computer. Add a domain and sign-in
later, when other people need in. It takes about two minutes and needs Docker: on a Mac or a Windows PC, open Docker Desktop
first; on Linux the installer installs Docker for you.

Run the installer with `--local` (a Mac or Linux; it asks for your email when you leave `--owner-email` out):

```
curl -fsSL https://github.com/ticoteam/tico/releases/latest/download/install.sh | sh -s -- --local --owner-email you@example.com
```

That installs the newest release; to pin one, use `releases/download/vX.Y.Z/install.sh` instead of `releases/latest/download/install.sh`.
Files go to `/opt/tico` on Linux (it uses `sudo`) and to `~/tico` on a Mac (no `sudo`); `--dir` changes it. Port 8765 is the default; use `--port 8877` if it is occupied. By hand, set `TICO_PORT=8877` in `.env`.
The public, runner and MCP addresses follow this port. `TICO_PUBLIC_URL` and `TICO_RUNNER_URL` override their defaults.
To prefill Finish setup, add `--owner-name "Ana" --team-name "Acme"` to the local installer command; `--company` remains an alias.

It prints a reusable private owner sign-in link. Keep it private: anyone with the link on this computer can sign in as you.
Recover the token any time with `docker compose exec server cat /data/local-owner.token` in the install directory.
Open the link in your browser: the app opens on **Finish setup**. Then:

1. **Choose AI providers** (optional): leave them all unticked to do it later. Bots need one to run; until then each bot says
   "Add an AI provider", and Settings > AI providers adds it.
2. **Name the team**, say what it does, and pick its groups. A few bots are suggested for each group.
3. **Add computer**: choose *A Linux or cloud server (Docker)*, press **Add computer**, and run the line it shows on this computer.
   It joins the server's own Docker network, so it needs no domain, and the computer shows online in a few seconds.
4. **Create my team.** The four built-in bots are created automatically. Your selected starters are placed on the computer,
   with **Needs setup** until you talk to them.
5. **Sign in to a model.** In **Settings > Computers**, wait for the selected harness to be installed, then press **Sign in**
   beside Codex or Claude Code. Open the provider link and enter its device code, or paste the returned code into Tico when asked.
   Wait for the computer's model sign-in to show **ready**. Other harnesses use their own terminal sign-in; API credentials and
   profiles are explained in [Harnesses](harnesses.md). Pick a provider in **Settings > AI providers** if you skipped it,
   then choose the starter's model in **Settings > Bots**.
6. **Set up one starter.** Select **Content Marketer** (`content`) in the **Marketing** group, open its page and press **Set up**.
   It needs only Tico; no GitHub or outside Tool is required. Tell it the readers, publishing plans, voice, topics and privacy rules
   using the sample answers below. Answer any remaining questions and review its first draft in the same chat. Outbound
   sending stays off until you turn it on for that bot.
7. **Finish one task.** On **Tasks > New task**, assign it: “Draft a 200-word blog post introducing our example app to project teams from these facts:
   our example app keeps tasks and meeting notes together; readers can try it locally; use a plain, friendly voice and invent
   no claims. Return a draft on this task.” Open the task to read its result, then close it. Success is a reviewed draft and a
   completed task, with the computer online and no blocking Health issue.

### Sample answers for Content Marketer

Paste this into its Setup chat (or replace it with your own facts):

> Our readers are project teams; the next step is to try our example app locally. Plan a weekly blog post and a short
> social version; keep both as drafts for me. Two voice examples: “Keep the next task beside the meeting notes.” and
> “Pick one job, write down the result, and share it with your team.” Avoid “The ultimate revolutionary platform.”
> Readers ask how to keep tasks and notes together, how to follow up after a meeting, and how to get a first result.
> Facts for the first draft: our example app keeps tasks and meeting notes together, and people can try it locally.
> No customer names, invented numbers, testimonials, comparisons or roadmap promises. Send the plan to me; use no
> external Tools for this first draft.

### First-result recovery

- **Add an AI provider**: when chat says "Saved — no AI provider is chosen", use its **Add an AI provider** link
  to open **Settings > AI providers**, then select the bot's model in **Settings > Bots**. Your message stays saved.
- **Offline** or **Missing bot repository or AGENT.md**: check **Settings > Computers** and keep the joined computer running;
  [Setup troubleshooting](onboarding.md#troubleshooting) explains repository and placement failures.
- **Sign-in rejected**: Health names the credential source when the computer reports it. Replace a shared model key in
  **Tools > Credentials**; for a computer-local key or login, update it or use **Sign in** in **Settings > Computers**.
  Older computers show both paths. See [Harnesses](harnesses.md).
- **Needs setup**: open the bot's chat and press **Set up**; tasks and routines wait until that conversation is finished.
- **A missing Tool or Credential**: use the bot's card or [Connect tools](connect-tools.md) and grant the credential to that bot.

[Set up your first bots](onboarding-guide.md) covers how to review and improve the result.

On a Windows PC, run the installer inside WSL 2 with Docker Desktop's WSL integration on, which is the Linux path.
By hand, on any computer with Docker (the same thing the installer does): download the release's compose bundle, copy `.env.example`
to `.env`, keep only these lines, and run `docker compose up -d`:

```
TICO_TEAM_NAME=Acme
TICO_OWNER_EMAIL=you@example.com
TICO_TAG=v0.3.3
TICO_PORT=8765
COMPOSE_PROFILES=updater
TICO_UPDATER_URL=http://updater:8080
```

`TICO_TEAM_NAME` is the team name; existing `TICO_COMPANY_NAME` settings still work. If both are set, `TICO_TEAM_NAME` wins.

Set `TICO_TAG` to the release whose bundle you downloaded. To upgrade by hand, run that release's installer with `--version vX.Y.Z`; it replaces the bundle and pins the images.

Then get the sign-in token with `docker compose exec server cat /data/local-owner.token` and open
`http://127.0.0.1:8765/api/v2/local-signin?token=<token>`. The app opens on the first-run wizard; you can create the team
before a computer or an AI provider exists. Join this computer under **Settings > Computers > Add computer**.

The server answers on this computer only: compose publishes the port on `127.0.0.1`, and a server with a domain and no
sign-in refuses to start. A public address always needs sign-in.

### Add a domain and sign-in later

To let your humans in from anywhere, edit `.env` in the install directory and run `docker compose up -d` there:

1. Point a domain at this computer and pick the front door: set `COMPOSE_PROFILES=caddy,updater` and `TICO_DOMAIN=tico.example.com`
   (or the Cloudflare tunnel; see below).
2. Choose the sign-in: `TICO_AUTH_PROXY=oidc` with its issuer, client ID and secret, or `cloudflare` (see [Sign-in](#sign-in)).
3. Add the others to the Humans list in the app; they sign in with the same account.

Setting `TICO_DOMAIN` without `TICO_AUTH_PROXY` stops the server with a message that names the missing setting.
The wizard below does all of this for you, on a fresh server.

## Install the server for your team

One command, run on the Linux server itself. It takes about 15 minutes, most of it waiting for DNS.

### Before you start

- [ ] **A Linux server**, about 2 GB of memory and 20 GB of disk (1 GB of memory is the minimum; see [sizing](sizing.md)). Ubuntu 24.04 or Debian 12 on
      x86_64 or arm64 is what is tested. You need root or `sudo`. See "Where to get a server" below.
- [ ] **A domain name** you can add a DNS record to, such as `tico.example.com`. The wizard tells you which
      provider serves it and the exact record to add.
- [ ] **A way to sign in.** A Google or Microsoft account that can create an OAuth client (Google Workspace,
      Microsoft Entra ID) for your humans. If the server has no public IP address (an office or home computer),
      use a **Cloudflare Tunnel** instead: it needs the domain on Cloudflare and no open ports.
- [ ] **A model subscription** for the bots (ChatGPT/Codex, Claude, Gemini and others). You sign the bots in after the
      install, on the computer that runs them; the server never holds it.

### Where to get a server

Any Linux computer works. If you need one, these are the cheap always-on options (list prices as of September 2026;
check the provider before you commit):

| | Size | Price, 24/7 | Notes |
|---|---|---|---|
| Hetzner Cloud | `cax11` (2 vCPU Arm, 4 GB, 40 GB) or `cx23` (2 vCPU x86, 4 GB) | about EUR 5.99 or EUR 5.49, plus the IPv4 | Lowest price. Arm sizes only in Germany and Finland (`nbg1`, `fsn1`, `hel1`); take `cx23` or `cx33` elsewhere. |
| DigitalOcean | `s-1vcpu-2gb` (1 vCPU, 2 GB, 50 GB), Ubuntu 24.04 x64 | $12 | `s-2vcpu-2gb` is $18. A Reserved IP is free while assigned. |
| AWS EC2 | `t4g.small` (2 GB, Arm), 30 GB gp3 | about $18 with disk and IPv4 | Ubuntu 24.04 or Amazon Linux 2023, in a public subnet (route to an internet gateway; a subnet whose 0.0.0.0/0 goes to a NAT gateway is unreachable from outside). The bot computer (runner) may sit in a private subnet since it only connects outbound. Open 80 and 443 in the security group (nothing for a tunnel), and set the metadata hop limit to 1 so containers cannot read the instance role: `aws ec2 modify-instance-metadata-options --instance-id i-... --http-tokens required --http-put-response-hop-limit 1`. Backups from the container then need explicit keys limited to the backup bucket: the server has no AWS credentials that could create them, so run `python3 -m setup backup-storage --domain tico.example.com --aws-region us-west-2` from a clone on your laptop (with your AWS credentials) first; it creates the bucket and key and prints the two `export` lines and the `existing` answer to give the installer. |
| Any Linux computer | 2 GB or more | your own | Give it a stable public IP (Caddy) or no public IP at all (tunnel). |

Whatever you pick, allow inbound 80 and 443 in the provider's firewall (not needed with a tunnel), and add an SSH
key or console access so you can reach a shell. The installer does not touch the firewall.

**Shortcut.** If you would rather not click through a provider's console, `tico setup --cloud hetzner|digitalocean|aws`
creates the server for you from your laptop, through your own `hcloud`, `doctl` or AWS credentials, and the new server
runs this same installer as its first boot step (`infra/cloud-init/`). Run it from a clone of the repository:
`python3 -m setup --cloud hetzner --domain tico.example.com`. It is optional; everything below is what it automates.

### The command

Open a shell on the server and run the installer of the release you want (see
[Releases](https://github.com/ticoteam/tico/releases); the tag is part of the address, so the script and the
software it installs always match):

```
curl -fsSL https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install.sh | sh
```

It asks for `sudo` if you are not root, and it prints each step. In order it:

1. **Checks the computer:** Linux on x86_64 or arm64, root or sudo, at least 1 GB of memory and 1 GB of free disk, and
   ports 80 and 443 free (skipped with `--tunnel`).
2. **Installs Docker** if it is missing or too old (Compose v2.20 or newer is needed): from Docker's own apt
   repository on Debian and Ubuntu, and with Docker's convenience script (`get.docker.com`) on other distributions.
3. **Downloads that release's compose bundle** (`tico-bundle-vX.Y.Z.tar.gz`) and checks it against the release's
   `SHA256SUMS`. A bundle that does not match is refused and nothing is installed.
4. **Runs the `tico setup` wizard** on the server, interactively, with the release pinned in `.env` as `TICO_TAG`.

Everything lives in `/opt/tico` (`compose.yaml`, `.env`, the wizard). Options, after `sh` or `sh -s --` when piping:

| Flag | Meaning |
|---|---|
| `--version vX.Y.Z` | Install this release instead of the one the script came from |
| `--dir PATH` | Install somewhere other than `/opt/tico` |
| `--yes` | Do not ask for confirmation |
| `--tunnel` | Cloudflare Tunnel: no public ports, so 80 and 443 are not checked |
| `--docker-only` | Only install Docker and Compose (runner computers) |
| `-- FLAGS` | Everything after `--` goes to `tico setup` (see Automation below) |

Exit codes: 0 done, 2 bad usage, 3 the computer does not qualify, 4 download or checksum failed, 5 Docker or Python
could not be set up, 6 the wizard or the health check failed.

**Running it again is safe.** With an existing `.env` it does not ask anything and never rewrites your settings: the
same version is repaired (bundle restored, stack restarted), a different `--version` upgrades (only `TICO_TAG` changes),
and an installer older than what the updater already installed leaves the newer version alone. If the wizard stopped
half way, running the command again resumes it.

### What the wizard asks

1. **How do humans reach it?** Caddy (automatic HTTPS, opens ports 80 and 443) or a Cloudflare tunnel (no open
   ports). With a scoped Cloudflare API token (Cloudflare Tunnel: Edit, and DNS: Edit on the one zone) it creates the
   tunnel, its route and the DNS record; without one it shows the exact dashboard steps and asks for the tunnel token.
2. **Domain and DNS.** It looks up which nameservers the internet actually uses for your domain and names the provider
   (Route 53, Cloudflare, Netlify/NS1, Vercel, Google, GoDaddy, Namecheap, ...). With Cloudflare and a token (or Route 53
   and AWS credentials) it offers to create the record; anywhere else it prints the exact record and where to add it. Then
   it polls public resolvers (8.8.8.8, 1.1.1.1, 9.9.9.9) and only starts Caddy once they agree, because a certificate
   requested before DNS is live fails without a visible error. It suggests this server's public IPv4 address; confirm it.
3. **Sign-in.** Google or Microsoft (OIDC), or Cloudflare Access. It opens the right console page, prints the redirect
   URI to paste (`https://<domain>/auth/callback`, character for character), and asks for the client ID and secret
   (hidden). Optionally limit sign-in to one email domain.
4. **Team name.** Name, owner email (it must be the account you will sign in with), and an optional model key for the
   server's own decision model.
5. **Backups.** A bucket (an S3 or R2 bucket it can create, one `setup backup-storage` created from your laptop, or one you already have) or local only, with the warning
   that local copies do not survive losing the server. See [Backups and restore](#backups-and-restore).

Then it shows the plan and asks "Go ahead?". `--dry-run` prints the plan and changes nothing (it only reads DNS):

```
$ python3 -m setup --dry-run --non-interactive --target local --front-door caddy --domain tico.example.com \
    --server-ip 203.0.113.7 --auth google --client-id 1234-abc.apps.googleusercontent.com --team-name Acme --owner-email you@example.com
tico setup (dry run: nothing will be created or changed)
Sign-in: create a Google OAuth client at https://console.cloud.google.com/auth/clients/create
  with redirect URI exactly https://tico.example.com/auth/callback

Plan
Tico at https://tico.example.com: one server running the Tico server in Docker. Bots run on computers you add afterwards.

  1. On this server: install Docker if missing, write /opt/tico/compose.yaml and /opt/tico/.env (0600), `docker compose up -d`
  2. DNS: tico.example.com is served by <your DNS provider>
       A tico.example.com -> 203.0.113.7
  3. Wait until public resolvers (8.8.8.8, 1.1.1.1, 9.9.9.9) answer with those records, before anything asks for a certificate
  4. Sign-in: Google OIDC; OAuth client redirect URI must be exactly https://tico.example.com/auth/callback
  5. Backups: local only
  6. Team 'Acme', owner you@example.com
  7. Verify: HTTPS certificate, /healthz, sign-in redirect, server container up
```

Credentials never appear on the command line, in the plan, in logs or in the repository. They go to the server's `.env`
(mode 0600) and to a private copy under `/opt/tico/.setup/` so a re-run can resume.

### What you will see after about 15 minutes

- The installer finishes with `Done. Open https://tico.example.com and sign in as you@example.com.`, after checks for
  the HTTPS certificate, `/healthz`, the sign-in redirect (provider host and exact redirect URI), and the server
  container. Any failed check prints a fix beside it.
- Most of the time goes to DNS: seconds with Route 53 or Cloudflare and a token, and up to your DNS provider's
  propagation time (the wizard waits up to 20 minutes and can be resumed) when you add the record yourself.
- Opening the URL shows Google or Microsoft sign-in, then the **setup wizard**: AI providers (when none are chosen yet),
  then Names, About the team, Your team chart, Add the computer that runs your bots, Connect an external agent and Review and create. Nothing runs
  bots until a computer is added: the next section is the step that does.

### When something fails

- **The certificate does not appear.** DNS was not live when Caddy first asked. Check the record is at the provider
  `tico setup` named (a Route 53 zone can exist while another provider serves the domain), then
  `docker compose restart caddy` in `/opt/tico`.
- **"redirect_uri_mismatch" at sign-in.** The OAuth client must list exactly `https://<domain>/auth/callback`.
- **Sign-in says you are not on the roster.** The owner email must match the account you sign in with.
- **Ports 80/443 time out from outside, though everything looks healthy on the server.** The server is probably in a
  private subnet: its route table's `0.0.0.0/0` goes to a NAT gateway instead of an internet gateway (`igw-...`).
  Launch it in a public subnet (or fix the route), and Let's Encrypt can then reach it.
- **A port is in use.** The installer stops before changing anything; free the port or use `--tunnel`.
- **Check again later:** `cd /opt/tico && sudo ./scripts/tico-setup doctor --domain tico.example.com` re-runs the checks
  with a fix for each failure.

### Automation

Run the installer with no terminal (a provisioning script, cloud-init, CI) and the wizard never prompts: a missing
value is an error that names the flag. Flags after `--` go to `tico setup`; credentials come from the environment only, so
they never appear in a process list:

```
TICO_OIDC_CLIENT_SECRET=... sh install.sh --yes --version vX.Y.Z -- --domain tico.example.com --front-door caddy \
  --server-ip 203.0.113.7 --auth google --client-id 1234-abc.apps.googleusercontent.com --team-name Acme \
  --owner-email you@example.com --backup local
```

Flags: `--domain`, `--front-door caddy|cloudflared`, `--server-ip`, `--tico-version`, `--auth google|microsoft|cloudflare`,
`--tenant`, `--client-id`, `--allowed-domain`, `--team-name` (`--company` remains an alias), `--owner-email`, `--decisions-provider` (the old `--judge-provider` still works), `--backup`,
`--backup-url`, `--backup-endpoint`, `--backup-region`, `--no-updater`, `--dns-timeout MINUTES`, `--skip-dns-wait`.
Credentials: `TICO_OIDC_CLIENT_SECRET`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_TUNNEL_TOKEN`, `OPENAI_API_KEY` (or
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`). `python3 -m setup --help` lists all of them.

**cloud-init.** `infra/cloud-init/tico-server.yaml` and `tico-runner.yaml` are paste-ready user data for any Ubuntu 24.04
cloud that accepts it (Hetzner, DigitalOcean, AWS, Vultr, Linode, OCI). Edit the block between the two `inputs` markers
(release, domain, owner email, team name, sign-in). They write `/opt/tico/.env` from it, turn on `ufw`, keep containers away
from the provider's metadata service, and call this same `install.sh` for the pinned release, which sees the existing `.env`
and installs and starts without asking. They are safe to run again and delete their inputs when done. Provider user data is
stored by the provider and readable from the server through its metadata service, so the OIDC client secret sits there for the
life of the server: use a client secret you can rotate. Progress is in `/var/log/tico-setup.log`.

### Sign-in

| `TICO_AUTH_PROXY` | Also set | Use |
|---|---|---|
| `oidc` | `TICO_OIDC_ISSUER`, `TICO_OIDC_CLIENT_ID`, `TICO_OIDC_CLIENT_SECRET`; optionally `TICO_OIDC_ALLOWED_DOMAINS` (comma-separated, such as `acme.com`) | Google, Okta, Microsoft Entra or any OpenID Connect provider. Register `https://<TICO_DOMAIN>/auth/callback` as the redirect URI with the provider. |
| `cloudflare` | `TICO_ACCESS_ISSUER`, `TICO_ACCESS_AUDIENCE` | Cloudflare Access in front of the tunnel |
| unset (or `none`) | nothing; leave `TICO_DOMAIN` unset | The quick start. The server answers on `http://127.0.0.1:8765` and the owner signs in with the token from `docker compose exec server cat /data/local-owner.token` (`Authorization: Bearer <token>`, or `GET /api/v2/local-signin?token=...`). With `TICO_DOMAIN` set, the server refuses to start. |

The owner is the first human on the roster; add the others in the app. The wizard writes these settings; to change
one later, edit `/opt/tico/.env` and run `docker compose up -d` there. `.env.example` in the bundle lists every setting.

Attachment storage settings also go in `.env` next to `compose.yaml`:

| Setting | Use |
|---|---|
| `TICO_BLOB_BUCKET` | Private attachment bucket, optionally `s3://acme-files/prefix`; unset keeps local storage. |
| `TICO_BLOB_REGION` | Attachment region; otherwise `TICO_BACKUP_REGION` when both endpoint settings are unset, then the AWS default inside the container. |
| `TICO_BLOB_ENDPOINT` | Endpoint URL for an S3-compatible attachment store; unset uses AWS. |
| `TICO_UPLOAD_MAX_BYTES` | Upload limit in bytes; default `2147483648` (2 GiB). |
| `TICO_BLOB_ACCESS_KEY_ID`, `TICO_BLOB_SECRET_ACCESS_KEY` | Optional separate keys for attachments and desktop downloads; set both. Otherwise AWS credentials, profiles or role settings inside the container use boto3's default chain; without those settings, the backup `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY` pair is reused. Grant it the attachment bucket permissions too. Without backup keys, the default chain applies, including IAM roles. |

Docker does not forward AWS credentials from the operator's shell; `AWS_REGION` and `AWS_DEFAULT_REGION`
pass through when set. Set them in `.env`, not in the shell: in-app updates read `.env` only, so a region
exported in a shell would change on the next update. Empty storage settings are treated as unset.

See [File storage](files.md#storage) for bucket permissions and Health warnings.

### Cloudflare Tunnel

No open ports, and Cloudflare Access can do the sign-in. The wizard creates the tunnel when you give it a token; by hand:

1. In Cloudflare Zero Trust, create a tunnel (Networks > Tunnels > Create > Cloudflared) and copy its token.
2. Nothing to route by hand: the compose file runs cloudflared with a small config that sends `TICO_DOMAIN` to
   `http://server:8765` and answers anything else with a 404. The server writes it at every start. Only the DNS
   record is yours: a proxied CNAME for the hostname to `<tunnel id>.cfargotunnel.com` (the wizard creates it).
3. Create an Access application for the hostname with your identity provider and a policy for your humans.
   Note the team URL (`https://<team>.cloudflareaccess.com`) and the application's Audience tag.
4. In `.env`: `COMPOSE_PROFILES=cloudflared,updater`, `TICO_DOMAIN=<hostname>`, `CLOUDFLARE_TUNNEL_TOKEN=<token>`,
   `TICO_AUTH_PROXY=cloudflare`, `TICO_ACCESS_ISSUER=<team URL>`, `TICO_ACCESS_AUDIENCE=<AUD tag>`.
5. `docker compose up -d`

**Which route wins.** cloudflared prefers the configuration Cloudflare holds for the tunnel. A tunnel you created in the
dashboard, or that setup created through the API, is remotely managed and keeps the **Public Hostname** route you set
there: the config the server writes is ignored, so keep that route at `HTTP` `server:8765`. A locally managed tunnel
(made with `cloudflared tunnel create` and reused here by its token) has none, and the server's config is what routes
it. Before this was shipped such a tunnel logged `No ingress rules ... cloudflared will return 503` and answered every
request with a 503 while its container showed as running. `python3 -m setup doctor` now fails on that 503 and on that log
line, and says what to run: `docker compose pull && docker compose up -d` in `/opt/tico`.

**A runner hostname without Access.** To keep Access on the team hostname and let computers and external agents in
without a bypass, add a second proxied CNAME, `runner.<domain>`, to the same tunnel (`<tunnel id>.cfargotunnel.com`),
create no Access application for it, and set `TICO_RUNNER_URL=https://runner.<domain>` in `.env`. The server then also
routes that hostname in the config it writes, for `/api/v2/...` and `/download/...` only (both check their own tokens);
anything else on it gets the 404. A remotely managed tunnel needs the same route added in the dashboard.

Otherwise computers join through the same hostname. If Access sits in front of all of it, give the runners a bypass or a
service token for `/api/v2/runners/*` (the runner authenticates itself with its own token), and give `/api/v2/mcp` a
Bypass policy so humans' own external agents can connect with their tokens ([Connect an external agent](connect-an-agent.md)).

Or run the bots' computer on the server itself and skip Access altogether: the runner joins the server's own Docker
network and talks to it directly. With a one-time code from Settings > Computers > Add computer, on the server:

```
curl -fsSL https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install.sh | sh -s -- --runner \
  --url http://server:8765 --server-network tico_default --code <code> --label "Server computer"
```

`--server-network` writes `runner.override.yaml` in the runner's directory; updates keep it. Size the server for both
(a t4g.medium or 4 GB is a good start for a handful of bots).

### Sizing

- **Server:** 1 to 2 GiB of memory and 10 GiB of disk is plenty (it is a web app and a SQLite database).
- **Runners:** plan roughly 0.5 to 1 GiB of memory for each bot working at the same time, and disk for the bots'
  repositories (20 GiB and up). When bots queue, add another runner computer rather than a bigger one. More in
  [sizing](sizing.md).

## Add computers to run your bots

In the app open **Settings > Computers > Add computer** (or step 4 of the Finish setup wizard, "Add the computer that
runs your bots"), pick the kind of computer, and copy the commands it shows. The one-time code works once and
expires after 15 minutes. Adding a computer never assigns bots to it: choose it for each bot afterwards.

**A new computer signs in to your model by itself when your team has given every computer its model key.** Store the key
once under Credentials (a credential named `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` or `CURSOR_API_KEY`,
type API key or token), open **Manage access** on it and choose **Every computer**. Only a credential administrator can do this,
and BotOps asks for a Confirm click first. From then on, when a new computer joins or gets its first job and its model is not
signed in, the runner (the software on the computer) asks the server for that key as itself, saves it in its own secrets folder
and signs in the way it would with a key you had typed there. Nobody copies a key between computers, and the key is in no log.
For those model-key names, a blank **Bot variable name** is inferred automatically; an explicit variable takes precedence.
Older name-only model keys also offer **Every computer (signs models in)**, and granting one fills the missing variable.
Tool Credentials need a grant per bot; `hub_credential_grant` targets a bot.

Only that model credential comes this way: a bot's own credentials stay with that bot, and a computer never receives them.

A subscription login (a ChatGPT or Claude plan) cannot be copied. If that is how your team signs in, Settings > Health shows
"*computer name*: sign in to Codex" (or Claude Code) for each new computer that needs it. Sign it in from Settings > Computers, or ask
BotOps to start it, then finish the sign-in in your browser.

### Linux or cloud server (Docker)

On any Linux computer, run the line the app shows. It downloads the installer of the release your server runs, so the
runner and its updater start on that same release:

```
curl -fsSL https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install.sh | \
  sh -s -- --runner --url https://tico.example.com --code <code> --label "Build computer"
```

`vX.Y.Z` is your server's release (Settings and Settings > Health show it); `--runner` exists from v0.2.1.

`install.sh --runner` installs Docker if it is missing, puts that release's `runner.compose.yaml` and a `.env`
(`TICO_URL`, `TICO_CODE`, `TICO_RUNNER_LABEL`, and `TICO_TAG` and `TICO_UPDATER_TAG` pinned to the release) in
`/opt/tico-runner` (`--dir` changes it), and runs `docker compose -f runner.compose.yaml up -d`. The compose file has
an **updater sidecar**, which is what keeps the runner on the server's release ([updates](updates.md#a-docker-runner));
a runner started any other way never follows it. Run the line again any time: it keeps the `.env` and repairs the
stack, and `--version vX.Y.Z` moves the pinned tag.

The runner enrolls, starts, and comes back by itself after a reboot or a server restart. Its model logins and the
bots' repositories live in a Docker volume, so running the line again never enrolls a second runner. If the server no
longer knows the runner (a wiped database), start it again with a new code.

**Reinstalling a runner** (its volumes were wiped, or the server forgot it): make a new code in Settings > Computers > Add
computer and run the installer again with it. `--code`, `--url` and `--label` given on that line replace their own keys in the
existing `/opt/tico-runner/.env` (it says which), and every other setting stays; `--url` and `--code` are only required when there
is no `.env` yet. Run with none of them, the installer keeps the `.env` as it is and only repairs and updates.

```
curl -fsSL https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install.sh | sh -s -- --runner --code <new code>
```

**A computer on the same host as a local server (the quick start).** A server that answers on this computer only cannot be
reached at `127.0.0.1` from inside the runner's container, so the runner joins the server's Docker network. The app shows
this line when the server is local:

```
curl -fsSL https://github.com/ticoteam/tico/releases/latest/download/install.sh | \
  sh -s -- --runner --url http://server:8765 --server-network tico_default --code <code> --label "This computer"
```

**Add a second computer on the same host (for a message bot).** A message bot must have a computer of its
own, so a second one on the same host is the usual next step ([mail](mail.md)). Give it a name and run the installer again with
a new code from Settings > Computers > Add computer; the first computer is not touched:

```
curl -fsSL https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install.sh | sh -s -- --runner --name mail \
  --url http://server:8765 --server-network tico_default --code <code> --label "Mail computer"
```

`--name` makes its own directory, `/opt/tico-runner-mail`, with its own compose project and container (`tico-runner-mail`, so
`docker exec tico-runner-mail ...`), its own home volume (`tico-runner-mail_runner-home`, so logins and repositories are not
shared) and its own updater; `--label` defaults to the name. `--server-network` works as above, and `--dir` still overrides
the directory. The settings that differ from the first computer are in `runner.override.yaml` there, which updates keep. Each
computer's updater follows the server's release on its own, so nothing else changes: to update, pin or reinstall it, run the
line again with the same `--name` (or use its directory), and add `--version`, or a new `--code`, as for the first one.

**Moving a runner that was started with a bare `docker run`.** Run the line above on the same computer. It finds the
`tico-runner` volume, points the compose file at it (`TICO_RUNNER_HOME_VOLUME=tico-runner` in `.env`), removes the old
container, and starts the compose one: the login, the bots' repositories and the enrollment carry over, and the runner
now has its updater.

**Plain `docker run` (an alternative that does not update itself).** If you would rather manage the container yourself:

```
docker run -d --name tico-runner --restart unless-stopped -v tico-runner:/home/runner \
  --user 0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add KILL --cap-add SETGID --cap-add SETUID \
  --security-opt no-new-privileges:true \
  ghcr.io/ticoteam/tico-runner:vX.Y.Z join --url https://tico.example.com --code <code> --label "Build computer"
```

The `--user 0` and capabilities are what keep the runner's own login out of the bots' reach (below); without them the
container runs as one user, as it did before. It has no updater: it stays on the release you pinned until you pull a newer image and recreate the container, and
Settings > Health says so.

**Who runs what.** The container's entrypoint starts as root, with five capabilities and nothing else, only to prepare
the volume; the runner itself is the `ticorun` user that owns `/home/runner/runner.json`, its state and the tools
directory, as in every earlier release. Everything a bot runs (each run's model CLI,
its `git`, the sign-in flows) runs as the unprivileged `bot` user, whose home holds the workspace, `secrets/` and the
model logins. So a bot cannot read the runner's own credential, which could claim any bot's work; a run gets its
run token, and asks the runner for its bot's GitHub token over a local socket. Bots still share the `bot` user with
one another ([SECURITY.md](../SECURITY.md#bots-on-one-computer-share-a-trust-boundary-on-purpose)). The first start of a volume from an older image
changes its ownership to match (one time, a minute on a large workspace). If an update is rolled back, the previous
image still starts on the migrated volume and reads its own files; the bots then run as the runner's user again until
the next update. Use `docker exec -u bot` for what a bot should
own (model logins). Legacy secret files stay with the runner supervisor; bots receive granted Credentials.

The image holds no model CLI. Once your team has enabled a provider (Settings > AI providers), the runner installs
that provider's CLI into `/home/runner/tools` in the volume (a minute or two; Settings > Computers shows the progress),
keeps it current between runs, and lets the owner pin a version. See [harnesses](harnesses.md).

Use **Settings > Computers > Sign in** on the Computer you registered. The login stays in its volume.
For terminal recovery, replace `<container-name>` in every command below with the name from that Computer's
Docker sign-in command in **Add computer**, or find it with `docker ps`. An installer command with
`--name build` creates `tico-runner-build`; use the full container name, including `tico-runner-`.
Only the optional unnamed manual install uses `tico-runner`. Sign in as `bot`:

```
docker exec -it -u bot '<container-name>' codex login --device-auth      # ChatGPT subscription: open the URL, enter the code
docker exec -it -u bot '<container-name>' claude auth login              # Claude subscription: open the URL, paste the code back
```

`claude setup-token` is not a sign-in: it prints a long-lived token and leaves Claude Code signed out. To use such a
token, store it as a `CLAUDE_CODE_OAUTH_TOKEN` Credential for every computer instead (above).

Store API keys and other Credentials in **Tools > Credentials**, set their environment-variable name,
and grant them to each bot that needs them. A bot run receives only its grants; it does not inherit
`_shared.env` or the runner's process credentials. On upgrade, each existing bot is granted its own keys and every
key in its computer's `_shared.env`, so its access continues. Bots created after the upgrade start with none. See [Credentials](credential-vault.md).

**Codex with an API key.** Model sign-in on a Computer may use a computer-local key or a team's model key shared
with **Every computer (signs models in)**. The runner sends it to `codex login --with-api-key` on standard input,
with no key in a command line or log. This signs models in; tool Credentials still need a grant per bot.
A key Codex refuses is tried again after ten minutes or when the key changes.
The `.codex` folder in the volume belongs to `bot`, is group-writable and setgid, and its login files are
readable by the runner's group, so a `codex login` you run yourself as `bot` works too.

Check with **Settings > Bots**, or
`docker exec '<container-name>' python -m runner --config /home/runner/runner.json doctor`.

To write the compose setup by hand instead of using the installer, copy `docker/runner.compose.yaml` from the release
to the computer, write a `.env` next to it with `TICO_URL=https://tico.example.com`, `TICO_CODE=<code>`,
`TICO_RUNNER_LABEL=<name>`, and `TICO_TAG` and `TICO_UPDATER_TAG` set to the server's release (`vX.Y.Z`, not `latest`), and run
`docker compose -f runner.compose.yaml up -d`.

**Meeting importers and Close calls run here too, with nothing extra to start.** The runner container also runs the
`importers` and `close-calls` jobs that a Mac runs as launchd jobs, as children of the runner with restart and backoff,
and only while they are wanted:

- *Meeting importers* (Zoom, Google Meet, Granola): in **Tools > Meeting importers**
  tick **Enabled** and choose this computer. The job starts within a minute and stops again when you switch it off
  or pick another computer. Put the tool's credential in the runner's secrets folder, for example
  `docker exec '<container-name>' sh -c 'umask 077; printf "%s\n" "GRANOLA_API_KEY=<key>" | tee /home/runner/workspace/secrets/granola.env >/dev/null'`
  (the file names are in [meetings](meetings.md#meeting-importers)); `docker exec '<container-name>' python -m runner
  --config /home/runner/runner.json importers-doctor` says which are present.
- *Close calls*: put `CLOSE_API_KEY=<key>` in `/home/runner/workspace/secrets/close-calls.env` the same way. The job
  starts when that file appears; keep it on one computer only. See [meetings](meetings.md#close).

`docker logs '<container-name>'` carries the jobs' lines (`Tico side jobs: started importers`), and each importer's health
shows on its Settings card and the Meetings Sources strip.

*Mail and calendar (`connectors`)* run on a Linux runner the same way, from the team's Google service-account
key ([Message bots](mail.md#works-on-linux-runners) has the Google Workspace setup). Put the key in the runner's state directory,
where only the runner can read it, and keep it on one computer only:
`docker exec -i -u ticorun '<container-name>' sh -c 'umask 077; cat > "$(ls -d /home/runner/state-* | head -1)/google-sa.json"' < google-sa.json`
(the runner refuses a key that is not mode 0600). A key an older install kept in `workspace/secrets/google-sa.json` is moved
there once, automatically. Bots cannot read it; a message bot asks the runner for a token for its own mailbox, and a message bot
gets a computer to itself ([Message bots](mail.md#who-can-read-the-key)).
The job starts within a minute of the file appearing, builds its Python environment into the volume the first time
(about a minute; `docker logs` shows it), and stops when the file is removed. Instead of the key, an owner who
sets `TICO_PROCESSING_OPERATORS` on the server assigns the job to that owner's runners. `docker exec '<container-name>'
python -m runner --config /home/runner/runner.json connectors-doctor` says whether the key is found.
`TICO_SIDE_JOBS=0` in the container's environment turns the supervisor off.

Update a runner with `docker pull ghcr.io/ticoteam/tico-runner:latest`, then remove and re-run the container
(for the manual example above, `docker rm -f tico-runner`, then the same `docker run` line; the volume keeps everything). With the compose file it
is `docker compose -f runner.compose.yaml pull && docker compose -f runner.compose.yaml up -d`. The runner
finishes runs in progress (up to 15 minutes) before it stops.
With the compose file the runner also updates itself to the release its server runs, through an `updater` sidecar
(the only container with the Docker socket); see [updates.md](updates.md).

The runner container is not a sandbox between bots: all bots on one runner run as the same user. It cannot see
the server or its data (they are on other computers), and bots cannot reach a Docker socket. On AWS, keep the
instance metadata hop limit at 1 for runner computers too.

### Mac

**Mac** in Add computer shows the same one-line installer as Linux; it needs Docker Desktop running and installs into
`~/tico-runner-<name>` without sudo. Sign the computer in from **Settings > Computers** afterwards.

The native runner is unchanged and fully supported: a Mac keeps its own logins, desktop apps and files. Choose **Mac**
in Add computer and **Use a Tico checkout instead**, then in the Tico checkout on that Mac run the command it prints
(`scripts/setup-runner.sh "$HOME/Downloads/<setup-file>.json"`, or `scripts/tico -e <env> enroll --code-file <file>
--label "Studio Mac"`, then `scripts/tico -e <env> install bot`; see the README). It connects to
`https://<TICO_DOMAIN>`, runs under launchd, and reconnects on its own after the server restarts.

**Mac updates.** The runner follows the server's release by itself ([updates.md](updates.md#a-mac-or-linux-checkout)).
After a healthy update it also restarts the side jobs installed beside it (`connectors`, `close-calls`, `importers`), and
each side job exits and restarts when it sees the checkout move to another revision (checked about once a minute), so no job
keeps old code in memory. `scripts/tico restart` does the same by hand.

### Windows PC (WSL 2)

A Windows PC runs the Linux runner inside WSL 2. Choose **Windows** in Add computer and paste the line it shows into
PowerShell opened **as administrator**:

```
& ([scriptblock]::Create((irm https://github.com/ticoteam/tico/releases/download/vX.Y.Z/install-wsl.ps1))) `
  -Url https://tico.example.com -Code <code> -Label 'Build PC'
```

`install-wsl.ps1` (in `infra/windows/`, published with each release) installs WSL and Ubuntu when they are missing (as root, skipping the "create a Linux user" prompt),
turns on systemd in Ubuntu, then runs `install.sh --runner` inside it, so Docker, the runner and its updater are the same
as on a Linux server. If WSL was just enabled, Windows asks for a restart first: restart, take a new code from Add
computer (codes last 15 minutes) and run the line again. WSL stops an idle distribution, so the script adds a logon task,
**Tico runner keep-alive**, that holds Ubuntu open, and turns off sleep on mains power (`-AllowSleep` leaves it alone).
To undo both: `Unregister-ScheduledTask 'Tico runner keep-alive'` and `powercfg /change standby-timeout-ac 30`.
Bots run while that Windows user is signed in. Sign the computer in to Claude Code or Codex from **Settings > Computers**.
Docker Desktop is not needed; if its WSL integration is on for Ubuntu, the runner uses it.

### A Linux checkout (systemd)

A Linux computer can also run the native runner from a Tico checkout, the way a Mac does. `scripts/tico` drives it with
systemd **user** units instead of launchd; no root is needed:

```
scripts/tico -e <env> enroll --code-file <file> --label "Build box"     # or scripts/setup-runner.sh
scripts/tico -e <env> install            # tico-bot, tico-connectors, tico-close-calls, tico-importers: Restart=always
scripts/tico -e <env> status
```

`install` writes `~/.config/systemd/user/tico-<job>.service` (`tico-<env>-<job>.service` with `-e`), enables and starts
each, and tells you to run `loginctl enable-linger $USER` when linger is off (otherwise the runner stops at logout and
does not start at boot). It needs a user session: over a bare ssh login that has none, set
`XDG_RUNTIME_DIR=/run/user/$(id -u)` first. `restart`, `logs`, `doctor`, `uninstall` and `update` behave as on a Mac.
Run `install` once: it is also what lets the runner update itself when the server does
([updates.md](updates.md#linux-from-a-checkout-run-scriptstico-install-once)); until then Settings > Computers and Health say
"run `scripts/tico install` once".

## Slack

Add `slack` to `COMPOSE_PROFILES` to run the Slack service, then paste the app's tokens in Settings. Steps and troubleshooting: [slack.md](slack.md).

## Update

Use **Update now**, or run the target release's installer again to update both the pinned image tag and compose bundle, preserving `.env`.
Pulling an unchanged pinned tag does not upgrade Tico. See [Updates](updates.md) for manual updates, self-update and rollback.
Data lives in named volumes and survives updates. Computers follow the server's release.

### One-click updates

With `updater` in `COMPOSE_PROFILES` and `TICO_UPDATER_URL=http://updater:8080` in `.env` (both are in
`.env.example`), the app's **Update now** button works. The `updater` service:

- listens on the compose network only (no published port); the server calls it with a shared secret that the
  server generates into the `tico-control` volume on first start, which only the server and updater mount;
- `POST /update {"version": "vX.Y.Z" | "latest"}` downloads and verifies that release's compose bundle and replaces the files in it (never `.env`), pulls that image, recreates the server, waits up to 3 minutes for
  `/healthz`, rolls back the image and the bundle if it does not come back, and otherwise writes `TICO_TAG` into `.env`;
- `GET /status` returns `{"state": "idle|pulling|restarting|healthy|rolled_back|failed", "from", "to", "message"}`;
- both calls need `Authorization: Bearer <token>`. The server gets `TICO_UPDATER_URL` and `TICO_UPDATER_TOKEN`, and
  the running version as `TICO_VERSION` (also the image label `org.opencontainers.image.version`).

Tradeoff: the updater mounts the Docker socket, which is root on the host. The server runs no bots, so nothing
untrusted shares that host, and the updater runs nothing but `docker compose` for the `server` service. To turn it
off, delete `updater` from `COMPOSE_PROFILES` and `TICO_UPDATER_URL` from `.env`. Self-update is on by default: a helper
recreates the updater, and restores the previous updater if the replacement fails. A failed server health check rolls the server
image, bundle and database back. See [Updates](updates.md#the-servers-own-updater) for self-update and recovery.
Computers follow the server's release by themselves.

The release bundle includes the current `compose.yaml`, so **Update now** also adds newly forwarded
attachment and AWS settings on existing installs while preserving `.env`. After upgrading, add
`TICO_BLOB_BUCKET` to that `.env` and run `docker compose up -d`. If it was already set before the
upgrade, the recreated server picks it up automatically. For manual upgrades, run the target
release's installer again or replace `compose.yaml` with that release's copy before recreating
the server; updating the image alone keeps the old environment list.

## Backups and restore

Backups are on from the first start. Inside the server container, Litestream copies the database within seconds
and attachments (`/data/blobs`) follow every 30 seconds.

| `.env` | Where the copies go | Survives |
|---|---|---|
| `TICO_BACKUP_URL` set | your S3, R2 or S3-compatible bucket | losing the server |
| nothing set (default) | the `tico-backups` Docker volume on the same server | deleting `tico-data`, not losing the server |
| `TICO_BACKUP=off` | nowhere | nothing |

Without a bucket the server logs a warning at start and every hour, and the config payload
(`GET /api/v2/config`) carries `backup: {mode, last_replicated_at, target_kind, warning}` with `mode` one of
`remote`, `local-only` or `off`, so the app can show it to the owner. Point it at a bucket when you can:

```
TICO_BACKUP_URL=s3://my-bucket/tico
TICO_BACKUP_ENDPOINT=https://<account>.r2.cloudflarestorage.com   # R2 or another S3-compatible store; omit for AWS
TICO_BACKUP_REGION=auto                                            # omit for AWS
LITESTREAM_ACCESS_KEY_ID=...
LITESTREAM_SECRET_ACCESS_KEY=...
```

`python3 -m setup` offers to create that storage: on AWS a versioned, encrypted, private S3 bucket and an IAM user
limited to it, on Cloudflare an R2 bucket (with an API token that has Workers R2 Storage: Edit), or a bucket you
already have. Turn on bucket versioning yourself if you make one by hand; a deleted or overwritten backup is then
still recoverable. With `TICO_BLOB_BUCKET` set, attachments already live in S3 and are not copied again.

**The credential key is copied too.** When no `TICO_CREDENTIAL_KMS_KEY` is set, the stored credentials are encrypted with
a local key in `/data/credential.key` ([Credentials](credential-vault.md)); Litestream carries the database, not that file, and a
database restored without it cannot decrypt a credential. The backup loop copies the key to the same place as the rest
(the bucket and prefix in `TICO_BACKUP_URL`, else the `tico-backups` volume), as `credential-key/credential.key`, when it
first appears and whenever it changes. It is stored as the bucket stores everything: encrypted at rest by the bucket (S3
and R2 encrypt every object by default), which also means anyone who can read the bucket can read the key and the database
together, so keep the bucket private and its access key to that bucket alone. The key is never logged or sent anywhere else,
and Health warns while a local key exists and has not been copied yet. `GET /api/v2/config` carries
`backup.credential_key: {present, copied_at, current}`.

**A lost volume never becomes a blank team.** The server records that it is an existing team outside the
database: a `.tico-environment` file in the data volume and an `environment.json` object beside the backup. When it
starts with an empty volume it first restores; if the restore fails (wrong key, no network) and a team is known
to exist, or the backup cannot be read at all, it refuses to start and says why, instead of creating an empty team
and replicating it over your backup. A genuinely new install (nothing anywhere) starts as usual. To begin a new
team over an existing backup on purpose, set `TICO_INITIALIZE_EMPTY=1` (or run `server --initialize-empty`).

<a id="restore"></a>

**Restore into an empty data volume**, from the bucket or, with no `TICO_BACKUP_URL`, from `tico-backups`:

```
docker compose stop server
docker compose run --rm --no-deps server restore    # database, attachments and the credential key
docker compose up -d
```

**The credential key is backed up with the database.** Credentials are encrypted with a key in `/data/credential.key`
(docs/credential-vault.md). The backup copies it beside the database whenever it changes, and Health warns if it has not been
copied. `restore` puts the key back at `/data/credential.key` (mode 0600) when the backup has one. A key already on the
volume is left alone (with `--force` it is kept beside the restored one as `credential.key.before-restore.<time>`). If
the database was restored by itself on first start, or from a bucket that has no key, run `restore` again or copy the
object `credential-key/credential.key` from the backup location to `/data/credential.key` by hand, then restart. With
`TICO_CREDENTIAL_KMS_KEY` set there is no file to keep.

**Restore an existing install.** `restore` refuses a volume that already holds data. Stop the server and add `--force`
to restore the database, attachments and Credential key together:

```
docker compose stop server
docker compose run --rm --no-deps server restore --force
docker compose up -d
```

Tico keeps the current database as `hub.sqlite.before-restore.<time>` and the current key as
`credential.key.before-restore.<time>` beside the restored files. The install's permanent id
(`TICO_ENVIRONMENT_ID`) is stored in the database, so it comes back with the restore and the Macs and runners
already enrolled keep working.

**Move to a new server:** install Docker on the new computer, copy the same `.env` (and `compose.yaml`), then

```
docker compose pull
docker compose run --rm --no-deps server restore
docker compose up -d
```

and point the domain at the new computer (Cloudflare tunnel: run the same tunnel token there). Stop the old server
first; two servers writing to one bucket corrupt the replica. A fresh volume that finds a backup also restores the
database by itself on first start, but run `restore` so attachments and any error are visible. This needs the
bucket, so with the default local-only backups you can only rebuild on the same computer; that is the reason to set
`TICO_BACKUP_URL`.

Runners hold no team data that is not in a git remote, but back up their volume if bots keep local work.

### Check the AWS identity

On AWS, the server's default AWS credentials (an instance or task role, usually) decide which account its KMS key,
Secrets Manager secrets and attachments bucket are used in. To make sure a start never runs with another account's
credentials (a changed role, a copied server), set the account it must run as:

```
TICO_EXPECTED_AWS_ACCOUNT=123456789012
TICO_EXPECTED_AWS_ROLE=tico-server        # optional: a role name, or arn:aws:iam::123456789012:role/path/tico-server
```

Before the server and the Slack gateway start, and before `restore` reads the backup, each asks AWS STS who it is
(`GetCallerIdentity`, which needs no IAM permission). It refuses, with one line in the log naming the expected and the
actual account or role, when the account differs, the caller is not that role, no credentials are found, or STS does not answer (about
10 seconds a try, two tries). A rehearsal checks too. Unset, nothing is checked and nothing is called.

### Rehearse a migration

To try a move (a new server, a new release, a new host) on a copy of the real data first, start the copy with
`TICO_REHEARSAL=1`. It runs the same migrations and initialization as any start and shows the whole team, but nothing runs
on a timer and nothing leaves the server:

| Off in a rehearsal | |
|---|---|
| Scheduler and directory sync | routines make no jobs, and the directory is not read (so nobody is marked as having left) |
| Backups | no Litestream, no replication loop, and nothing is written to `TICO_BACKUP_URL`; the log says so at start |
| Release check and usage count | nothing goes to GitHub or Tico HQ |
| Outbound from the server | contact support and team suggestions (HQ), the Slack gateway (its container waits), GitHub App calls, error and analytics reporting, and **Update now** |
| Uploads to an attachments bucket | reading works; adding to `TICO_BLOB_BUCKET` is refused |

The server sends no email of its own. The app shows a banner on every page, **Rehearsal: nothing runs or leaves this server**,
and `GET /api/v2/config` carries `"rehearsal": true`.

```
# in a separate directory, with a copy of compose.yaml and .env
# Set these in the copy's .env, and leave TICO_DOMAIN and COMPOSE_PROFILES empty:
COMPOSE_PROJECT_NAME=tico-rehearsal
TICO_PORT=8877
TICO_REHEARSAL=1
TICO_BACKUP_URL=s3://my-bucket/tico     # restore reads this backup; replication stays off
# Use local owner sign-in: unset TICO_AUTH_PROXY, TICO_PUBLIC_URL, TICO_RUNNER_URL and TICO_UPDATER_URL.
docker compose up -d
```

For a local backup on the same computer, a second directory also needs its own project, data volume and port.
Stop the source server first so its local backup is complete. In its original install directory:

```sh
docker compose stop server
```

Copy `compose.yaml` and `.env` into a separate rehearsal directory. In the copied `.env`, set
`COMPOSE_PROJECT_NAME=tico-rehearsal`, `TICO_PORT=8877` and `TICO_REHEARSAL=1`; leave `TICO_DOMAIN`,
`TICO_AUTH_PROXY`, `TICO_PUBLIC_URL`, `TICO_RUNNER_URL`, `COMPOSE_PROFILES`, `TICO_UPDATER_URL` and
`TICO_BACKUP_URL` empty. Create `rehearsal.yaml` there:

```yaml
services:
  server:
    volumes:
      - tico-backups:/backups:ro
volumes:
  tico-backups:
    external: true
    name: tico_tico-backups
```

Replace `tico_tico-backups` with the source project's backup volume (`<project>_tico-backups` for the default
volume naming). The rehearsal's data volume remains separate. In the rehearsal directory:

```sh
docker compose -f compose.yaml -f rehearsal.yaml run --rm --no-deps server restore
docker compose -f compose.yaml -f rehearsal.yaml up -d server
docker compose -f compose.yaml -f rehearsal.yaml exec server cat /data/local-owner.token
```

Open `http://127.0.0.1:8877/api/v2/local-signin?token=<token>` with that token. The backup is mounted read-only:
restore reads the database, attachments and credential key; rehearsal does not write backups or call a decision provider.
Restart the source from its original directory with `docker compose start server`. Remove the rehearsal when finished with
`docker compose -f compose.yaml -f rehearsal.yaml down -v`; its external source backup volume is kept.

Set `TICO_REHEARSAL=1` before the first start of the copy. Do not enrol runners against it or give it the production
domain: a runner that connects would run bots against the copy's queue. Leave rehearsal by building a new server without
the variable; a copy that ran in rehearsal was never backed up. An explicit `TICO_SCHEDULER=0` (without `TICO_REHEARSAL`)
turns off only the scheduler and directory sync; unset, the server starts them.

Generated **Add computer** commands use this server's Docker network and a separate runner name for each enrollment.
With `COMPOSE_PROJECT_NAME=acme`, the network is `acme_default`; if you use a custom network, set
`TICO_SERVER_NETWORK` in the server's `.env`. Its runner commands use that network rather than the default `tico_default`.

At startup, the Computer catches up local teammate commits using each teammate's scoped GitHub credential, through
the same in-memory credential helper used after a task. If scoped access is unavailable, the commits stay local;
a Computer without a connected GitHub App keeps its existing Git access.

In **Settings > Computers**, **Remove computer** revokes its registration immediately. Its assigned bots remain visible;
use **Reassign in Bots** to choose another computer. The page shows how to stop the Docker runner from that computer's
install directory; this keeps its repositories and sign-in. Re-register it with a new code if you want to use it again.

## Operating it

| | |
|---|---|
| Status | `docker compose ps` (the server shows `healthy`) |
| Logs | `docker compose logs -f server`; on a runner computer `docker logs -f tico-runner` |
| Restart | `docker compose restart server` (runners reconnect by themselves) |
| Stop and keep data | `docker compose down` |
| Remove everything | `docker compose down -v` (deletes the data volume) |

Every service restarts automatically (`restart: unless-stopped`), including after the host reboots.

## Building the images yourself

```
docker build --target server  -t tico:local .
docker build --target runner  -t tico-runner:local .
docker build --target updater -t tico-updater:local .
TICO_IMAGE=tico TICO_TAG=local docker compose up -d
TICO_RUNNER_IMAGE=tico-runner docker/smoke.sh     # local image checks
```

Tool versions and their sha256 digests are in `docker/versions.env` and are checked during the build. The
model CLIs are not in the image; the runner installs them (see [harnesses](harnesses.md)).

## Advanced installs

Everything above is the one way to install: Docker is the only supported way to run the server. Driving the wizard from a
laptop, pasting cloud-init by hand, and putting your own load balancer or Cloudflare Access in front are in
[install-advanced.md](install-advanced.md).
