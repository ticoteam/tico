# Harnesses: the model CLIs a runner uses

A **harness** is the program a runner starts for a bot's run: Codex, Claude Code, Gemini CLI, Grok Build, Cursor Agent, Muse Code, pi, or Antigravity (`agy`).
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
| Antigravity (Gemini variant) | `gemini-cli.toml` `[variants.antigravity]` | Google | Operator installs `agy`; the runner never installs or updates it | Google account, signed in by the operator in `agy`; readiness checks only that `agy` is on PATH, a real run checks the sign-in; no MCP servers |
| Grok Build | `grok.toml` | xAI | npm `@xai-official/grok` (xAI's own package) | Grok subscription (device code, from Settings) or `XAI_API_KEY` |
| Cursor Agent | `cursor-agent.toml` | Cursor | Cursor's install script, run with `HOME` inside the tools directory | Cursor subscription (browser link, from Settings) or `CURSOR_API_KEY` |
| Muse Code | `muse-code.toml` | Meta | Meta's install script (`dev.meta.ai/install.sh`), run with `HOME` inside the tools directory | Meta account (device code, from Settings) or `META_API_KEY` |
| pi (catch-all) | `pi.toml` | DeepSeek, Kimi (Moonshot), Mistral, OpenRouter | npm `@earendil-works/pi-coding-agent` | `OPENROUTER_API_KEY` |

xAI has an official CLI (Grok Build, `docs.x.ai/build`; also an install script at `x.ai/cli/install.sh`), so xAI does
not go through the catch-all. Tico uses the npm package because it installs into a directory of our choosing and npm
checks the tarball's integrity hash; the script installs into `~/.grok`.

`runtime: cursor` was retired in v0.1.0. It is back as an
ordinary harness: `runner-rs/src/hosts/cursor.rs` runs `cursor-agent -p --output-format stream-json --force --trust` once per
run, with the prompt on stdin and the chat made by `cursor-agent create-chat`, so a restart resumes it. Cursor picks
the model itself on `auto` (`cursor-auto` in the model list); a bot's config may name any other model Cursor offers.
Cursor's installer only ever installs the newest release, so this harness follows `latest` and cannot be pinned to an
older version (Pin to this version pins the one installed).

Grok Build and Cursor Agent sign in from **Settings > Computers**, using **Add connection** for the
computer's login. Both offer **Sign out** and **Switch account**. Grok uses
[`grok login --device-auth`](https://docs.x.ai/build/cli/reference), then reads its scoped `~/.grok/auth.json`
store for sign-in state, including saved API-key logins (an empty store is signed out). A bot granted
`XAI_API_KEY` can run without a subscription store; an inherited operator key cannot sign in a separate default home.
Grok has no status command. Cursor uses
[`cursor-agent login`](https://cursor.com/docs/cli/reference/authentication) with `NO_OPEN_BROWSER=1`, and checks
`cursor-agent status`. Neither flow asks for a code to paste into Tico.

Cursor commands in Tico use `AGENT_CLI_CREDENTIAL_STORE=file`, its supported
[file credential mode](https://cursor.com/docs/release-notes), so the computer's default login home and switches have separate
credentials on macOS too. The CLI stores these in `~/.config/cursor/auth.json` on Linux or `~/.cursor/auth.json`
on macOS. An existing macOS Keychain login needs a fresh sign-in from Tico; its Keychain entry is untouched.
A configured `default_profile` uses its own `HOME` and drops inherited config-path overrides. A computer login using a custom
auth folder can still sign in and out; Switch account refuses that layout before starting, so it cannot overwrite
an account outside the switch's private folder. Sign out and sign in instead, or configure a separate computer login home.

Muse Code is Meta's coding CLI (`muse`). Its runtime is `musecode`, because `muse` already names the Muse app synced
in as an external agent. `runner-rs/src/hosts/musecode.rs` runs `muse exec --json --yolo` once per run with the prompt in a
file (the CLI takes no prompt on stdin) and `--session-id <uuid>`: Tico makes the id, the first run creates the
session and every later run with the same id continues it, so a restart resumes it. The hub and a bot's remote MCP
servers are set for that run only (`-c mcp_servers.<name>=...`), with secrets left as `${VAR}` in the run's
environment. Every run sets `MUSE_NO_AUTO_UPDATE=1` so the launcher does not update itself; the runner updates it.
Muse also loads every MCP server in its own `settings.json` and refuses to switch those off per run, so bot runs
never use the operator's `~/.config/muse`: they run with `XDG_CONFIG_HOME` set to a Muse folder of Tico's own (a
subscription profile's `musecode/`, else the runner's host state), and any servers in it are removed before each
run. A bot run gets only the hub, its bot.yaml servers and the repository's own MCP config. Its sessions (and each session's
lock and log) go to a data folder of Tico's own as well, `XDG_DATA_HOME` beside that config folder (`musecode/data`
in the runner's host state, or `musecode/profiles/<profile>/data` there for a subscription profile, outside every
folder handed to the bot user), never `~/.local/share/muse`: on a computer that runs
bots as their own user, that folder may belong to another user. If the data folder is there but the bot user can't
write it, readiness shows Muse Code as not ready and names the folder: Settings > Computers marks the Muse Code line
and says which folder, without asking for a sign-in, since signing in again does not fix it.
The download is about 340 MB. The sign-in is `muse/auth.json` in that folder (sign in from Settings; a
profile signs in to its own); `META_API_KEY` wins over it, and a computer-wide key reaches the run. The CLI has no
sign-in status command, so readiness checks for its saved Meta credential: an API key or Meta-account token in
that file. Every Muse process Tico starts sets `TBH_CREDENTIAL_BACKEND=file`, so the login stays in that file on a Mac
too; Muse's default there keeps the token in the login Keychain, outside the config files that Switch account moves
into place and Cancel keeps. A saved login that points at the Keychain (from before this setting) shows as not
ready, asking for a sign-in from Tico; Tico never reads or changes that Keychain item. Sign out runs `muse logout`;
Switch account signs in to a temporary config folder and replaces the saved login only after success,
for the computer's login. Cancelling keeps the old login. The runner retains the same protection for
legacy named profiles, which Settings no longer lists.
Version checks and sign-ins also set
`MUSE_NO_AUTO_UPDATE=1` and `TBH_CREDENTIAL_BACKEND=file` (the manifest's `[env]`).
Meta's install script only installs the newest release, so this harness follows `latest` like Cursor's.
Meta's Llama models stay on pi through OpenRouter (`llama-4-maverick`, no longer recommended).

### Why pi is the catch-all

Providers with no CLI of their own (DeepSeek, Kimi, Llama, Mistral, and whatever comes next) share one harness that
takes an API key. Candidates:

- **pi** (already a Tico host, `runner-rs/src/hosts/pi.rs`): a long-lived process driven over JSON on stdin and stdout
  (`pi --mode rpc`), a JSON event stream Tico already parses, resumable sessions, read/bash/edit/write/grep tools, MCP servers (the bot
  reaches Tico through the hub MCP server and the `hub` CLI over bash), no permission prompts, an npm package, and keys
  for many providers by environment variable. Tico reaches every model through OpenRouter, so one key covers all of them.
- **OpenCode**: also capable and widely used, with a large provider list, but it would need a new host adapter and its
  own event format, and ships a per-platform native binary. Nothing in a headless run needs what it adds.
- **aider**: edits a git repository well, but it is a chat-driven editor, not an agent with a shell; a bot's run
  (read the repo, call Tico, run tests, commit) does not fit.

A pi turn gets the same MCP servers as a Claude Code or Codex turn (the hub, the bot's bot.yaml servers and the
repository's `.mcp.json`), in the turn's `mcp.json` under its agent folder (`PI_CODING_AGENT_DIR`: `runtime/pi-agent/<bot>`,
or the turn's own folder for a Tico model). The file is written atomically per turn. pi takes streamable HTTP only, so an
SSE server is named as not passed. Each tool is declared directly (`exposure: direct`). Secrets stay `${VAR}`, and each
stdio server's environment names the Gateway key empty so it never inherits it. Built-in MCP arrived in pi 0.99.0
(2026-09-29); the runner installs the latest pi, but an older pi would ignore `mcp.json` without an error.

**Compact at** applies to a pi turn: the turn's `settings.json` (in the same agent folder) sets pi's
`compaction.reserveTokens` to the model's context window less the bot's Compact at (`reserve_tokens` in `hosts/pi.rs`,
kept between pi's default 16,384 and a compaction point of 100,000). The Tico Gateway's models have a 200,000-token window.
No OpenRouter model's window is known to Tico yet, so those turns keep pi's own compaction. pi's `compaction_end` with a
result is a compaction: the session's next turn is given the whole prompt. pi can compact between one turn's tool calls,
where Tico cannot put back what the turn left out, so that turn keeps what it was given until the next one.
`compact_first` and `/compact` send pi's RPC `compact`, which ends at its `compaction_end`.

One pi process serves a conversation's turns (`persistent_session`). The session id is the thread id, so a process that
starts again picks up the same session file. A turn is a `prompt` and ends at pi's `agent_settled`. After a turn that
completed, the runner keeps the process (`Pool` in `hosts/pi.rs`, as many as the runner's capacity, at least 8). It
stops it after 15 minutes unused, when the runner stops, or when its turn did not complete. pi reads `HUB_TOKEN` from a
token file of its own (`tico-file:`), which holds the running attempt's token and is blanked between attempts. A turn
that would start pi differently starts it again on the same session: a different model, a changed environment or granted
secret (a new GitHub token too), a different agent folder (MCP servers, Compact at). A new effort is pi's
`set_thinking_level`. A pi that exits fails its turn (`process_exit`); the next turn starts it again on the same
session. A message sent while a turn runs is pi's `steer` (`steer`): pi writes it in after the turn's current tool
calls, before its next model call. One steered in after the turn's last model call is sent as a new prompt within the
same turn. One pi had not yet read when the turn ended is taken back out of its queue and waits for the next turn. A
pi run as a leased turn user (a Tico model's turn on a Docker runner) is kept with that user and its Gateway relay, for
the same bot's next turn in the same conversation, which takes them back before it would lease a user of its own. The
relay keeps its URL and token and gets each attempt's key; between turns it refuses every call, and calls still open
when the turn ends are cut off. The token file and granted credential files are readable by that user only, and the
files sit at the same paths every attempt. A turn that finds every turn user busy stops the longest-idle kept pi and
takes its user. A kept pi's relay is stopped and its user cleaned and returned when the pi goes (idle, eviction, a
crash, a failed turn, the runner stopping); a pi started again for a changed start keeps both. A new GitHub token
(the runner and the server hand out a new one about every 15 minutes) still restarts it.

pi is the one that best fits a headless run in a git repository with the least new code. If it stops being
maintained, replacing the catch-all is a manifest and a host adapter, described below.

## How providers map to harnesses

The owner enables providers in **Settings > Models** (`backend/providers.py`). Each provider names its runtime
(the host adapter) and its harness (the manifest). Enabling a provider offers its models; it does not install
anything. Each computer installs only:

- the harness of every bot assigned to it, and of each bot's fallback (`fallback.harness`);
- with no bot yet, the harness of the company's default model (`default_runtime` in `GET /api/v2/config`), so the
  first computer can be signed in before its first bot arrives;
- on a computer that isolates bot processes, the Gateway runtime selected by the company's models choice
  (`gateway_runtime` in the runner's `GET /api/v2/config`), so recommended starter turns can run before
  subscription sign-in. This also applies when automatic quota fallback is off. The owner can turn this
  optional install off; an explicit bot primary or fallback still makes its harness required;
- any harness the owner turned on for that computer (see [Choosing what a computer installs](#choosing-what-a-computer-installs)).

A runner whose team has no provider yet installs nothing and still joins and goes online.

A model with no CLI of its own has a row in `MODEL_CATALOG` with `runtime: "pi"` and an entry in
`runner-rs/src/hosts/pi.rs` `MODELS` that gives its OpenRouter id (for example `kimi-k3` is `moonshotai/kimi-k3`). Enabling
the `openrouter` provider offers every such model; enabling one vendor offers that vendor's models only.

### The Tico AI Gateway

The Gateway's picks (`tico-<department>-<level>[-fast][-<version>]`, for example `tico-sales-medium` or
`tico-engineering-high-fast-1.0`; `backend/gateway_tiers.py`) run on pi through Tico's own Gateway, not OpenRouter. The
catalog lists each tier at its current pinned version; any other id of the lineup (no version, an older version) resolves
on lookup, and pi's `models.json` names the turn's model for the run. `tico-cheap-1.1`, `tico-cheap-1.0`, `tico-mid-1.0`
and `tico-power-1.0` are the old lineup (`deprecated`, `lineup: old`): no picker offers them, but a bot already on one keeps
running at its old price. In Settings the Gateway is a harness / provider of its own ("Tico AI Gateway") whose Model control is
Department, Level, Fast and Version, and the stored model is the id they make. Their provider, `tico`, is never ticked: it is on
exactly while the **Tico AI Gateway** connection in Settings > Models has a key (`backend/gateway_connection.py`),
and OpenRouter's catch-all does not offer them. The key is the one Decide uses; the server's `TICO_GATEWAY_KEY` and
`TICO_GATEWAY_URL` win over a saved one and show as connected. A saved key is sealed and never read back, only a
preview of its first and last three characters. What the owner sees and does is in [Tico AI Gateway](ai-gateway.md).

No bot gets the key. For a bot on a Tico model, or whose own fallback is one, the runner fetches it
(`GET /api/v2/runner-gateway-credentials`, runner only) and starts a relay on 127.0.0.1 for that turn
(`runner-rs/src/gateway_relay.rs`); the turn gets the relay's URL and a token good for that turn's model calls only (a
pi kept between turns keeps both, closed between its turns), as
`TICOAI_GATEWAY_URL` and `TICOAI_GATEWAY_KEY`. pi reads them through a `tico` provider in the turn's own `models.json`
(OpenAI chat completions, streamed), in a directory removed when the turn ends. A runner hands out the key only when
it runs bot code as another user (Tico's Docker runner); elsewhere a Tico model's turn is refused once
(`not_isolated`, not retried) and a Tico fallback is left out. The server reads the Gateway's word on the key
(`<gateway>/key`) on save, on **Check now**, hourly, and when an owner opens the page (at most once a minute, since
the Gateway answers that six times a minute per key): the owner sees the 80% warning, **Gateway cap reached** and a
key the Gateway doesn't recognise (revoked or unknown, one plain message) in Settings and Health. A Gateway that
answers 404 (no chat endpoint yet, or a wrong URL) shows as **Not ready**.

The Gateway names every refusal with a code, the same on every endpoint (`error.code`, or a flat `error` from
decisions). pi's own retry is off for Tico models, so the server decides what happens next from the code, for a Tico model the bot runs on or names as its own fallback (an automatic fallback is never a Tico model). pi sends the bot's effort as `reasoning_effort`, its `max` as the Gateway's `xhigh`:

| Code | What the bot says | The run |
|---|---|---|
| `cap_reached` | Gateway cap reached | Waits until the next UTC month, whatever Retry-After says |
| `revoked_key`, `unknown_key` | The Tico AI Gateway key is not working | Waits for a new key |
| `rate_limited` | Tico AI Gateway rate limited | Waits the Gateway's Retry-After (an hour), never sooner |
| `unknown_model` | The Gateway doesn't offer this model, and the three it does | Saved for later, not retried |
| `unsupported_field`, `bad_request`, `upstream_refused` | Refused as sent, with the Gateway's message | Saved for later, not retried |
| `no_route` | No Tico route for this request | Saved for later, not retried |
| 404 | The Gateway doesn't serve Tico models at its URL yet | Saved for later, not retried |
| `upstream_unavailable` | Tico AI Gateway is unavailable, try again | Back in the queue if nothing ran, at most twice, the bot waiting 1 then 2 minutes first; then saved for later |

A reply the Gateway cuts off after it began streaming is posted as far as it got, with a note saying the Gateway cut
it off. Only codes are logged, never the key.

## Choosing a harness, model and effort

Bot settings, new bots, fallbacks and bulk model changes use three separate fields:
**Harness / provider**, then **Model**, then **Effort**. Model choices follow the selected harness
and the team's enabled providers; effort choices follow that model. For an existing bot's primary model, choose
all three and press **Apply** to use the existing confirmation and checkpoint transition. Changing
a field alone does not switch the running bot. Cancel restores the saved selection. In the bot editor,
**Save bot** with a model picked but not applied opens the same confirmation; press **Prepare change** to
save the bot and start the change, or **Cancel** to keep the editor open with "Model change not applied".
Everything else in the bot editor (fallback, task model, access, owners, computer, profile, run budget) is held until
**Save bot**, which saves it all together; a line above the button lists what is not saved yet. If something can't be
saved, the editor stays open with the reason.

A subscription quota response is a usage limit. The runner uses its configured fallback, or another
signed-in provider when automatic fallback is on. Without a fallback it keeps the work queued; an explicit
usage-window reset holds it until that time. **Try now** still lifts the hold after a person renews the plan.

The current OpenAI and Claude choices, reviewed 2026-10-03, are:

| Harness | Model | Model ID | Released |
|---|---|---|---|
| Claude Code (Anthropic) | Opus 5.5 | `claude-opus-5-5` | 2026-09-22 |
| Claude Code (Anthropic) | Sonnet 5.5 | `claude-sonnet-5-5` | 2026-09-28 |
| Claude Code (Anthropic) | Fable 5.1 | `claude-fable-5-1` | 2026-09-01 |
| Claude Code (Anthropic) | Haiku 5.5 | `claude-haiku-5-5` | 2026-10-07 |
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
Muse Code offers Muse Spark 1.3 (`muse-spark-1.3`, effort low to max, medium by default), Muse Wonder
(`muse-wonder-1.0`) and Muse Wonder Full (`muse-wonder-full-1.0`), both high by default. These are the ids Meta's
service accepts (checked with Muse Code 1.4.8 on 2026-10-14); `muse-wonder` and `muse-wonder-full` without the version
are refused.
Grok Build retains its catalog model and supported efforts through the
[existing CLI integration](https://docs.x.ai/build/overview). Selecting a harness does not enable
its provider, grant credentials or guarantee model access on a computer.

### Run a bot's tasks on another model

Use **Task model**, **Task effort** and **Task context length** for a bot's task sessions. Each starts as
**Same as bot** and is set on its own: a task effort or context length works without a task model. Chat keeps the
bot's own model, effort and **Compact at**.

1. Open **Settings > Bots** and click **Tasks** beside the bot's model.
2. In the row that opens, choose the harness and model and click **Apply**, or change **Task effort** or
   **Task context length** (each saves when changed).

**Task effort** lists the efforts of the model that runs tasks: the task model, or the bot's own. **Task context
length** offers the **Compact at** sizes. The **Tasks** mark gets an accent dot when any of them is set, and names
a task context length (**Tasks · 600k**). For example, you can keep a bot on Sonnet 5.5 and choose Haiku 5.5 for its
tasks. You can also set them in the bot editor, then click **Save bot**. Changing one keeps the others: picking
**Same as bot** for the model keeps the task effort and context length, and tasks are back to the bot's own settings
only when all three are **Same as bot**. Changing the bot's own model leaves its task settings set; a bulk **Change
model** for task models keeps each bot's task context length.

If a choice cannot be saved, the row or editor shows the reason. A saved task model that cannot run falls back
to the bot's own model, with the task context length and any task effort that model takes; the task history records why.
A task's own model ([Choosing the model a task runs on](tasks.md#choosing-the-model-a-task-runs-on)) falls back to the
bot's task model first, then to the bot's own model. A review or check subtask keeps its check model.

#### A warning about a cheaper task model

A bot that has requested send, spend or publish approvals, or holds a tool grant that can write beyond its
own repository, can show a warning under **Task model**. The warning compares models from the same provider
using their listed output prices. Another provider's model, or one without a listed price, is not judged.

For example: "This bot sends messages for you. Haiku 5.5 is a cheaper model than its own Sonnet 5.5, and
may word things badly." The warning names the risk and still lets you save. Tasks still run on that model;
the run note and task history say it ran "below the minimum for this bot". The warning updates when the bot's
own model changes. A requested tool grant that has not been added does not count, nor does access to the bot's own repository.

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
volume, so harnesses survive a restart or a new image. The directory is put last on `PATH`, except on a cloud machine.

- **Mac**: a harness already on `PATH` (Homebrew, `npm -g`) is used as it is. The runner installs into its tools
  directory only when the harness is missing, and never updates one it did not install. Installing needs `node` and
  `npm` on the Mac.
- **Cloud machine**: Tico owns the disk, so a copy on `PATH` is not left alone. A harness a bot wants gets the runner's
  own install, which then wins and updates as usual; the tools directory goes first on `PATH`. See
  [Cloud machines](cloud-machines.md#whats-on-a-machine).
- **Linux (Docker)**: the image has node, npm, python, pip, git, gh, build tools, ripgrep, jq and curl, and no model
  CLI. It also has the system libraries Chromium needs, so a browser a bot installs (`npx playwright install
  chromium`) starts. When a Playwright Chromium is on a computer, the runner starts it headless every 15 minutes;
  two failures in a row show in Health as "<computer>: browser does not start".
- **Current Docker runner**: the supervisor `ticorun` (UID 10002) owns the tools directory, registration
  and runner state; bot code runs as `bot` (UID 10003) and cannot replace those files. Bots still share
  their bot user, workspace and model logins. Every model host, including Muse Code, Gemini and Antigravity,
  starts its CLI through the same UID/GID and capability-dropping helper. Muse prompts and Gemini's own
  CLI state belong to the bot user. Antigravity's conversation records stay private to the supervisor;
  its bot-readable token file has mode 0600 and is blanked between turns. Start with the release's
  [runner Compose file](../docker/runner.compose.yaml), which supplies the required user and capabilities.
- **Native Mac/Linux and legacy containers without isolation**: bots share the runner's user and can
  replace a CLI or reach that user's runner state. See the isolation notes in
  [Install](install.md#add-computers-to-run-your-bots) and [Security](../SECURITY.md).

## Choosing what a computer installs

**Settings > Computers > Installation and updates** lists every harness on each computer with an **Enabled** switch for
the owner. These controls manage installation; the separate status above says whether each tool is signed in.
A harness a bot on that computer runs on, as its own or its fallback, is always on: the switch is locked and the
server refuses an off for it (409; `bot_runtimes` in `GET /api/v2/operations` lists them). Any other one can be turned on, for
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

Every day, and at every start on a cloud machine, the runner asks each harness it installed (and has not been turned off) for the newest release. With the default policy (`latest`) it
installs the new version **between runs**: the copy is downloaded and checked beside the old one while runs are in progress,
and switched in only at a moment no run in progress uses that harness (a run on another harness does not hold it
back). A failed install keeps the working version and is retried after 30 minutes.

A harness can be **pinned** to a version: the runner keeps that version and only reports that a newer one exists.
The manifest can ship a pin (`[update] policy = "pinned"`, `pin = "1.2.3"`), and the owner can pin or unpin per
computer.

**Settings > Computers > Installation and updates** shows a chip per harness on each computer: name, installed version, `pinned`, `<version> available`,
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

1. **Host adapter**: `runner-rs/src/hosts/<host>.rs` (`start_thread`, `start_turn`, `interrupt`, and events on the
   host's channel). Add it to the `Host` enum in `runner-rs/src/hosts/mod.rs` and to `make_host` in
   `runner-rs/src/execute.rs`.
2. **Manifest**: `runner/harnesses/<id>.toml`. The file name is the id.

   ```toml
   id = "acme-agent"            # lowercase letters, digits, hyphens
   name = "Acme Agent"
   host = "acme"                # runner-rs/src/hosts/acme.rs
   runtime = "acme"             # what bot configs and the server call it (`claude` for claude-code.toml)
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
   logout = ["logout"]          # optional: the CLI's sign-out, for Sign out and Switch account in the app (needs files)
   files = ["auth.json"]        # the login's files, relative to its home; a switch moves these in from the side folder
   seed = []                    # of files, those copied into the side folder first (settings the CLI keeps with the login)
   keychain = []                # platforms (as the runner names them, e.g. "darwin") where the CLI keeps the login in
                                # the system keychain instead: no Switch account there (`switch_unavailable_on`)
   connect = ["login", "--device-auth"]  # optional: signs a plan in for a company subscription (Connect in Settings > Models)
   connect_file = "auth.json"   # its result: this one of files, in a home of its own; or connect_token = "<prefix>",
                                # a token it prints starting with that (and a kind in backend/model_subscriptions.py)

   [capabilities]               # what Tico does differently for this CLI; see Capabilities below
   terminal_login = ["login"]
   mcp_transports = ["http"]
   ```

3. **Provider and models**: add the provider (with `runtime` and `harness`) and its models to `backend/providers.py`.
4. **Check**: `shipped_manifests_parse_and_claim_once` in `runner-rs/src/harness_tools.rs` validates every manifest
   (file name, unique providers, install fields). `backend/tests/test_harness_capabilities.py` fails on a new
   comparison with a CLI name outside the host adapters: add a capability instead.

Loading fails loudly on a bad manifest, naming the file and the field.

## Capabilities

Code never asks which CLI a bot runs; it asks the manifest's `[capabilities]`. The server
(`clients/harness_manifests.py`), the Rust runner (`runner-rs/src/harness_tools.rs`) and the web app (`harnesses[].capabilities`
in `GET /api/v2/models`) read the same files. An absent key means its fallback below; a harness with no manifest (an
external agent) has no capabilities, and the app treats each as unknown. A capability that is on and fails at runtime
is a bug in its host, not a reason to compare names.

Sign-in facts come from the tables that already hold them: `[login]` present means the browser relay (Settings >
Computers), `paste_code` means it takes a pasted code, `logout` means Sign out and Switch account (`sign_out` in
`harnesses[].capabilities`), `connect` means Connect in Settings > Models (`subscription_connect`), `auth.api_key_env` names the keys. A login's `files` are paths inside its home (the
`profile_home` variable's folder, or `HOME`), never absolute, never with `..` or an empty part; `seed` names only
some of `files`, and `logout` needs `files`. `keychain` lists the platforms where Switch account is not offered
(`switch_unavailable_on` in `harnesses[].capabilities`; Claude Code on a Mac).

Codex bots on the same ChatGPT login run turns at the same time, as Claude bots do. All bots on the computer's
login share its plan's usage allowance. Tico never copies a login into
another bot's home; all turns use the original login in its own home.

| Capability | Means | Absent |
|---|---|---|
| `terminal_login` | What a person runs on the computer to sign in (after the executable) | No interactive sign-in; keys only |
| `sign_in_status` | How readiness checks the sign-in: `check` = `command` (`args`, `ready`, `not_ready` text), `json` (`args`, `field`, `method_field`), `file` (the host's login file), `bot-key` (each bot's key grant) or `none`; `ready_detail`, `missing_detail`, `timeout_s` | `none`: reported as `unknown` |
| `api_keys` | Who holds `auth.api_key_env`: `computer` (one key signs every bot in; the company can give it to every computer) or `bot` (each bot's own Credential, the first name) | Keys are not wired by Tico |
| `api_key_login` | The command that turns a computer's key into a stored login (key on stdin) | The key in the environment is the sign-in |
| `api_key_check` | A command that asks the provider about the key in its environment, run in an empty home so no other login answers; it passes on exit 0 with no `"is_error": true` in its JSON. Switch to API key checks a key with it (or with `api_key_login`) before the subscription is signed out | Switch to API key is offered only with this or `api_key_login` |
| `api_key_in_turn` | The CLI accepts a key in the turn's environment, ahead of a stored login; an explicit bot Credential grant can run without a subscription login. A computer key is passed when supported | No computer key passed per turn |
| `subscription_marker` | Text in the sign-in detail that means a plan, so turns bill as `subscription` | Every turn bills as `api` |
| `subscription_profiles` | Can run on a computer whose login is a profile folder (`default_profile`; `profile_home` is the variable pointed at the profile's folder; `profile_dirs` are made inside it) | Not covered by subscriptions |
| `profile_home_overrides` | Variables the CLI reads ahead of `profile_home`; a profile's sign-in, sign-in check and runs never inherit them | Nothing dropped |
| `own_config_home` | Bot runs use a config folder of Tico's own, never the operator's | The operator's (or the profile's) home |
| `weekly_usage` | `refresh` (turns report it, and it can be refreshed on demand), `turns`, or `manual` (only a person's reading) | No weekly usage |
| `account` | Where the signed-in account (email, plan, organization) is read: `status-json` (the `sign_in_status` JSON's `email`, `subscriptionType`, `orgName`) or `app-server` (Codex app-server `account/read`, never with a token refresh). Credential files are never read | Account shown as unavailable |
| `tool_events` | The host emits one `tool` event per tool call, so a run with none provably did nothing | No evidence either way |
| `mcp_transports` / `mcp_unsupported` | The MCP transports a run takes; the reason shown when it takes none | No MCP servers, "it has no MCP support in Tico" |
| `resume_after_interrupt` | An interrupted thread can be continued | `true`. No longer read: an interrupted session is always kept and resumed ([sessions](sessions.md#checkpoints-and-resume)) |
| `steer` | A message sent while a turn runs is written into that turn (Claude Code, Codex and pi; Claude Code also needs a CLI that echoes its input) | The message waits for the next turn; DM says so and offers Stop and redirect |
| `persistent_session` | One process per conversation is kept between turns (pi, Antigravity) | One process per turn |
| `goal_runs_across_turns` | A native goal keeps the thread going after a turn completes, and a clear is reconciled | A goal ends with its turn |
| `serial_cold_start` | An expired stored token is refreshed by the first turn; the others wait for it | Turns start together |
| `one_turn_per_login` | Optional queue, disabled for Codex (`false`). A subscription login (its `subscription_marker` in the sign-in detail) runs one turn at a time on the computer: another bot's turn on it waits, or runs on its fallback. A key login is not held | Turns on one login run together |
| `isolated_user` | The CLI keeps no owner-only sign-in state in the bot's home, so on a Docker runner with a turn-user pool each turn runs as its own leased OS user that cannot enter another bot's checkout. Only pi sets it: Claude Code, Codex, Gemini CLI, Cursor, Grok, Muse Code and Antigravity keep 0600 sign-in files shared across turns | The turn runs as `bot`; readiness reports it as not isolated |
| `compaction` | The bot's Compact at setting applies | Setting hidden |
| `compaction_events` | The harness says in its stream when it compacts a session (Claude Code's `compact_boundary`, pi's `compaction_end` with a result), so a resumed turn whose standing instructions and task are unchanged and still in the session gets one line for each instead; after a compaction the next turn gets them in full | Every turn gets the whole prompt |
| `resume_command` | The local command that resumes a session (the id follows) | `<executable> --resume` |
| `model_list` | `host`: the host adapter lists the models in readiness; `command`: the CLI lists them (`model_list_args`) | No model list |
| `readiness` | The runner reports install and sign-in for it | `true`; `false` reports nothing, so checks say unknown |
| `repo_permission_lint` | Readiness warns about permission rules in the bot's repository | No lint |
| `repo_commands` | The bot's repository skills and commands are offered as chat commands | Built-in commands only |

Goals, chat commands and controls stay probed: the runner reports them per installed version in its heartbeat.

A runtime with a second host (Gemini's Antigravity) declares it as `[variants.<harness>]` in the runtime's manifest,
with its own `name`, `host`, `executable` and capabilities. The runner never installs a variant.

