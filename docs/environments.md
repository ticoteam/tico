# Environments

One environment is one team: its own server or its own account on a hosted server, its own
database, workspace, subscriptions, names and Mac app. Several can run on one Mac. `-e <slug>`
(or `TICO_ENV`) picks one for every `scripts/tico` command; without it you get the original
single-team setup described under [the legacy default](#the-legacy-default).

The reference implementation is `clients/environments.py` (create, list, show, remove),
`scripts/tico` (services and lifecycle) and `runner-rs/src/profiles.rs` (subscriptions). Start from the
quick start in the [README](../README.md); this page is the contract.

## The environment directory

`~/.config/tico/environments/<slug>/`, mode 0700. `TICO_ENVIRONMENTS_DIR` moves the root. A slug is
lowercase letters, digits and single hyphens.

| Path | Mode | Written by | What it is |
|---|---|---|---|
| `environment.json` | inside the 0700 directory | `env create` | Identity and names. The only file `scripts/tico` reads to resolve `-e` |
| `server.env` | 0600 | `env create --local` | `KEY=VALUE` lines the local backend runs with, including the provider choice (`TICO_ENABLED_PROVIDERS`, `TICO_DEFAULT_RUNTIME`, `TICO_DEFAULT_MODEL`), which seeds the database once. No credential is in it |
| `local-owner.token` | 0600 | `env create --local` | 32 url-safe random bytes: the loopback owner session credential |
| `hub.sqlite`, `-wal`, `-shm` | as SQLite writes them | `backend.manage initialize`, then the server | This team's database |
| `blobs/` | 0700 | `env create --local` | Attachments, meeting files and generated files |
| `registry/` | 0700 | `env create --local` | Seed data: `employees.yaml`, `people.yaml`, `hub-access.yaml`, copied from `templates/environment-registry/` with this team's names filled in. The seed roster is two bots, `coo` (the assistant) and `botops`, both `planned`; every other bot is picked from the templates during [Finish setup](onboarding.md) |
| `logs/` | 0700 | `env create --local` | `api.log` and `tico-<job>.log` |
| `runner.json` | 0600 | `enroll` | This computer's registration for this environment |
| `state-<runner_id>/` | 0700 | the runner | `runner.sqlite`, `heartbeat`, `runtimes.json`, `runner-revision`, locks, warm sessions, routine checkouts |
| `profiles/<name>/` | 0700 | `profile add` | One subscription: a provider home per runtime, plus `profile.json` |
| `icon.png` | any | you, optionally | The app icon `scripts/app.sh --env <slug>` builds with |

The **workspace** is deliberately outside this directory, because it is the team's own material
rather than the product's state. It defaults to `~/Companies/<Company>`, is created mode 0700, and
holds `bot-<slug>/` bot checkouts (older `emp-<slug>/` folders keep working), their uncommitted `bot-<slug>.data/` siblings, and `secrets/`
(mode 0700) with legacy credential files for migration and the service jobs' own files.
Bot runs receive only granted Credentials from Tico. `env remove` never deletes it.

Every run is given the workspace as `HUB_WORKSPACE`, alongside `HUB_API_URL`, `HUB_TOKEN`,
`HUB_BOT` and `HUB_DIR` (this checkout). It is how `hub bot create` knows where a new bot
repository goes, and how a bot finds a sibling's work.

### `environment.json`

| Field | Meaning |
|---|---|
| `id` | 16 hex characters, generated once, permanent. Owns resources: the runner registration, the app's bundle identifier, the mismatch guard. Never a display name |
| `slug` | The short name you type after `-e`. Appears in launchd labels and paths |
| `company_name`, `app_name`, `assistant_name` | Display names. `app_name` defaults to the team, `assistant_name` to the app. All three may be changed later |
| `url` | Where clients reach the server: `http://127.0.0.1:<port>` for a local server, the hosted address otherwise |
| `server` | `local` or `remote`. `local` is what makes `scripts/tico -e <slug> server ...` legal |
| `port` | Local only. The first free loopback port from 8770 that no other environment has claimed |
| `workspace` | Absolute path to the bot checkouts and `secrets/` |
| `created_at` | UTC timestamp, for the record |

### `server.env`

Written for a local server only, and read in two places: the launchd job's `EnvironmentVariables`,
and a hand-run `uvicorn`, so both see exactly the same configuration. Keys:

`TICO_DB`, `TICO_BLOB_DIR`, `TICO_REGISTRY_DIR` (all inside the environment directory),
`TICO_PUBLIC_URL` and `TICO_RUNNER_URL` (both the loopback URL), `TICO_ENVIRONMENT_ID`,
`TICO_TEAM_NAME` (`TICO_COMPANY_NAME` remains an alias), `TICO_APP_NAME`, `TICO_ASSISTANT_NAME`, `TICO_OWNER_EMAIL`,
`TICO_LOCAL_OWNER_TOKEN_FILE`, `TICO_SCHEDULER=1`, and `TICO_GITHUB_OWNER` when
`env create --github-owner` was given. Values are single lines; a newline is refused at creation.

A copy of real data that must send nothing out takes `TICO_REHEARSAL=1` in `server.env`
(`TICO_SCHEDULER` and the other switches are then ignored); see [install.md](install.md#rehearse-a-migration).

`server install` bakes these values into the launchd plist. After editing `server.env`, re-run
`scripts/tico -e <slug> server install`, not `server restart`, or the job keeps the old values.

### `runner.json`

`enroll` writes `runner_id`, `token`, `operator`, `url`, `projects_dir` (the workspace),
and `environment`, the environment id this registration belongs to. Without `capacity` (or with
`"auto"`) the runner sizes how many turns it runs at once to the machine: 5 per CPU core, as memory allows (3 per
GB), at least 4 and at most 64. Turns mostly wait on their model provider, so this is well above the core count. A
number is kept (up to 256), so set `"capacity": 80` to go higher. The `"capacity": 4` that setups before October 14,
2026 wrote becomes `"auto"` once (`capacity_auto` marks it done); set 4 again to keep it. The owner's **Turns at once**
for a computer (Settings > Computers) replaces this number for that computer, up to 256: the runner takes it from its
heartbeat reply, and a runner that predates `turns_at_once_v1` runs at most the capacity it reports. Mode 0600 is
enforced on every command. Profiles add three more keys:

```json
{"profiles": {"acme": {"dir": ".../environments/acme/profiles/acme", "share_operator": false}},
 "default_profile": "acme"}
```

A `repos` map (`{"sales": "/some/other/checkout"}`) overrides the `<workspace>/bot-<slug>`
convention for a bot whose checkout is elsewhere.

## The legacy default

Without `-e`, `scripts/tico` uses `~/.config/tico/runner.json` (`TICO_RUNNER_CONFIG` overrides),
logs under `<parent of the checkout>/runtime/`, labels `team.tico.tico-<job>`, and the hosted site
URL compiled into the script. That is Acme's existing setup, unchanged: it has no environment
directory, no `server` subcommand (its server is hosted), and no profiles, so every bot runs on the
owner's own `~/.codex` and `$HOME`. `scripts/tico env list` shows it last, as
`acme (legacy default)`, next to whatever environments exist. Nothing has to be migrated: an
environment can be created beside it and the two do not share a label, a log or a port.

## Services and logs

launchd agents live in `~/Library/LaunchAgents`, mode 0600, in the `gui/<uid>` domain.

| Job | Legacy label | Environment label | Log |
|---|---|---|---|
| bot runs | `team.tico.tico-bot` | `team.tico.tico.<slug>.bot` | `<env>/logs/tico-bot.log` |
| tool snapshots | `team.tico.tico-connectors` | `team.tico.tico.<slug>.connectors` | `<env>/logs/tico-connectors.log` |
| local server | not applicable | `team.tico.tico.<slug>.api` | `<env>/logs/api.log` |

Default-environment logs are `~/.config/tico/logs/tico-<job>.log` (older installations may still use `<parent of the checkout>/runtime/tico-<job>.log`). `scripts/tico -e <slug> logs`
reads the path out of the installed plist, so a job installed with an older layout still tails
correctly. `restart` and `update` wait for runs in flight before bouncing the bot job;
`TICO_RESTART_NOW=1` skips the wait when a hung run is the reason for restarting.

## Subscription profiles

A profile is a named directory holding one provider login. The one named `default_profile` is the Computer's login:
every bot on the Computer runs on it, and a bot cannot choose another. For a different account, add another
environment (another runner, which can be on the same machine). Credentials stay on the Computer.
See [Computers](computers.md) for connections and browser sign-in.

```bash
scripts/tico -e acme profile add acme                 # first one added becomes the default
scripts/tico -e acme profile login acme codex         # interactive, the CLI owns the browser flow
scripts/tico -e acme profile list
```

`--share-operator` makes a profile that does not relocate anything: its bots use the owner's own
`~/.codex` and `$HOME`, which is what every registration made before profiles existed already does.
`profile login` refuses on such a profile, because there is nothing separate to sign in to, and on any profile but
the default.

After the server/runner `computer_logins_v1` handshake, every run uses `default_profile`, or the operator's own logins
without one. A `bot_profiles` entry, and a bot's choice saved by an earlier release, is ignored (the server upgrade
deletes the saved choices). Until an older server upgrades, an updated runner preserves its existing local and
server account selections. An older runner reporting named bot logins to a new server waits for its own update;
unnamed legacy runners continue working. See [Computers](computers.md#subscriptions-from-earlier-releases).
Linux Docker profiles currently share the existing bot Unix user; their separate login directories
do not enforce file isolation between groups.

| Runtime | How the profile moves its login |
|---|---|
| `codex` | `CODEX_HOME` points at `<profile>/codex` |
| `claude` | `HOME` points at `<profile>/claude`, keeping the existing login layout; an inherited `CLAUDE_CONFIG_DIR` is dropped, since Claude Code would read it ahead of `HOME` |
| `grok` | `HOME` points at `<profile>/grok`. Grok Build 1.0.30 also honours `GROK_HOME` (probed 2026-09-15), but one rule for both keeps a profile a single directory |
| `gemini` | No variable in the run's environment: the Gemini host is handed `<profile>/gemini` and sets `GEMINI_CLI_HOME` and `GEMINI_CLI_SYSTEM_SETTINGS_PATH` itself |

Because a relocated `HOME` is an empty home, `profile add` copies `~/.gitconfig` into the `claude`
and `grok` homes, so a bot's commits still have an author. Edit them per environment if that is wrong.

**Gemini is an API key, not an interactive login.** `profile login <name> gemini` says so and
exits: store a Credential with **Bot variable name** `GEMINI_API_KEY` in **Tools → Credentials**
and grant it to the bot. **Every computer (signs models in)** separately signs model software in;
a bot still needs its own grant. Existing credential files are migration sources only.
The profile still owns that runtime's session directory.

**Antigravity stays per macOS user.** A bot with `runtime: gemini` and `harness: antigravity` runs
Google's `agy` CLI, whose Google account sign-in is per user account and is not moved by a profile.
Its per-bot session files sit under the runner's state directory. Readiness only checks that `agy`
is on `PATH`; the first real run is what proves the sign-in. Two environments that both need
Antigravity therefore need isolated mode, not two profiles.

`doctor` and `status` print one line per bot, `<bot>: subscription profile <name> (<state>)`, where
the state is `ready`, `unknown`, `missing` or `failed`. A runtime row reports its worst profile
first, but a bot is only blocked by its own.

## Sign-in options

`TICO_AUTH_PROXY` picks who vouches for humans in the browser. Runner, bot and personal-token bearer
credentials work the same under every option and never depend on a browser session; a bad bearer is
always a `401`. Whatever the option, the email must be on the human roster (otherwise `403`), the
server refuses to start when the selected option is missing its settings, and writes are
origin-checked against `TICO_PUBLIC_URL`.

| Option | `TICO_AUTH_PROXY` | You need |
|---|---|---|
| Built-in OpenID Connect (default choice) | `oidc` | A server, a DNS name with HTTPS in front, one OAuth client |
| Cloudflare Access | `cloudflare` | A Cloudflare tunnel and an Access application |
| AWS ALB + Cognito | `aws-alb` | An ALB and Cognito you run yourself in front of the server ([install-advanced.md](install-advanced.md#advanced-your-own-front-door)) |
| Neither (loopback only) | unset | Local sign-in, below |

### Built-in OpenID Connect

Tico redirects to the provider (authorization code with PKCE, `state` and `nonce`), verifies the ID
token (signature from the provider's JWKS, `iss`, `aud`, `exp`, `iat`, `nonce`, a verified email,
optional allowed domains) and starts its own session. TLS termination is not part of Tico: put any
HTTPS terminator (a load balancer, Caddy, nginx, a tunnel) in front and set `TICO_PUBLIC_URL` to the
public `https://` address. Loopback may use `http` for development.

```
TICO_AUTH_PROXY=oidc
TICO_PUBLIC_URL=https://tico.example.com
TICO_OIDC_ISSUER=https://accounts.google.com
TICO_OIDC_CLIENT_ID=...
TICO_OIDC_CLIENT_SECRET_FILE=/etc/tico/oidc-secret     # or TICO_OIDC_CLIENT_SECRET
TICO_OIDC_ALLOWED_DOMAINS=example.com                  # optional
```

The redirect URI to register with the provider is `https://<host>/auth/callback`.

**Google.**
1. In the Google Cloud console pick or create a project, then open APIs & Services, OAuth consent
   screen. Choose Internal if every human is in your Workspace (otherwise External), name the app,
   and add the scopes `openid`, `email` and `profile`.
2. Open Credentials, Create credentials, OAuth client ID, type Web application.
3. Under Authorized redirect URIs add `https://<host>/auth/callback`.
4. Copy the client ID and client secret into `TICO_OIDC_CLIENT_ID` and the secret setting.
5. Set `TICO_OIDC_ISSUER=https://accounts.google.com`. With `TICO_OIDC_ALLOWED_DOMAINS` set, the ID
   token's `hd` (hosted domain) claim and the email domain must both match, so a personal Gmail
   account is refused. `email_verified` must be true.

**Microsoft Entra ID.**
1. In the Entra admin center open App registrations, New registration. Choose Accounts in this
   organizational directory only (single tenant).
2. Under Redirect URI choose Web and enter `https://<host>/auth/callback`.
3. From Overview copy the Application (client) ID and the Directory (tenant) ID.
4. Under Certificates & secrets create a client secret and copy its Value (not its ID).
5. Set `TICO_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0`. The tenant must be
   named: `common`, `organizations` and `consumers` are refused at startup, and the token's `tid`
   must equal it. The email is the token's `email` claim, else `preferred_username`. Microsoft
   publishes no `email_verified`, so the tenant check is what vouches for the address; use
   `TICO_OIDC_ALLOWED_DOMAINS` to also restrict the domain (for example to keep out guest accounts
   with other domains).

**Any other issuer** with `/.well-known/openid-configuration` works: it must return an `email`
claim with `email_verified: true`, sign ID tokens with an asymmetric algorithm, and accept the
client secret in the request body or HTTP Basic.

Sessions. The browser holds a random id in a `__Host-tico_session` cookie (`Secure`, `HttpOnly`,
`SameSite=Lax`, path `/`; on loopback `http` the prefix and `Secure` are dropped). Only its hash is
stored, in `oidc_sessions`. A session ends after 30 days idle or 90 days in all (`TICO_SESSION_IDLE_SECONDS` and
`TICO_SESSION_ABSOLUTE_SECONDS` change these), is replaced on each
sign-in, is deleted when the owner marks the human as left, and is deleted by
`GET /api/v2/logout`, which then lands on a "signed out" page with a sign-in button. Tico ends its
own session only, not the one at Google or Microsoft. `TICO_SESSION_SECRET` signs only the short
login round trip, so changing it cancels sign-ins in progress; to end every session, delete the rows
in `oidc_sessions`. No provider token is stored or logged.

A browser page request without a session is redirected to `/auth/login?next=<path>`; API calls get
`401` with `error.sign_in` set, which the app follows to the login page. `next` must be a same-origin
path. An email that passes the provider but is not on the roster sees a page saying it is not on the
team's list.

### Cloudflare Access and AWS ALB + Cognito

Here a proxy in front of the server authenticates the human and passes a signed JWT. A header from
the other provider is ignored.

- `cloudflare` (the default when `TICO_ACCESS_ISSUER` is set) verifies the Access JWT from
  `Cf-Access-Jwt-Assertion` or the `CF_Authorization` cookie. Needs `TICO_ACCESS_ISSUER` and
  `TICO_ACCESS_AUDIENCE`.
- `aws-alb` verifies `x-amzn-oidc-data` from an ALB with an authenticate-cognito action. Needs
  `TICO_ALB_ARN` and `TICO_ALB_REGION`. The token must be ES256 and its `signer` must equal the ARN;
  only then is the key fetched from `https://public-keys.auth.elb.<region>.amazonaws.com/<kid>`.
  It must carry an unexpired `exp` and an `email` (and `email_verified`, if present, must not be false).
- With neither set, only loopback sign-in (below) is available.

`GET /api/v2/logout` ends the session. Under `cloudflare` it redirects to `/cdn-cgi/access/logout`.
Under `aws-alb` it expires the `AWSELBAuthSessionCookie-*` cookies and redirects to
`TICO_COGNITO_LOGOUT_URL` when set (its `logout_uri` should point back at the app), else to `/`.

## Local sign-in

A loopback server has no identity proxy in front of it, so the owner signs in with a token.

- The token is 32 url-safe random bytes in `local-owner.token`, mode 0600. The server reads the
  file on every request, and answers `500` with "chmod 600" if the mode ever loosens.
- `GET /api/v2/local-signin?token=...&next=/` sets `tico_local_session_<hash>` (the hash is this install's, so two installs on one
  browser never share a cookie; the bare `tico_local_session` from earlier versions is still read), HttpOnly, SameSite=Lax
  (a Strict cookie is dropped on the redirect when the link is opened from a terminal or another page), 30 days,
  path `/`, no Domain, and Secure only when the public URL is https. `next` must be a same-origin path.
  The same token also works as a `Bearer` credential for scripts.
- `scripts/tico -e <slug> open` builds that link; the Mac app reads the token file itself, on every
  launch, from the path in its `Info.plist`.
- The owner email must be on the human roster, or sign-in is refused with a roster error.
- **Loopback only.** The server refuses to start when a token file is configured and
  `TICO_PUBLIC_URL` is not `127.0.0.1`, `localhost` or `::1`. Do not put a local environment behind
  a tunnel and keep the token.

Rotating it takes effect on the next request, and invalidates every browser session:

```bash
ENV=~/.config/tico/environments/acme
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$ENV/local-owner.token"
chmod 600 "$ENV/local-owner.token"
scripts/tico -e acme open
```

## Trusted and isolated mode

**Trusted mode** is the default: every environment under your own macOS account, separated by
directory, launchd label, database and profile. It stops collisions and keeps subscriptions apart.
It is not a security boundary: a bot runs as you and can read anything you can read.

**Isolated mode** is one dedicated macOS user per environment, which is the right answer for an
unrelated team on shared hardware. macOS keys provider logins, browser sessions and desktop
tools to the user account, so a separate user gives each of them a clean home with no
per-tool workaround. This is documented, not automated: there is no installer that creates the
user, and each model CLI's first unattended login under a fresh account should be checked by hand.

1. Create a standard (not administrator) account, for example `acme`, in **System Settings → Users
   & Groups**, or `sudo sysadminctl -addUser acme -fullName "Acme runner"`.
2. Log in as that user at least once. A launchd *user agent* needs a login session, so keep the
   session open with fast user switching, or enable automatic login for that account on a computer
   that reboots unattended.
3. Give that user the code. Either clone this repository into its home, or share one read-only
   checkout (`chmod -R a+rX <checkout>`) and let the user keep its own venv:
   `python3.15 -m venv ~/tico-venv && ~/tico-venv/bin/python -m pip install -r
   <checkout>/backend/requirements.txt` (any Python from 3.12 works; 3.15 is what the images run), then export `TICO_RUNNER_PYTHON=~/tico-venv/bin/python`.
   A shared checkout cannot be updated by `scripts/tico update` from the unprivileged account.
4. As that user, run the same commands as the quick start: `env create`, `server install`,
   `enroll`, `profile add` and `profile login`, `install bot`, `doctor`.
5. Sign in to the model CLIs as that user. Their state follows `HOME`, so nothing is shared.
6. Build that environment's Mac app while logged in as that user, so the app's data store, login
   state and notifications belong to it.

The two accounts must not share a workspace, a profile directory or a token file. Check with
`ls -ld` that nothing in one environment is group or world readable.

## Backups

For a local server, the durable state is `hub.sqlite` (with its `-wal` and `-shm` siblings) and
`blobs/`, plus `environment.json`, `server.env`, `runner.json` and `profiles/` to rebuild the
computer, and the workspace, which holds the bot repositories and their history.

Stop the server and copy, which is the simplest correct thing:

```bash
scripts/tico -e acme server stop
cp -a ~/.config/tico/environments/acme /Volumes/Backups/acme-$(date +%F)
scripts/tico -e acme server start
```

Or take a consistent copy of a running database with SQLite's own backup:

```bash
sqlite3 ~/.config/tico/environments/acme/hub.sqlite ".backup '/Volumes/Backups/hub.sqlite'"
```

`python -m backend.manage snapshot <source> <destination>` writes a verified copy, and
`restore <source> <destination>` puts one back. Keep `local-owner.token` out of any shared backup
target, or rotate it after a restore. To open a restored copy without it touching anything outside the computer (no
scheduler, directory sync, release check or usage count, and no calls to GitHub, Slack or Tico HQ), add
`TICO_REHEARSAL=1` to that environment's `server.env` first; the app then says "Rehearsal: nothing runs or leaves this server".

## A dev server on a copy of real data

To see slow lists, odd data and long names before a release, run a dev server on a pruned copy of a real install:

```sh
python -m backend.devcopy --from /path/to/hub.sqlite --to runtime/devcopy/hub.sqlite --days 30
```

`--from` is a database file (a live one is only read, never written) or a backup bundle,
`s3://<bucket>/backups/<name>.sqlite` (`--profile`, `--region`); only the bundle's database is fetched. For a
Litestream backup, `litestream restore -o <file>` first and pass that file. The copy:

- has every table that could act emptied: queued work, routines, watchers, enrolments and computer assignments,
  bot sessions, human tokens, sign-in sessions, OAuth and service keys, credentials, mail, Slack, GitHub App,
  Granola and meeting-bot connections, calendar actions, usage alerts, stored request replies (`EMPTY` in
  `backend/devcopy.py`). Every table is listed as `KEEP`, `HISTORY` or `EMPTY`; one in none of them (added later)
  is emptied and named in the output;
- keeps tasks, docs, people and bots, the last `--days` of messages and events (0 keeps all), and the finished runs
  in that window with their steps, so run details and replay work. Unfinished jobs and runs are removed;
- has no way in: bot tokens cleared, external agents and computers revoked, run tokens and leases ended;
- never has the key files beside it (`credential.key`, `github-app.key`, `slack.key`), so nothing in it can be decrypted;
- is mode 0600, with `server.env`, a fresh `local-owner.token` and empty `registry/` and `blobs/` beside it. Attachments
  are not copied.

`server.env` sets `TICO_REHEARSAL=1` and a loopback URL, so nothing runs on a timer, nothing leaves the server, and
runners are refused ([install.md](install.md#rehearse-a-migration)). The command prints how to start it and sign in.
It refuses a destination that is the source, in use, already there (without `--force`), beside key files, or inside
a git checkout that would track it: copies stay in gitignored folders such as `runtime/` and are never test fixtures.

## Removing an environment

```bash
scripts/tico -e acme uninstall            # the bot, tool and Close jobs
scripts/tico -e acme server uninstall     # the local server job; the database and blobs stay
scripts/tico env remove acme              # refuses while data is present
scripts/tico env remove acme --delete-data
```

`remove` refuses, and names what it found, while `hub.sqlite`, `blobs/`, `profiles/`, `runner.json`
or any `state-*` directory exists. Repeat with `--delete-data` once the services are stopped and a
backup is taken. Logs are not on that list. **The workspace is never deleted either way**, so the
bot repositories survive. Neither form revokes the runner on the server. In **Settings → Computers**,
choose **Remove computer** to revoke its registration (`POST /api/v2/computers/<runner_id>/revoke`).
Its bots remain visible; reassign them to another computer in **Settings → Bots**. Removal does not
stop the local runner or delete its files; follow the stop command shown on the Computers page.

## Troubleshooting

**"`runner.json` was not enrolled for `<slug>`"** is the environment mismatch guard: the `environment`
in the registration is not the `id` in `environment.json`. It means a directory was copied or
restored across environments. Restore the right registration, or enroll this environment again.
**"runner config must have mode 600"** is the same file with loose permissions: `chmod 600`.

**Port already in use.** `env create` takes the first loopback port from 8770 that nothing holds
and no other environment has claimed, but something else can take it later. The api job then fails
to start: read `scripts/tico -e <slug> server logs`, change `port` and `url` in `environment.json`,
re-run `server install` (the plist carries the port), and rebuild the Mac app if it was built
against the old URL.

**`doctor` says NOT READY.** The problem strings are literal: *Missing bot repository or AGENT.md*
(no `bot-<slug>/AGENT.md` in the workspace; the assistant and `botops` are the exception, since the
runner materializes those two from the templates on the computer they are assigned to),
*Runtime executable is not on PATH*, *Antigravity CLI
is not on PATH*, *`<profile>`: ...* (that subscription is signed out or its check failed),
*Unsupported runtime*, and *Configuration differs from server* (`bot.yaml`'s `name`, `runtime`
or `model` disagrees with Settings; the simplest repository omits runtime and model entirely).

**"server: not answering at .../healthz"** means the api job is not running or is crash-looping:
`scripts/tico -e <slug> server status`, then `server logs`. **"cloud: unreachable or registration
rejected"** is the runner's view: the `url` in `runner.json` is wrong or unreachable, or the runner
was revoked in Settings. A **stale heartbeat** over 60 seconds shows the computer as offline in the
interface. If `status` reports the runner started from a different revision than the checkout, the
code changed under a running job: `scripts/tico -e <slug> restart`.
