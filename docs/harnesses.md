# Harnesses: the model CLIs a runner uses

A **harness** is the program a runner starts for a bot's run: Codex, Claude Code, Gemini CLI, Grok Build, Cursor Agent, Muse Code, or pi.
Tico does not ship them. Model CLIs release every few days and Tico has to work with many providers, so each runner
installs the harnesses its own bots need, keeps them current, and lets the owner pin one when a release misbehaves.

Where bots run: on a Mac (native runner) or on a Linux computer (the `tico-runner` Docker image). The server runs no bots
and holds no harness.

## Supported harnesses

| Harness | Manifest | Providers | Installed with | Signs in with |
|---|---|---|---|---|
| Codex | `runner/harnesses/codex.toml` | OpenAI | npm `@openai/codex` | ChatGPT subscription (device code, from Settings) or `OPENAI_API_KEY` |
| Claude Code | `claude-code.toml` | Anthropic | npm `@anthropic-ai/claude-code` | Claude subscription (link and pasted code, from Settings), `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` |
| Gemini CLI | `gemini-cli.toml` | Google | npm `@google/gemini-cli` | `GEMINI_API_KEY` |
| Grok Build | `grok.toml` | xAI | npm `@xai-official/grok` (xAI's own package) | Grok subscription (`grok login` in a terminal) or `XAI_API_KEY` |
| Cursor Agent | `cursor-agent.toml` | Cursor | Cursor's install script, run with `HOME` inside the tools directory | Cursor subscription (`cursor-agent login` in a terminal) or `CURSOR_API_KEY` |
| Muse Code | `muse-code.toml` | Meta | Meta's install script (`dev.meta.ai/install.sh`), run with `HOME` inside the tools directory | Meta account (device code, from Settings) or `META_API_KEY` |
| pi (catch-all) | `pi.toml` | DeepSeek, Kimi (Moonshot), Mistral, OpenRouter | npm `@earendil-works/pi-coding-agent` | `OPENROUTER_API_KEY` |

xAI has an official CLI (Grok Build, `docs.x.ai/build`; also an install script at `x.ai/cli/install.sh`), so xAI does
not go through the catch-all. Tico uses the npm package because it installs into a directory of our choosing and npm
checks the tarball's integrity hash; the script installs into `~/.grok`.

`runtime: cursor` was retired in v0.1.0. It is back as an
ordinary harness: `runner/hosts/cursor.py` runs `cursor-agent -p --output-format stream-json --force --trust` once per
run, with the prompt on stdin and the chat made by `cursor-agent create-chat`, so a restart resumes it. Cursor picks
the model itself on `auto` (`cursor-auto` in the model list); a bot's config may name any other model Cursor offers.
Cursor's installer only ever installs the newest release, so this harness follows `latest` and cannot be pinned to an
older version (Pin to this version pins the one installed).

Muse Code is Meta's coding CLI (`muse`). Its runtime is `musecode`, because `muse` already names the Muse app synced
in as an external agent. `runner/hosts/musecode.py` runs `muse exec --json --yolo` once per run with the prompt in a
file (the CLI takes no prompt on stdin) and `--session-id <uuid>`: Tico makes the id, the first run creates the
session and every later run with the same id continues it, so a restart resumes it. The hub and a bot's remote MCP
servers are set for that run only (`-c mcp_servers.<name>=...`), with secrets left as `${VAR}` in the run's
environment. Every run sets `MUSE_NO_AUTO_UPDATE=1` so the launcher does not update itself; the runner updates it.
Muse also loads every MCP server in its own `settings.json` and refuses to switch those off per run, so bot runs
never use the operator's `~/.config/muse`: they run with `XDG_CONFIG_HOME` set to a Muse folder of Tico's own (a
subscription profile's `musecode/`, else the runner's host state), and any servers in it are removed before each
run. A bot run gets only the hub, its bot.yaml servers and the repository's own MCP config.
The download is about 340 MB. The sign-in is `muse/auth.json` in that folder (sign in from Settings; a
profile signs in to its own); `META_API_KEY` wins over it, and a computer-wide key reaches the run. The CLI has no
sign-in status command, so readiness reports the file being there. Version checks and sign-ins also set
`MUSE_NO_AUTO_UPDATE=1` (the manifest's `[env]`).
Meta's install script only installs the newest release, so this harness follows `latest` like Cursor's.
Meta's Llama models stay on pi through OpenRouter (`llama-4-maverick`, no longer recommended).

### Why pi is the catch-all

Providers with no CLI of their own (DeepSeek, Kimi, Llama, Mistral, and whatever comes next) share one harness that
takes an API key. Candidates:

- **pi** (already a Tico host, `runner/hosts/pi.py`): one non-interactive process per run (`pi -p --mode json`), a
  JSON event stream Tico already parses, resumable sessions, read/bash/edit/write/grep tools (the bot reaches Tico
  with the `hub` CLI over bash), no permission prompts, an npm package, and keys for many providers by environment
  variable. Tico reaches every model through OpenRouter, so one key covers all of them.
- **OpenCode**: also capable and widely used, with a large provider list, but it would need a new host adapter and its
  own event format, and ships a per-platform native binary. Nothing in a headless run needs what it adds.
- **aider**: edits a git repository well, but it is a chat-driven editor, not an agent with a shell; a bot's run
  (read the repo, call Tico, run tests, commit) does not fit.

pi is the one that best fits a headless run in a git repository with the least new code. If it stops being
maintained, replacing the catch-all is a manifest and a host adapter, described below.

## How providers map to harnesses

The owner enables providers in **Settings > AI providers** (`backend/providers.py`). Each provider names its runtime
(the host adapter) and its harness (the manifest). Enabling a provider offers its models; it does not install
anything. Each computer installs only:

- the harness of every bot assigned to it, and of each bot's fallback (`fallback.harness`);
- with no bot yet, the harness of the company's default model (`default_runtime` in `GET /api/v2/config`), so the
  first computer can be signed in before its first bot arrives;
- any harness the owner turned on for that computer (see [Choosing what a computer installs](#choosing-what-a-computer-installs)).

A runner whose team has no provider yet installs nothing and still joins and goes online.

A model with no CLI of its own has a row in `MODEL_CATALOG` with `runtime: "pi"` and an entry in
`runner/hosts/pi.py` `MODELS` that gives its OpenRouter id (for example `kimi-k3` is `moonshotai/kimi-k3`). Enabling
the `openrouter` provider offers every such model; enabling one vendor offers that vendor's models only.

## Choosing a harness, model and effort

Bot settings, new bots, fallbacks and bulk model changes use three separate fields:
**Harness / provider**, then **Model**, then **Effort**. Model choices follow the selected harness
and the team's enabled providers; effort choices follow that model. For an existing bot's primary model, choose
all three and press **Apply** to use the existing confirmation and checkpoint transition. Changing
a field alone does not switch the running bot. Cancel restores the saved selection.

The current OpenAI and Claude choices, reviewed 2026-10-03, are:

| Harness | Model | Model ID | Released |
|---|---|---|---|
| Claude Code (Anthropic) | Opus 5.5 | `claude-opus-5-5` | 2026-09-22 |
| Claude Code (Anthropic) | Sonnet 5.5 | `claude-sonnet-5-5` | 2026-09-28 |
| Claude Code (Anthropic) | Fable 5.1 | `claude-fable-5-1` | 2026-09-01 |
| Codex (OpenAI) | Sol 6.1 | `gpt-6.1-sol` | 2026-09-29 |
| Codex (OpenAI) | Luna 6 | `gpt-6-luna` | 2026-09-22 |

Release dates: [Claude release notes](https://platform.claude.com/docs/en/release-notes/overview)
and [OpenAI changelog](https://developers.openai.com/api/docs/changelog).
The offered effort levels are low, medium, high, xhigh and max, supported by the existing runner
adapters and documented for [Claude](https://platform.claude.com/docs/en/build-with-claude/effort),
[Sol 6.1](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and
[Luna 6](https://developers.openai.com/api/docs/models/gpt-6-luna).
Astra, Sol 6 and Opus 5 remain readable for existing configurations but cannot be newly selected.
No existing bot is migrated automatically.

Cursor Agent and Grok Build already have runner adapters and appear when their providers are enabled.
Cursor currently offers Auto; its effort field says **Managed by harness** because the CLI has no
separate effort switch. Before adding named Cursor models, confirm their exact identifiers with
[`cursor-agent --list-models`](https://cursor.com/docs/cli/reference/parameters) on the relevant account.
Muse Code offers Muse Spark 1.3 (`muse-spark-1.3`, effort low to max, medium by default), the only public model
the Muse Code 1.4.7 CLI names (checked 2026-10-14); `muse-spark-1.2` exists but is older.
Grok Build retains its catalog model and supported efforts through the
[existing CLI integration](https://docs.x.ai/build/overview). Selecting a harness does not enable
its provider, grant credentials or guarantee model access on a computer.

### Keeping the model list current

Maintain a curated list per harness, reviewing provider release notes at least monthly and when
preparing a release. Prefer models released within the preceding six calendar months; newer replacements
can retire a choice earlier. Verify the actual release date (not its knowledge cutoff), exact CLI model
ID, effort flags and account availability before adding it to `backend/providers.py`.
Record dated sources here for each catalog refresh. Unknown dates need verification, not a guessed expiry.
Older or withdrawn choices use `deprecated: True`, preserving saved bot configurations and history while
preventing new selections. Catalog retirement is a reviewed change, not a clock-driven migration.
Auto and externally managed model choices have no fixed model release date and are explicit exceptions.
The remaining provider catalogs are preserved by this update; their next refresh must apply the same review.

## Where installs go, and what is left alone

Installs go into the runner's **tools directory**, never system-wide: `TICO_TOOLS_DIR`, else `tools_dir` in the
registration, else `tools/` beside `runner.json`. In the Docker image that is `/home/runner/tools`, inside the runner
volume, so harnesses survive a restart or a new image. The directory is put last on `PATH`.

- **Mac**: a harness already on `PATH` (Homebrew, `npm -g`) is used as it is. The runner installs into its tools
  directory only when the harness is missing, and never updates one it did not install. Installing needs `node` and
  `npm` on the Mac.
- **Linux (Docker)**: the image has node, npm, python, pip, git, gh, build tools, ripgrep, jq and curl, and no model
  CLI. It also has the system libraries Chromium needs, so a browser a bot installs (`npx playwright install
  chromium`) starts. When a Playwright Chromium is on a computer, the runner starts it headless every 15 minutes;
  two failures in a row show in Health as "<computer>: browser does not start".
- **Current Docker runner**: the supervisor `ticorun` (UID 10002) owns the tools directory, registration
  and runner state; bot code runs as `bot` (UID 10003) and cannot replace those files. Bots still share
  their bot user, workspace and model logins. Start with the release's
  [runner Compose file](../docker/runner.compose.yaml), which supplies the required user and capabilities.
- **Native Mac/Linux and legacy containers without isolation**: bots share the runner's user and can
  replace a CLI or reach that user's runner state. See the isolation notes in
  [Install](install.md#add-computers-to-run-your-bots) and [Security](../SECURITY.md).

## Choosing what a computer installs

**Settings > Computers > Model CLIs** lists every harness on each computer with an **On** switch for the owner.
A harness a bot on that computer runs on is always on (the switch is locked). Any other one can be turned on, for
example to sign it in before moving bots there, or off. Turning one off removes the runner's own install (its
`<tools>/<id>` directory and its `bin` link) at a moment no turn uses it; a copy the person installed outside Tico is
never touched. The choice is kept per computer on the server (`runner_harness_choices`,
`POST /api/v2/computers/{id}/harness-choices` with `{harness, enabled}`, audited as `runner.harness.on|off`) and
reaches the runner with its pending actions (`GET /api/v2/runner-harness-actions`, `choices`).

A harness never switched either way is left as it is: an install no bot needs any more stays and is kept current
until the owner turns it off. So a computer that installed every enabled provider's harness before this change keeps
them all working. A runner talking to a server that does not know the setting installs what its bots run on and
keeps what it has.

Moving a bot to a computer, or choosing a model whose harness that computer lacks, is not refused: the computer
installs the harness when the bot is there, and the readiness check says "<harness> is installing on <computer>".
Only an install that failed (no `npm` on a Mac, say) blocks the move, with the reason.

## Updating and pinning

Every day the runner asks each harness it installed (and has not been turned off) for the newest release. With the default policy (`latest`) it
installs the new version **between runs**: the copy is downloaded and checked beside the old one while runs are in progress,
and switched in only at a moment no run in progress uses that harness (a run on another harness does not hold it
back). A failed install keeps the working version and is retried after 30 minutes.

A harness can be **pinned** to a version: the runner keeps that version and only reports that a newer one exists.
The manifest can ship a pin (`[update] policy = "pinned"`, `pin = "1.2.3"`), and the owner can pin or unpin per
computer.

**Settings > Computers > Model CLIs** shows a chip per harness on each computer: name, version, `pinned`, `<version> available`,
`installing`, `updating`. The owner (and only the owner) sees the actions, for installs the runner manages:

- **Update**: install the newest release now (waits for an idle moment; moves the pin if the harness is pinned).
- **Pin to this version** / **Resume updates**.

Each request is an audit event (`runner.harness.update|pin|unpin`, then `runner.harness.done|failed`) and travels the
same way as the browser sign-in: the server keeps the request, the runner polls `GET /api/v2/runner-harness-actions`
and reports back. Nothing listens on the runner. A request the computer does not answer within six hours expires.

The readiness heartbeat carries, per harness: `installed`, `version`, `pinned` (and `pin`), `authenticated`,
`update_available` (and `latest`), `managed` (installed by the runner, not by a human), `wanted` (a bot here runs on it,
or the owner turned it on), `state`. A runner
talking to a server from before this change drops the field instead of going offline.

## Writing a harness

A harness is a manifest plus a host adapter.

1. **Host adapter**: `runner/hosts/<host>.py`, a `Host` subclass (`runner/hosts/base.py`: `start_thread`, `start_turn`,
   `interrupt`, and events through `emit`). Register it in `Runner.make_host` (`runner/service.py`).
2. **Manifest**: `runner/harnesses/<id>.toml`. The file name is the id.

   ```toml
   id = "acme-agent"            # lowercase letters, digits, hyphens
   name = "Acme Agent"
   host = "acme"                # runner/hosts/acme.py
   executable = "acme"          # what the host runs, looked up on PATH
   providers = ["acme"]         # provider ids from backend/providers.py; one harness per provider
   # catch_all = true           # at most one harness; it serves providers with no CLI of their own

   [install]
   method = "npm"               # npm | pip | script | binary
   package = "@acme/agent"      # npm and pip
   # script: url (https), prefix_env (variable that tells the script where to install), bin (path inside it)
   # binary: url with {os} and {arch}, and [install.sha256] "linux-arm64" = "<hex>" per platform (required)

   [version]
   args = ["--version"]
   pattern = '(\d+\.\d+\.\d+)'  # one capture group

   [update]
   policy = "latest"            # latest | pinned (a pinned manifest also needs pin = "1.2.3")

   [auth]
   methods = ["device-code", "subscription", "api-key"]
   api_key_env = ["ACME_API_KEY"]

   [login]                      # only for device-code / subscription sign-in through the browser relay
   command = ["login", "--device-auth"]
   terminal = false             # true when the CLI insists on a terminal
   paste_code = false           # true when the sign-in page hands back a code to paste
   ```

3. **Provider and models**: add the provider (with `runtime` and `harness`) and its models to `backend/providers.py`.
   If the CLI signs in through the browser relay, add its runtime to `RUNTIMES` (and `PASTE_RUNTIMES`) in
   `backend/model_login.py`; a test checks that this list matches the manifests.
4. **Check**: `runner/tests/test_harness_tools.py` validates every manifest (file name, host adapter, unique
   providers, install fields) and that the provider list and manifests agree.

Loading fails loudly on a bad manifest, naming the file and the field.
