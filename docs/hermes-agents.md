# Hermes agents

> **Beta**: external agents (Hermes, OpenClaw, Grok Bot, Dots and Muse sync) work today and may still change. See [Beta features](support.md#beta-features).

A [Hermes](https://hermes-agent.nousresearch.com/) profile can be a bot in Tico. Tico knows it is
there, shows whether it is alive, and gives it a credential to use when it chooses. Tico never
starts a run for it.

## Connect a Hermes profile in 2 minutes

You need the profile to exist already (`hermes profile create <name>`), and the Tico address shown
in **Settings → Computers → Add computer**. Use that full address as `<Tico URL>` below: normally
`https://tico.example.com`, or `http://127.0.0.1:8765` for a local install on the same computer.
A separate runner hostname is needed only if an external sign-in proxy intercepts API requests
(see Troubleshooting).

1. On the computer that runs the profile:

   ```bash
   curl -fsSL "<Tico URL>/api/v2/agents/setup-script" -o hermes_agent.py \
     && python3 hermes_agent.py pair --profile <name> --url "<Tico URL>"
   ```

   It prints a code like `K7QM-4F2P` and waits up to 10 minutes. No token to copy.
2. In Tico, tell BotOps: **"Connect my Hermes profile `<name>`, code K7QM-4F2P."** BotOps adds
   the bot if it does not exist yet, checks the profile and host, then approves the code as you. Pairing activates a planned bot. You can also approve it
   yourself: in **Settings → Bots**, press **Pair** in the bot's Computer column (also on the bot's page, More → Setup),
   and type the code. Only a bot's owner or an admin can approve.
3. In the profile's chat, run `/reload-mcp` (or restart its gateway). The profile now has the Tico tools its bot credential is allowed to use.
4. Test it: message the bot in Tico, then ask the profile to check its inbox. It should answer
   in the same chat.

The `pair` command sets up the profile's config, saves the credential on the computer, starts a
heartbeat timer and schedules the sync job (hourly unless you add `--sync`, see
[Keep it in sync](#keep-it-in-sync)), then prints what it did. It never prints the credential.
The timer is the last step. If launchd or systemd refuses it, the pairing, the skill and the sync job stay in
place, heartbeat mode becomes manual, and the command ends with a `WARNING` that says what failed, gives a cron
line for the heartbeat, and gives the `reinstall ... --timer` command to retry (no new approval). It still exits 0.

The setup script downloads without a browser sign-in from the address Tico shows (Tico 0.2.26
and later). Built-in sign-in and local installs need no separate hostname. If an external proxy
returns a sign-in page, follow Troubleshooting below.

### The manual way

1. **Settings → Bots → Add bot.** Pick the model **Hermes/profile's own model**. The computer
   field goes grey: a Hermes bot has no computer. Save.
2. **Create credential** in the bot's Computer column. The credential is shown once. The dialog
   gives one install command to run on the computer that runs the profile:

   ```bash
   curl -fsSL -H "Authorization: Bearer <credential>" "<Tico URL>/api/v2/agents/setup-script" -o hermes_agent.py \
     && python3 hermes_agent.py install --profile <name> --url "<Tico URL>" --bot <slug> --token <credential>
   ```

3. `/reload-mcp` in the profile.

Members can add Hermes bots too, and each one counts toward their bot limit
([people.md](people.md)).

## Using it day to day

Nothing pushes work to a Hermes bot. It reads its messages when its own schedule, or a person
talking to it, makes it look. `pair` and `install` set that schedule up for you: one Hermes cron job,
`tico-sync`, that runs the Tico sync skill. You do not write a prompt or a job. Next section.

The profile uses the shared tool list (`clients/hubtools.py`); its bot credential controls which tools and records it can access. With a Tico checkout on the computer the
`hub` CLI works too, with `HUB_API_URL` and `HUB_TOKEN`.

## Keep it in sync

`pair` and `install` take `--sync <interval>`, default `1h`:

| `--sync` | The agent looks |
|---|---|
| `15m`, `30m`, `2h`, `1d` | every that long (5 minutes is the shortest) |
| `1h` (default) | every hour |
| `daily` | once a day at 09:00, the computer's time |
| `'0 8-18 * * 1-5'` | on that cron expression, in quotes |
| `off` | never on its own; the skill stays installed and you can run it by hand |

```bash
python3 hermes_agent.py pair --profile <name> --url "<Tico URL>" --sync 15m
```

What it installs, all inside the profile:

- **The skill**, `skills/tico-sync/SKILL.md`, the same file for Hermes and [OpenClaw](openclaw-agents.md)
  (it is `skills/tico-sync/SKILL.md` in the repository; `update` fetches the newest from Tico). Each run it reads who
  it is and any quiet notes Tico holds for it, reads its waiting messages and its open tasks, answers every
  person's message in that conversation with `hub_message_send` and marks it read, moves the tasks it owns
  forward with `hub_task_update` (done with a short result note, or waiting with the reason), and stops. It keeps
  answers short and never pastes a secret. Once a week it runs the `update` command in `hermes_agent.py`.
- **A pre-check script**, `scripts/tico-sync-check.py`. Hermes runs it before each job. It asks the saved
  heartbeat reply what is waiting, and when nothing is (and no update is due) it ends with
  `{"wakeAgent": false}`, so Hermes starts no agent and spends no model call. An empty run is one short
  Python run. If the saved reply is older than five minutes it makes a fresh heartbeat first, so a stopped
  timer cannot hide work.
- **One cron job** named `tico-sync`, with the skill attached, delivering nowhere (`--deliver local`). Running
  `pair`, `install` or `reinstall` again replaces it; it never touches your other jobs.

Hermes runs cron jobs only while the profile's **gateway** runs, so without it the sync job never fires.
`doctor` warns when the gateway is not running and gives the fix: `hermes -p <name> gateway install`
(`hermes -p <name> gateway status` and `hermes -p <name> cron status` say how it is doing). `doctor` also warns
when the profile's `.env` connects the gateway to Slack, Telegram, Discord or another chat service, because
chats there bypass Tico's rules.

**Change the interval** any time, on the computer:

```bash
python3 ~/.config/tico/agents/hermes_agent.py reinstall --profile <name> --sync 30m
```

`--sync off` removes the job. Without `--sync`, `reinstall` (and `update`) keep the saved interval.
A profile connected before sync jobs were added has no sync job until you run `reinstall --sync 1h`. A faster schedule means
faster answers and more model use, so only when something waits.

`doctor` says whether the job exists, its schedule, and when it last ran (from Hermes's own job record).
`status` prints the saved interval. `python3 hermes_agent.py check --profile <name>` prints what the pre-check
sees, for trying it by hand.

## Keep it working

These commands run from the installed copy of `hermes_agent.py`, `~/.config/tico/agents/hermes_agent.py`; the
downloaded `hermes_agent.py` in your current folder works the same while it is there.

- **Update.** `python3 hermes_agent.py update --profile <name>` fetches the current script from Tico,
  replaces the copy the timer runs and installs again (the skill and the sync job too). Run it after each Tico
  update; with a sync job the skill also runs it once a week for you. It also removes older duplicate heartbeat
  jobs.
- **Reinstall.** `python3 hermes_agent.py reinstall --profile <name>` runs the install steps again with
  the credential saved by `pair` or `install`: config entry, `.env`, timer, skill, sync job. `update` ends by
  running it. `--sync <interval>` changes how often the agent looks.
- **Status.** `python3 hermes_agent.py status --profile <name>` prints the saved settings and the last
  heartbeat reply. It never prints the credential.
- **Doctor.** `python3 hermes_agent.py doctor --profile <name>` checks the setup (config,
  credential, reach to Tico, timer, last heartbeat, sync job and when it last ran) and scans the profile's `SOUL.md`, skills, cron
  jobs and memories for old tool names. It changes nothing and says what to fix. A sync run that ended in an error
  is a problem, with Hermes's error. `doctor` does not check the model, because Hermes has no offline check for it
  and doctor never calls a model: it prints a `NOTE` saying so, and its last line says
  `Everything doctor checks is working; it did not check the model`.
- **Move to another computer.** Run `pair` on the new computer with the same profile name and
  approve it. Approving replaces the credential, so the old computer stops with a 401. On the old
  computer run `python3 hermes_agent.py uninstall --profile <name>`.
- **Rotate or revoke.** Settings → Bots → the bot's Computer column, or ask BotOps. Rotating
  replaces the credential at once; pair again, or install the new one. Revoking stops it for
  good. Revoke if the computer is lost. When you remove (archive) a Hermes bot in Settings → Bots, the
  dialog has a **Revoke its credential** box, on by default.
- **Remove the Tool.** `python3 hermes_agent.py uninstall --profile <name>` removes the timer, the
  `tico-sync` job, the skill and its pre-check script, the `.env` lines, the `mcp_servers.tico` entry and the
  credential file. It does not revoke the credential in Tico.
- **Renamed tools (0.2.21).** The `hub_*` tools were renamed: `hub_inbox` is now
  `hub_message_list`, `hub_say` is `hub_message_send`, `hub_ack` is `hub_message_mark_read`.
  Old names now answer "X was renamed Y". After updating, `/reload-mcp`, change any saved prompt
  or cron job that uses an old name, and let `doctor` find the ones you miss.

## Troubleshooting

| You see | What it means | Do this |
|---|---|---|
| Heartbeat or tool says **409**, "bot is archived" | The bot was archived. The agent stops and retries only once an hour. | Restore the bot (**Settings → Bots → Archived → Restore**, `hub bot restore <slug>`, or ask BotOps). `hermes_agent.py` tries again within the hour and reconnects by itself. |
| **401** | The credential was revoked or replaced (archiving with the revoke box on does this). | Restore the bot if it is archived, then pair again (`pair`) and approve it. |
| A login page instead of JSON, or `curl` gets HTML | An external sign-in proxy is intercepting API requests. | Configure a bypass or a separate API hostname, then use the address Tico shows. See [proxy troubleshooting](connect-an-agent.md#behind-cloudflare-access-or-another-sign-in-proxy). Built-in sign-in and local installs need neither. |
| Two heartbeats a minute, or double answers | A duplicate heartbeat job from an older install. | Run `update`. It removes older jobs. `doctor` lists what it found. |
| The bot is online but no messages arrive | Tico never pushes work. The profile only looks when something makes it. | Run `doctor`: it says whether the `tico-sync` job exists and when it last ran. Hermes runs cron only while the profile's gateway runs: `doctor` says so, and `hermes -p <name> gateway install` fixes it. `reinstall --sync 1h` brings the job back. |
| Tool not found | An old tool name. | See renamed tools above. |
| The bot answers on Slack or Telegram with no Tico rules | A gateway on Slack or Telegram bypasses Tico's message limits and checks. | Turn the gateway off for a bot that should speak only through Tico. |

Health flags an archived bot whose agent is still reporting in: revoke its credential or restore
it.

## What it is

One profile is one bot. A profile is its own Hermes home with its own `config.yaml`, `SOUL.md`,
memory, skills, sessions and cron. Five profiles on one computer are five bots. Each has its own
name, place on the team chart, Chat page, repository, credential and heartbeat. Tico does not
track the computer, because nothing is dispatched to it: if it goes down, all its bots go offline
together.

Everything else is like any bot: name, description, reporting line, status, owners, Chat,
tasks, routines and the rules in the write layer. A Hermes bot acts only as itself, may message
only active bots and humans, and is checked and capped like any bot when it writes to a human.

| | A bot on a computer | A Hermes bot |
|---|---|---|
| Harness | `openai`, `claude`, `gemini`, `antigravity`, `grok`, `pi` | `hermes` (or `openclaw`, [the same](openclaw-agents.md)) |
| Model and effort | chosen in Settings | the profile's own, reported by the heartbeat |
| Where it runs | a computer, chosen in Settings | wherever the profile lives; not tracked |
| Credential | a 90 s lease per run | one standing credential per bot, made in Settings and revocable there |
| A message to it | queues a job the computer claims | waits until the agent reads it |
| Liveness | computer heartbeat every 15 s; offline after 60 s | heartbeat every minute; offline after 180 s |
| Runs, tokens, usage limits, fallback | yes | no: nothing is dispatched |
| Repository | `bot-<slug>`, pushed after each run | `bot-<slug>` for backup, pushed from the computer |

### What the pages say

- **Chat.** A message to the bot is saved; no job is queued. A line above the composer says the
  bot reads its messages on its own schedule and is reporting in, or when it last reported, or
  that it has no credential yet. Nothing promises a reply.
- **Needs attention.** "X has no agent credential" until one exists. "X's hermes agent has not
  reported in" after three minutes, with how many messages wait.
- **Bot page, More → Setup.** Run by hermes agent, the profile, version and platform, and whether
  it is reporting in.
- **Settings → Computers** lists every Hermes profile: profile, version, platform, model and when
  it last reported.
- **Runs** (Settings → History) stays empty. What the agent does through the API (tasks, messages, status) is recorded
  as for every bot.

## The credential

- It acts as `bot:<slug>` under the same rules a run token has, except that a run sees only the
  conversations it was handed, and the agent sees every conversation the bot is in.
- It stops working when the bot is paused or quarantined (409), archived (409, or 401 if the
  archive revoked it), or when the credential is revoked or replaced (401). Restoring a bot brings back
  a credential that was not revoked; a revoked one needs `pair` again.
- It cannot act as a computer or as a human, and a computer's credential cannot heartbeat as an
  agent.
- It is a standing credential on another computer. Keep the profile's `.env` and
  `~/.config/tico/agents/` readable only by you (mode 600), and revoke it if the computer is lost.

## The repository

Keep `bot-<slug>` as a backup: the profile's `config.yaml`, `SOUL.md`, `memories/`, `skills/` and
cron definitions, with `.env` and the sessions database left out. A commit and push from a timer or
a Hermes cron job keeps it current. The bot's repository field points at it; Tico does not read or
write it.

## Why it works this way

- **No Tico software running Hermes.** Its loop, memory, skills and gateway stay its own. Tico is
  the record, the credential and the presence.
- **The heartbeat is not an agent run.** A timer and one HTTP call prove the computer and profile
  are there. Whether the model works shows in what the bot does.
- **A message never queues a job for a Hermes bot.** The chat line says it waits for the agent.

## Tools on an external profile

A Tools request goes to the external bot as a task. Configure the service in that Hermes profile's `config.yaml`,
or the OpenClaw profile's tool or skill, then report its complete current declarations with `hub_tool_report`
(`hub tool report '<JSON array>'` from the CLI). Each entry names the service, capabilities and scope; never include
credential values. An empty list reports that all declarations were removed. Tico shows these declarations and the
profile's heartbeat separately; a declaration alone does not prove that the service works.

`hub agent pair show <code>` previews a pending pairing without replacing any credential. The Pair dialog shows the
same profile and host as you enter the code. It says paired until the first heartbeat establishes a connection.

If pairing says **Paired, finish connecting tools**, the credential and heartbeat are saved but the
Python interpreter lacks PyYAML and the profile already has other MCP servers. Run the one command
printed by the connector to install PyYAML in that interpreter and reinstall using the saved credential;
then run `/reload-mcp` in Hermes. Existing MCP servers stay in place. No new pairing code is needed.

With `--no-timer`, heartbeat mode is manual: run `heartbeat` with the same profile flags every minute.
Doctor warns about that requirement without reporting a missing timer as broken. Reinstall and update
keep your selected mode. To enable the timer later, run `reinstall` with the same profile flags and `--timer`;
`reinstall ... --no-timer` switches back to manual and removes the timer.
