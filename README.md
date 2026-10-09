# Tico

Tico is a source-available operating system for a team of humans and bots, licensed under
[PolyForm Perimeter 1.0.1](LICENSE). Humans file
work, answer bots and approve actions in a web app; bots pick the work up, run it with
the team's own model subscriptions, and report back. It is built for teams from a handful of bots up.
A small pilot is what has been measured ([sizing](docs/sizing.md)).

- **A small server holds the team.** One Docker install (the server, HTTPS through Caddy or a
  Cloudflare Tunnel, Litestream backups, an optional updater) keeps tasks, chats, approvals,
  routines, files and humans in SQLite. The server never runs a bot or a model CLI; when decisions (or Slack routing) are on, it sends the text of each
  question to the decision provider you configured (TypeSafe, or OpenAI, Anthropic, Gemini, xAI or OpenRouter with a key stored on the server).
- **Computers run the bots.** A Mac (the native runner) or any Linux or cloud computer (the
  `tico-runner` image) joins with a one-time code, claims work over HTTPS, and runs each bot's run
  in that bot's own git repository. Model sign-ins stay on the computer; granted bot credentials can also come from Tico's encrypted vault.
- **Harnesses are installed per computer, on demand.** Codex, Claude Code, Gemini CLI, Grok, or a
  multi-provider catch-all; each bot picks one. Humans sign in with built-in Google or Microsoft
  sign-in, and GitHub access comes from a per-team GitHub App scoped to each bot's own repository.

## Quick start

On your own computer (a Mac with Docker Desktop, or Linux), with no domain and no sign-in setup, run the installer with `--local`:

```bash
curl -fsSL https://github.com/ticoteam/tico/releases/latest/download/install.sh | sh -s -- --local --owner-email you@example.com
```

Docker Desktop must be open and running on a Mac. Tico runs at `http://127.0.0.1:8765`; open the sign-in link the
installer prints. Then get one bot working:

1. In **Finish setup**, choose an AI provider, name your team and choose **Content Marketer** (`content`) in the **Marketing** group.
   It needs only Tico. Assistant, BotOps, Librarian and Goal Manager are the four built-in bots.
2. Use **Add computer** to join this computer, run the command shown, then **Create my team**.
3. Open **Settings > Computers** and **Sign in** beside Codex or Claude Code; finish the provider's browser flow.
   Other harnesses and API credentials are covered in [Harnesses](docs/harnesses.md). Choose the bot's model in **Settings > Bots**.
4. Open Content Marketer and press **Set up**. Use the [sample answers](docs/install.md#sample-answers-for-content-marketer)
   to give it readers, publishing plans, topics, voice examples and privacy rules; review its first draft in chat.
5. On **Tasks > New task**, assign it: “Draft a 200-word blog post introducing our example app to project teams from these facts:
   our example app keeps tasks and meeting notes together; readers can try it locally; use a plain, friendly voice and invent
   no claims. Return a draft on this task.” Read its result on the task, then close it.

You can choose providers later in **Settings > AI providers**; bots wait with “Add an AI provider” until you do.
If the bot waits or sign-in fails, open **Settings > Health** and follow [first-result recovery](docs/install.md#first-result-recovery).
Add a domain and sign-in later ([Install](docs/install.md#add-a-domain-and-sign-in-later)).

For a team, on a Linux server (about 2 GB, with a domain pointed at it), run the installer with no flags:

```bash
curl -fsSL https://github.com/ticoteam/tico/releases/latest/download/install.sh | sh
```

It installs Docker if needed, downloads that release's compose bundle (checksum verified) and walks you through `tico setup`:
a domain name to a signed-in Tico, with each step checked. Then open the app, complete **Finish setup**, and use
**Settings > Computers > Add computer** to join a Mac or a Linux computer. What you need first, where to get a server, and what
you will see are in [docs/install.md](docs/install.md).

Just looking? `docker run --rm -p 127.0.0.1:8765:8765 ghcr.io/ticoteam/tico:latest demo` opens a fictional
team on localhost with no setup ([docs/demo.md](docs/demo.md)).

![Tico's Updates page in the demo](docs/images/updates-desktop-light.png)

## Documentation

[Start here](docs/README.md) · [Use Tico](docs/using-tico.md) · [Developer and computer operator](docs/README.md#developer-or-computer-operator) · [Glossary](docs/glossary.md)

| Read | For |
|---|---|
| [docs/glossary.md](docs/glossary.md) | The words Tico uses, and what each replaces |
| [docs/install.md](docs/install.md) | Installing the server, adding computers, updates and backups |
| [docs/architecture.md](docs/architecture.md) | What runs where, how a bot run flows, why the server runs no bots or model CLIs |
| [docs/sizing.md](docs/sizing.md) | Server and computer sizes; what was measured and what is a guess |
| [SECURITY.md](SECURITY.md) | Reporting a vulnerability and the threat model |
| [docs/harnesses.md](docs/harnesses.md) | Installing and choosing model harnesses per computer |
| [docs/github-app.md](docs/github-app.md) | The per-team GitHub App and token scoping |
| [docs/people.md](docs/people.md) | Humans, roles and sign-in roster |
| [docs/permissions.md](docs/permissions.md) | Who can see, read and write to each bot |
| [docs/databases.md](docs/databases.md) | Letting bots read the team's own databases (PostgreSQL, MySQL, SQLite) read-only, and keeping your config private |
| [docs/meetings.md](docs/meetings.md) | Meetings and call transcripts |
| [docs/creating-bots.md](docs/creating-bots.md), [docs/onboarding.md](docs/onboarding.md) | Creating bots and finishing setup |
| [docs/connect-tools.md](docs/connect-tools.md) | Connect common tools (Jira, Confluence, Linear, PostHog, Sentry, GitHub): a vendor's MCP server if it takes an API token, else a REST skill |
| [docs/starter-bots.md](docs/starter-bots.md) | The 93 bot templates by group, their card fields and what a good bot looks like |
| [docs/onboarding-guide.md](docs/onboarding-guide.md) | Picking your first bots, writing good instructions, approval gates, and reviewing a bot's first week |
| [docs/environments.md](docs/environments.md) | Sign-in options, environments, profiles, removal |
| [docs/files.md](docs/files.md) | What bots publish, versions, who can see a file |
| [docs/docs.md](docs/docs.md) | Docs: internal docs with history and locks, linked docs, import, search |
| [docs/updates.md](docs/updates.md) | Updating the server and its computers |
| [docs/assistant.md](docs/assistant.md) | The built-in Assistant: what it does at once and what it proposes |
| [docs/librarian.md](docs/librarian.md) | The built-in Librarian: answers from the team's docs, with citations |
| [docs/goals-and-kpis.md](docs/goals-and-kpis.md) | Goals, KPIs and the built-in Goal Manager: automatic colours, a human's override, readings with evidence |
| [docs/connect-an-agent.md](docs/connect-an-agent.md) | Connecting an external agent (Grok, Muse, Claude, Cursor, Codex, any MCP client) with a personal token |
| [PRIVACY.md](PRIVACY.md), [docs/telemetry.md](docs/telemetry.md) | The anonymous usage count: exactly what is sent, and how to turn it off |

## Hosting modes

| Mode | Where the server runs | Status |
|---|---|---|
| Docker | Any Linux server, HTTPS by Caddy or Cloudflare Tunnel, built-in Google or Microsoft sign-in | the one-line installer; [docs/install.md](docs/install.md) |
| Local only | The same Mac as the runner, bound to `127.0.0.1` | For trying Tico out; `scripts/tico env create --local` (below) |

The server runs no bots or model CLIs and may call your configured Decision provider, and a runner never holds team-wide authority: it works one
leased run at a time. A bot is one git repository plus one row on the server. One environment
is one team; several can run side by side on one Mac.

## Local trial from a Mac checkout

For the shortest trial, use Docker above. For a native development setup, follow [Environments](docs/environments.md)
and [register a Mac computer](docs/install.md#mac). Open the app, complete **Finish setup**, join the
computer and sign in to your model provider. All four built-in repositories are created automatically. Starters are parked
in **Needs setup** until you open one and press **Set up**; review its first draft, then assign a small task and read its result.
Bots can allow [branches](docs/creating-bots.md#branches): personal tasks and chats on your computer, following the original's instructions and repository. Independent copies keep using `hub bot copy`.

See [Setup reference](docs/onboarding.md) for the wizard's saved fields and [Creating bots](docs/creating-bots.md) for custom bots.

## Running two teams on one Mac

`-e <slug>` (or `TICO_ENV`) selects the environment, and everything derived from it stays apart:
the launchd labels and services, the log files, the runner registration and its state directory,
the subscription profiles and their provider sign-ins, the workspace and its `secrets/`, the database
and blob directory of a local server, the seed registry, and the Mac app (its own bundle
identifier, so macOS keeps Dock identity, login state, preferences and notifications apart).

Shared: the git checkout of this repository, the Python environment under `runtime/`, and, in
trusted mode, the macOS user account. Trusted mode prevents collisions and keeps subscriptions
apart, but a bot still runs as you and can read what you can read; it is not a security boundary.
For an unrelated team on the same hardware use isolated mode, one dedicated macOS user per
environment, written out in [`docs/environments.md`](docs/environments.md) along with the full
reference for the environment directory, profiles, backups and removal.

## The desktop app

One Rust crate (`app/`, Tauri 2) builds every environment's app for macOS, Windows and Linux: the
name, icon, identifier and server URL come in at build time, so there is no runtime rename. The
page itself is Tico's, loaded from the server, so the site changes reach the app with no update;
the shell updates itself from Tico's published builds when it changes (`backend/downloads.py`,
`.github/workflows/app.yml`).

```bash
scripts/app.sh build --env acme      # compile into ~/Applications/<App name>.app
scripts/app.sh install --env acme    # build, replace the running copy, open it
scripts/app.sh check                 # compile only, safe while the installed app is running
```

Per environment the build changes the app name and window title, the icon (`icon.png` in the
environment directory, if you put one there), the server URL, and the bundle identifier, which is
derived from the permanent environment id and never typed by hand. For a local server it also
records the path to the owner token file, which the app reads on every launch and trades for a
session cookie, so rotating the token needs no rebuild and the token never enters the bundle.
`scripts/app.sh release` builds the signed macOS bundles and publishes them to Tico; CI does the
same for all three platforms whenever `app/` changes on `main`. Anyone can build from source, in
which case the bundle is ad-hoc signed and Gatekeeper asks for **Open Anyway** the first time.

## Creating bots

A bot is one durable git repository in the environment's workspace plus one row on the server. The
repository holds `AGENT.md`, playbooks, knowledge and memory; the server holds runtime, model,
effort, assignment, tasks and approvals. The runner looks for the checkout at
`<workspace>/bot-<slug>` (an older `emp-<slug>` folder keeps working) unless the registration's `repos` map says otherwise.

A new bot starts from a template, `templates/catalog/<template>/`. Use **Settings > Bots > Add from template**, or ask
BotOps to build a bot from your brief. A starter is created parked in **Needs setup**; its computer materializes the repository,
and **Set up** starts its first conversation. For other templates, BotOps builds the repository and reports when it is ready.
Read [Creating bots](docs/creating-bots.md) for Instructions, Tools, Routines and the first week. One rule belongs here: **the bot's repository
link is stored on the bot in Settings**, not in a configuration file. It may be a bare name, an
`owner/name` pair, or an https URL; a bare name is completed by the environment's GitHub owner.
GitHub is optional, and a plain repository in the workspace is enough to run. To give each bot
scoped access to its own repository without sharing a personal token, the owner connects a GitHub App
in Settings: [`docs/github-app.md`](docs/github-app.md).

## Skills in your own Claude Code

The dev bots' and humans' own Claude Code sessions use the same team skill, from this repo:
`skills/create-pr` (open a draft PR the way the team does). This repo is also a Claude Code plugin
marketplace (`.claude-plugin/marketplace.json`) with one plugin, `tico`, that carries it. Install
it once per computer:

    claude plugin marketplace add ticoteam/tico
    claude plugin install tico@tico

Then `/tico:create-pr` works in any session, and Claude also reaches for it on its own. Bots keep
reading the same file from `$HUB_DIR/skills/`, so there is one copy to change.

## Hosted and self-hosted servers

The server is configured entirely by environment variables, read once in `backend/config.py`. A
named team must also carry a permanent id, or the process refuses to start.

| Variable | Meaning | Example |
|---|---|---|
| `TICO_DB` | SQLite database path. Required; there is no default | `/var/lib/tico/hub.sqlite` |
| `TICO_ENVIRONMENT_ID` | Permanent opaque id for this team. Required whenever `TICO_TEAM_NAME` or its alias is set | `9f3c1ab27d0e4a51` |
| `TICO_TEAM_NAME` | Team name in the interface; takes precedence over `TICO_COMPANY_NAME` | `Acme` |
| `TICO_COMPANY_NAME` | Compatibility alias for the team name | `Acme` |
| `TICO_APP_NAME` | App, window and notification name | `Atlas` |
| `TICO_ASSISTANT_NAME` | The Assistant's name (default `Assistant`) | `Morgan` |
| `TICO_ASSISTANT_BOT` | Slug of that assistant in the roster | `coo` |
| `TICO_OWNER_EMAIL` | Who owns the environment on first boot. Falls back to `owner:` in `hub-access.yaml`; after that the owner is stored and changed in Settings > Humans ([docs/people.md](docs/people.md)) | `you@example.com` |
| `TICO_PUBLIC_URL` | Where browsers reach this server | `https://atlas.example.com` |
| `TICO_RUNNER_URL` | Where runners enroll, when it differs. A runner on the same computer may use `http://127.0.0.1:<port>` | `https://runner.example.com` |
| `TICO_REGISTRY_DIR` | Seed roster and access list directory; its `integrations/` folder layers the team's own tool pages and queries over the release ([docs/databases.md](docs/databases.md)) | `/etc/tico/registry` |
| `TICO_INTEGRATIONS_DIR` | Where that team layer lives when it is not `<registry>/integrations` | `/etc/tico/integrations` |
| `TICO_BLOB_DIR` or `TICO_BLOB_BUCKET` | Where attachments and meeting files are stored: a directory or an S3 bucket | `/var/lib/tico/blobs` |
| `TICO_ACCESS_ISSUER` and `TICO_ACCESS_AUDIENCE` | Identity proxy issuer and application audience. JWKS is read from `<issuer>/cdn-cgi/access/certs` | `https://acme.cloudflareaccess.com` |
| `TICO_AUTH_PROXY` | `oidc` (built-in sign-in), `cloudflare` or `aws-alb`. Defaults to `cloudflare` when `TICO_ACCESS_ISSUER` is set, else none (loopback sign-in). See [Sign-in options](docs/environments.md#sign-in-options) | `oidc` |
| `TICO_OIDC_ISSUER` | For `oidc`: `https://accounts.google.com`, `https://login.microsoftonline.com/<tenant-id>/v2.0`, or any issuer with OpenID discovery | `https://accounts.google.com` |
| `TICO_OIDC_CLIENT_ID` | For `oidc`: the OAuth client id | `1234-abc.apps.googleusercontent.com` |
| `TICO_OIDC_CLIENT_SECRET` or `TICO_OIDC_CLIENT_SECRET_FILE` | For `oidc`: the client secret, or a file holding it | `/etc/tico/oidc-secret` |
| `TICO_OIDC_ALLOWED_DOMAINS` | For `oidc`, optional: comma-separated email domains that may sign in (Google also checks the `hd` claim) | `acme.com` |
| `TICO_SESSION_IDLE_SECONDS` and `TICO_SESSION_ABSOLUTE_SECONDS` | For `oidc`, optional: how long a sign-in lasts without use (default 30 days) and in all (default 90 days). Lower them to sign humans out sooner | `28800` |
| `TICO_SESSION_SECRET` | For `oidc`, optional: at least 32 characters, signs the sign-in round trip. Generated and kept in `tico-session-secret` beside the database (mode 0600) when unset | |
| `TICO_ALB_ARN` and `TICO_ALB_REGION` | For `aws-alb`: the load balancer's ARN (must equal the token's `signer`) and its region | `arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/tico/50dc6c495c0c9188` |
| `TICO_ALB_KEYS_URL` | For `aws-alb`: base URL for the public keys, replacing `https://public-keys.auth.elb.<region>.amazonaws.com`. Meant for tests | `http://127.0.0.1:9000/keys` |
| `TICO_COGNITO_LOGOUT_URL` | For `aws-alb`: where `/api/v2/logout` sends the browser after clearing the ALB session | `https://acme.auth.us-west-2.amazoncognito.com/logout?client_id=...&logout_uri=...` |
| `TICO_LOCAL_OWNER_TOKEN_FILE` | Loopback owner sign-in. Refused unless `TICO_PUBLIC_URL` is loopback | `.../environments/acme/local-owner.token` |
| `TICO_GITHUB_OWNER` | Organization that completes bare bot repository names | `acme-inc` |
| `TICO_CREDENTIAL_ADMINS` | Comma-separated emails allowed to write shared credentials. The owner and the Admins when empty | `you@example.com` |
| `TICO_UNSOLICITED_PER_DAY` | How many messages a bot may start to one human in a day before it must file a task instead. Default 10; lower it to tighten | `3` |
| `TICO_ESCAPE_QUARANTINE_AT` | How many times in a day a bot may try to reach another bot's files or a `secrets/` path before it is quarantined until a human clears it. Default 3 | `1` |
| `TICO_BLOCK_EXTERNAL_INVITES` | Set to `1` to limit bots to inviting humans on the team roster to calendar events (default: any address). Set it on computers too for `mail calendar add` | `1` |
| `TICO_PROCESSING_OPERATORS` | Humans whose computers may run the Close transcript importer and tool publishers | `dana` |
| `TICO_SCHEDULER` | `1` runs the routine scheduler in this process | `1` |
| `TICO_CREDENTIAL_KMS_KEY` | Optional AWS KMS key for the credential vault. Unset (the default), the vault works with a key in `/data/credential.key`: back that file up with the database ([docs/credential-vault.md](docs/credential-vault.md)) | `alias/tico-acme` |
| `TICO_EXPECTED_AWS_ACCOUNT` and `TICO_EXPECTED_AWS_ROLE` | Optional: the Docker server, Slack gateway and `restore` refuse to start unless STS GetCallerIdentity reports this 12-digit account and, when set, this role (a name or role ARN). Unset, nothing is checked. See [docs/install.md](docs/install.md#check-the-aws-identity) | `123456789012`, `tico-server` |
| `TICO_TYPESAFE_SECRET_ARN` or `TYPESAFE_API_KEY` | Optional key for the decisions provider (TypeSafe's Jev) behind `hub_decision_ask` / `POST /api/v2/decisions` and the Slack gateway (`skills/decisions/SKILL.md`, `questions/README.md`), which is also what checks a message bot's mail for spam and injection. Without it the server asks the team's own model provider, using `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` or `OPENROUTER_API_KEY` from `.env` (`compose.yaml` hands these to the server); with none of them the route answers 503. The `judge.call` audit events and `TYPESAFE_*` names are unchanged | |
| `TICO_UPDATE_CHECK`, `TICO_RELEASES_URL`, `TICO_VERSION`, `TICO_UPDATER_URL`, `TICO_UPDATER_TOKEN` | The "New version" notice and owner-only "Update now"; `TICO_UPDATE_CHECK=off` disables it. See [docs/releasing.md](docs/releasing.md) | |
| `TICO_TELEMETRY`, `DO_NOT_TRACK`, `TICO_TELEMETRY_DEBUG`, `TICO_HQ_URL` | The anonymous usage count: `TICO_TELEMETRY=off` or `DO_NOT_TRACK=1` (or Settings > Privacy) turns it off, `TICO_TELEMETRY_DEBUG=1` prints what would be sent and sends nothing. See [PRIVACY.md](PRIVACY.md) | |
| `TICO_RELEASE`, `TICO_OBSERVABILITY_*`, `TICO_POSTHOG_*`, `TICO_SENTRY_*` | Optional release id and telemetry. Empty disables all of it | |

Sign-in for a hosted server is either built in (`TICO_AUTH_PROXY=oidc`: Google, Microsoft Entra ID
or any OpenID Connect issuer, so a server, a DNS record and one OAuth client are enough) or an
identity-aware proxy in front of the app (Cloudflare Access, or an AWS ALB with Cognito) that passes
a signed JWT. Either way the server verifies the credential, then matches the email against the
humans roster; an account that is not on the roster is refused, so the sign-in policy and
`hub-access.yaml` have to agree. See [Sign-in options](docs/environments.md#sign-in-options).

Docker is the only way to install and run the server. A team with its own AWS load balancer and Cognito can put it in
front of the Docker server ([Sign-in options](docs/environments.md#sign-in-options)). `.env.example` is the shape of the
server's variable file; real hostnames, buckets, ARNs and humans live outside this repository.

## Repository map

| Path | What is in it |
|---|---|
| `backend/` | The server: API, authorization, write layer, scheduler, backups, settings admin (FastAPI, SQLite) |
| `runner/` | The local runner: enrollment, readiness, leases, runs, subscription profiles, the tool and Close transcript workers, and one host per model CLI in `runner/hosts/` |
| `clients/` | What bots and owners call: the `hub` CLI, the HTTP client, preflight, routine validation, team documents, and `environments.py` |
| `ui/`, `app/` | The web interface and its browser tests; the Tauri desktop shell (Rust) |
| `setup/` | `tico setup`, the guided Docker install of the server and of Linux runners |
| `infra/` | cloud-init files for a new server or runner (they call `scripts/install.sh`) |
| `connectors/`, `integrations/`, `skills/` | Shared adapters bots use instead of vendor APIs, one page per outside system, and shared runtime skills |
| `policies/` | Rules every bot follows: approvals, access, handoffs, writing |
| `templates/` | `catalog/` is the bot templates setup picks from, one folder per template with its card and its starting repository; `employee-repo/` is the generic starting point, `environment-registry/` seeds a new environment with `coo` and `botops` |
| `scripts/` | `tico`, `hub`, `setup-runner.sh`, `app.sh`, `publish-employee.sh` and maintenance commands |
| `docs/` | Documentation: [how it works](docs/how-it-works.md), [using Tico](docs/using-tico.md), [finish setup](docs/onboarding.md), [creating bots](docs/creating-bots.md), [files](docs/files.md), [environments](docs/environments.md), [Hermes agents](docs/hermes-agents.md) and [OpenClaw agents](docs/openclaw-agents.md) (connect a profile in 2 minutes, with a scheduled sync); `history/` holds retired designs |

### Where a change belongs

If only one bot should change, change that bot's repository; if every bot or the system should
change, change this repository.

| Change | Location |
|---|---|
| One bot's instructions | `bot-<slug>/AGENT.md` |
| One bot's repeatable method | `bot-<slug>/playbooks/` |
| Domain facts, learnings and decisions for one bot | `bot-<slug>/knowledge/`, `memory/` |
| One bot's routines, tools or send switch | `bot-<slug>/bot.yaml` |
| Roster, hierarchy, status, model, effort, assigned computer, repository link | **Settings** in the app. The registry directory seeds a new database only |
| Rules every bot must follow | `policies/` |
| The server, runner, tools, interface, or the starting point for future bots | This repository's code |

## Operating commands

`scripts/tico` is the one command on a Mac that runs bots (on Linux it does the same with systemd user units, [install.md](docs/install.md#a-linux-checkout-systemd)). `scripts/tico help` prints the full usage; the groups are:

```
scripts/tico [-e ENV] install|uninstall|restart|status|doctor|logs|update|open
scripts/tico env create|list|show|remove ...         the teams on this Mac
scripts/tico -e ENV enroll --code-file F --label L   register this Mac for one team
scripts/tico -e ENV profile add|list|login|assign    the subscriptions its bots run on
scripts/tico -e ENV bot create SLUG --name "Display" [--template T]
scripts/tico -e ENV server install|uninstall|start|stop|restart|status|logs
```

`-e ENV` (or `TICO_ENV`) picks a team environment.

`scripts/setup-runner.sh [--env <slug>] <enrollment.json> [workspace]` does the venv, the
enrollment, the bot service and `doctor` in one go from a file downloaded by **Add computer**, and
`scripts/publish-employee.sh --owner <org> --workspace <dir> <slug>...` turns bot folders into
private GitHub repositories.

## Developer notes

The suite is small on purpose and runs locally; GitHub Actions does not run tests. Write tests while you build if they help, then keep only the few that
guard what matters most: security and privacy boundaries, data safety (migrations, backup, restore),
the updater and release path, and core contracts (job claim and lease, task writes, chat, the API
schema), plus one happy path per major feature. The default Python suite runs in about a minute on a laptop;
tests that need real git, Docker or a server are marked `@pytest.mark.slow` and run with `-m slow` (and at release).

```bash
pip install -r backend/requirements-dev.txt
python -m pytest -q          # parallel by default (pytest-xdist); add -n 0 to debug serially
```

Browser checks need `npm ci` and a Playwright browser (`npx playwright install chromium`), then
`npm run test:ui` (the core scripts in `ui/tests/`, three at a time; `--all` runs every script, `node scripts/ui-tests.cjs <name>` runs one). The UI has no build step and no runtime network
dependency: `marked` and the icon font are vendored in `ui/vendor/` with their licenses.

The live checkout on an owner's Mac is what the runner executes: the launchd jobs run
`python -m runner` with that directory as the working directory, so an edit in the tree is live for
the next run, and a pull without a restart leaves the old code running (`scripts/tico status` says
so explicitly). Do development in a git worktree, never in the live checkout, and use
`scripts/tico update`, which pulls and restarts after waiting for runs in flight.

## License

Tico is available under the [PolyForm Perimeter License 1.0.1](LICENSE). You may use and modify it
for your team's internal use, and redistribute it for purposes permitted by the license. You may
not provide a competing product to others, including a free competing product.

This is a source-available license. It has no automatic change date. Earlier versions and
contributions released under [Apache 2.0](licenses/Apache-2.0.txt) retain those grants; third-party components retain their own
licenses, listed in [NOTICE](NOTICE). See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution terms.

## Contributing

Engineering work is tracked as GitHub issues on [ticoteam/tico](https://github.com/ticoteam/tico/issues);
`skills/tico-tickets/SKILL.md` describes how issues are filed and worked. To contribute: state the
current behavior, the wanted outcome and how it will be checked; make the smallest durable change;
run the tests above; open a pull request against `main`. Bot repositories are read from the
checkouts on the Mac that runs them, so a push to one is not live until that checkout has it.
Never put a credential in git, in a task, or in bot instructions, and keep team names, humans
and accounts out of the repository: examples use the fictional team Acme (`acme.example`).

## Community

- [LICENSE](LICENSE): what you may do with Tico.
- [CONTRIBUTING.md](CONTRIBUTING.md): running the tests, opening a pull request, and the DCO sign-off (`git commit -s`).
- [SECURITY.md](SECURITY.md): report a vulnerability privately, and which versions get fixes.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md): how we treat each other.
- Questions and ideas: [Discussions](https://github.com/ticoteam/tico/discussions). Bugs: [issues](https://github.com/ticoteam/tico/issues/new/choose).

## Privacy

Tico counts active installs anonymously: a random ID, the version and two yes/no activity flags, sent with the update check. Turn it off in Settings > Privacy, with `TICO_TELEMETRY=off` or with `DO_NOT_TRACK=1`. Exactly what is sent, and what is kept: [PRIVACY.md](PRIVACY.md).

## Your own deployment

A team that runs Tico keeps its humans, bots, policies and connected accounts in its own
registry directory (`TICO_REGISTRY_DIR`) and in bot repositories outside this one; this repository
is the product. `templates/environment-registry/` shows the shape of the registry.

Tasks support [tags with metadata, Markdown checklists and templates](docs/using-tico.md#tags-and-release-checklists).
Open **Settings > Tags** or use `hub tag list|show|create|update`. Existing `--label` and
`hub_task_label` calls keep using tag keys.

## Task pipelines

Tasks start on General. Movers can add types and named steps in Settings → Types, then select a
type on the board to use its steps as columns. Bots and existing scripts keep using task statuses.
See [Task types and steps](docs/tasks.md) for the UI, CLI, MCP and API.
