# OpenClaw agents

An [OpenClaw](https://openclaw.ai/) profile can be a bot in Tico, the same way a
[Hermes profile](hermes-agents.md) can: Tico knows it is there, shows whether it is alive, and gives it a
credential to use when it chooses. Tico never starts a run for it. It uses the same `hermes_agent.py` Tool,
with `--harness openclaw`.

**Tested with** OpenClaw 2026.9.9 (stable, October 2026) on macOS with Node 24, and earlier with 2026.3.13. The 2026.9.9 check used a scripted stand-in model; a run with a real model, the macOS heartbeat timer and the Gateway as an installed service were not exercised.
OpenClaw 2026.9 needs Node `>=24.16 <25` or `>=26.1`.

## Connect an OpenClaw profile in 2 minutes

You need:

- an OpenClaw profile that is set up (`openclaw --profile <name> setup`, or the default profile in `~/.openclaw`);
- **a model the profile can use.** OpenClaw answers with its own model, so the profile needs a provider signed
  in (`openclaw --profile <name> models status` shows no "Missing auth"). Without one, every sync run ends at
  the first model call ("No route-compatible authentication source is configured") and nobody gets an answer;
- the profile's **Gateway running** (`openclaw --profile <name> gateway status`, or `gateway run` in a terminal);
  the sync job is an OpenClaw cron job, and cron runs inside the Gateway;
- the Tico address shown in **Settings → Computers → Add computer**. Use that full address as `<Tico URL>`
  below: normally `https://tico.example.com`, or `http://127.0.0.1:8765` for a local install on the same
  computer. If an external sign-in proxy returns HTML instead of JSON, follow
  [proxy troubleshooting](connect-an-agent.md#behind-cloudflare-access-or-another-sign-in-proxy); built-in
  sign-in needs no separate hostname.

1. On the computer that runs OpenClaw:

   ```bash
   curl -fsSL "<Tico URL>/api/v2/agents/setup-script" -o hermes_agent.py \
     && python3 hermes_agent.py pair --harness openclaw --profile <name> --url "<Tico URL>"
   ```

   Leave out `--profile <name>` for the default profile. The command prints a code like `K7QM-4F2P` and waits
   up to 10 minutes. No token to copy.
2. In Tico, tell BotOps: **"Connect my OpenClaw profile `<name>`, code K7QM-4F2P."** BotOps adds the bot if it
   does not exist yet, then approves the code as you. Or approve it yourself: **Settings → Bots**, **Pair** in
   the bot's Computer column, type the code. Only a bot's owner or an admin can approve.
3. **Check it worked.** The command ends with `Registered OpenClaw profile ...`, `OpenClaw cron job tico-sync,
   every 1h, skill attached` and `Tico sees it: ...`. Then:

   ```bash
   python3 ~/.config/tico/agents/hermes_agent.py doctor --harness openclaw --profile <name>
   openclaw --profile <name> skills info tico-sync     # "Ready", source openclaw-managed
   ```

   `doctor` ends with `Everything needed is working.`, and the bot shows online in **Settings → Bots**.
4. **Test it.** Message the bot in Tico. It answers in the same chat at its next sync run, up to the sync interval
   later (an hour by default). To see it now, run the job once:
   `openclaw --profile <name> cron run <job id> --wait` (the id is in `openclaw --profile <name> cron list`).

`pair` saves the credential on the computer (mode 600), installs the `tico-sync` skill in the profile's
`skills` folder, schedules the sync job, and starts a heartbeat timer, then prints what it did. It never prints
the credential.

## What is different from Hermes

- **Tico's tools go through the connector, not MCP.** `pair` adds no MCP server to OpenClaw's config. The
  credential is in `tico.env` in the profile's state folder (mode 600) and in
  `~/.config/tico/agents/openclaw-<name>.json`. The skill calls the Tico tools this bot may use through
  `hermes_agent.py`:
  `python3 ~/.config/tico/agents/hermes_agent.py call --harness openclaw --profile <name> hub_message_list`
  (one HTTPS call to Tico's MCP endpoint with the bot's token; the agent never sees the token). This works
  with every OpenClaw release since 2026.3. See [MCP](#mcp) for the client newer releases have.
- **Profiles.** `--profile <name>` is OpenClaw's own `--profile`: state in `~/.openclaw-<name>`. Leave it out
  for `~/.openclaw`. The saved files are named `openclaw-<name>`, so a Hermes profile and an OpenClaw profile can
  share a name.
- **The job is an OpenClaw cron job**, created with `openclaw cron add`: an isolated agent turn with nothing
  delivered to a chat. It lives in the profile's Gateway, so the Gateway must be running for it to fire. Unlike
  Hermes there is no pre-check that keeps the agent asleep, so every run is one agent turn: its first step is a
  one-line check that ends the run at once when nothing is waiting (a small, cheap model call).
- **The Gateway announces itself on the local network** (Bonjour) by default. On shared office Wi-Fi, check
  OpenClaw's Gateway settings if you don't want it visible to other machines.

## Keep it in sync

`--sync <interval>` works as it does for Hermes: `15m`, `1h` (the default), `daily`, a cron expression in
quotes, or `off`. The shortest is `5m`. It is the agent's own schedule, and Tico never starts it, so a message
waits up to one interval for its answer.

- **Change it:** `python3 ~/.config/tico/agents/hermes_agent.py reinstall --harness openclaw --profile <name> --sync 30m`.
  This replaces the one `tico-sync` job and leaves your other jobs alone.
- **What each run does:** the same skill as Hermes ([what it does](hermes-agents.md#keep-it-in-sync)): read
  what is waiting, answer each person in their conversation, move its tasks (to done with a result note; the
  requester closes them), mark the messages read, stop. Once a week it runs the `update` command in
  `hermes_agent.py`.
- **Check it:** `python3 hermes_agent.py doctor --harness openclaw --profile <name>` says whether the job
  exists, when it last ran and how it ended, and whether the Gateway is up. `openclaw [--profile <name>] cron list`
  is OpenClaw's own view.
- **If the Gateway was down when you paired,** the pairing still works and the command says the job is not in
  place. Start the Gateway, then run `reinstall --harness openclaw --profile <name>`.

## Keep it working and remove it

The commands are Hermes's (see [Keep it working](hermes-agents.md#keep-it-working)) with `--harness openclaw`:
`update`, `reinstall`, `status`, `doctor`, `heartbeat` and `uninstall`.

| You see | What it means | Do this |
|---|---|---|
| `doctor`: "the OpenClaw Gateway is not running" | No cron job fires, `tico-sync` included. The heartbeat still runs, so the bot shows online. | Start the Gateway (`openclaw --profile <name> gateway start`, or `gateway run`). The job is kept; the next run picks up everything that waited. |
| A sync run ends in `error`, "No route-compatible authentication source" | The profile has no signed-in model. | Sign the profile in to a provider (`openclaw --profile <name> configure`), then run the job once. |
| `reinstall` or `pair` says "WARNING: the sync job is not in place: openclaw cron list: Gateway target ..." | The Gateway was down, so the job could not be made or changed (a job made earlier stays as it was). | Start the Gateway, then `reinstall`. |
| `doctor`: "GET /api/v2/me failed: ... credential revoked" (401) | The credential was revoked or replaced in Tico. | `pair` again and approve it, or `uninstall` if the profile should stop. |

**Remove it.** `uninstall` removes the heartbeat timer, the `tico-sync` job, the skill, `tico.env` and the
credential file; it leaves OpenClaw's own config, your other cron jobs and the connector copy in
`~/.config/tico/agents/hermes_agent.py` (other profiles may use it) alone. It does **not** revoke the
credential in Tico: do that as a separate step in **Settings → Bots** (the bot's Computer column), or ask BotOps.
After a revoke, a profile that still has the connector gets 401: the heartbeat backs off to once an hour and
says `credential revoked: run pair again`, `check` and `doctor` say the same, and the skill stops at its first
step. The cron job still starts one short agent turn each interval until you `uninstall`.

## MCP

OpenClaw 2026.3 had no MCP client. OpenClaw 2026.9 has one: `openclaw mcp add|set|probe|list` manages remote
MCP servers (streamable-http or sse, with headers) and exposes their tools to the agent as `<server>__<tool>`.
Tico's MCP endpoint works with it. `openclaw --profile <name> mcp probe` against `<Tico URL>/api/v2/mcp`
with the bot's credential lists the bot's `hub_*` tools as `tico__hub_*`.

The connector does not set this up, and the `tico-sync` skill does not depend on it: the skill calls Tico through
`hermes_agent.py call` on every release. If you add Tico as an MCP server yourself, keep the token out of
`openclaw.json`: put `TICO_AGENT_TOKEN=...` in the profile's `.env` (mode 600) and use
`"headers": {"Authorization": "Bearer ${TICO_AGENT_TOKEN}"}`. The Gateway reads `.env` when it starts, so restart
it after adding the line (`mcp reload` is not enough). In a cron turn the tools are found through OpenClaw's
`tool_search` and called with `tool_call`. `uninstall` does not remove an MCP entry or `.env` line you added.

## In Tico

An OpenClaw bot is an external bot like a Hermes one: model **OpenClaw/its own model** (`hub bot create <slug>
--record-only --model openclaw`), harness `openclaw`, no computer, one standing credential, a heartbeat every
minute (offline after 180 s), and no runs or usage, because nothing is dispatched. A message to it waits until
the agent reads it. Everything else, including the troubleshooting table and the credential rules, is as in
[Hermes agents](hermes-agents.md).

What Tico shows and does not show:

- **Online is the heartbeat, not the work.** The heartbeat is a plain HTTP call from a timer. It proves the
  computer and the profile are there, not that the model works or that the Gateway runs the sync job. Completed
  work shows as the bot's answers, task notes and task status.
- **No Tico-dispatched runs.** Tico never starts the agent; there are no runs on the bot's page and no Stop.
- **Usage is unknown to Tico.** The model calls are made by OpenClaw with its own provider account; Tico
  records none of them.

Profile paths follow OpenClaw's environment: `OPENCLAW_STATE_DIR` wins even for a named profile;
otherwise `OPENCLAW_HOME` supplies the home used for `.openclaw` or `.openclaw-<profile>`. An explicit
`--profile-dir` wins over those defaults. Tico saves the resolved path and passes it to every OpenClaw
cron command, including listing, replacing and removing the sync job, so files and jobs use the same
profile after an update or reinstall.

With `--no-timer`, heartbeat mode is manual: run `heartbeat` with the same profile flags every minute.
Doctor warns about that requirement without reporting a missing timer as broken. Reinstall and update
keep your selected mode. To enable the timer later, run `reinstall` with the same profile flags and `--timer`.
