# Slack

Talk to your bots in Slack. DM Tico, or `@Tico` in a channel it has been invited to, and the message
reaches the bot it is for; the reply comes back in the same thread under that bot's name.
How messages are routed and stored is in [slack-gateway.md](slack-gateway.md).

## Set it up (about five minutes)

1. In `.env` add `slack` to `COMPOSE_PROFILES` (for example `caddy,updater,slack`), then `docker compose up -d`.
   This starts one extra container from the same image. It only makes an outbound connection to Slack
   (Socket Mode): no port to open and no public URL.
2. At <https://api.slack.com/apps> choose **Create New App > From a manifest**, pick your workspace, and paste
   `connectors/slack-app-manifest.json` (or use **Copy the manifest** in Tico under Tools > Slack).
3. **Install App** to the workspace and copy the **Bot User OAuth Token** (`xoxb-`).
4. **Basic Information > App-Level Tokens > Generate**, scope `connections:write`, and copy it (`xapp-`).
5. In Tico, Tools > Slack, paste both tokens. They are stored encrypted in Tico's database
   and never shown again. Do not put them in `.env`.

Within a minute the Slack card on Tools and Settings > Health say **Connected**. The first connection pins the workspace
the tokens belong to; to pin it in advance, set `SLACK_TEAM_ID` in `.env`. To change workspace, disconnect and
paste the new tokens. Invite Tico to a channel with `/invite @Tico`.

## Choose the channels bots use

Tico, invited to a channel, stores what is said there. Which bots read it, and whether bots may post in it, is a
list in Tico's database, not a file:

- **In the app.** Tools > Slack channels (an owner or an admin): the channel (`#customer_success` or its id),
  the bots that read it, whether bots may post (on unless you turn it off), and a note. A channel listed by name gets
  its id from Slack once Tico is in it.
- **With `hub`.** `hub slack channel add '#customer_success' --reader support`, `hub slack channel list`,
  `hub slack channel remove '#customer_success' [--reader BOT]`.
- **By asking BotOps.** "Let the support bot read #customer_success": BotOps adds it as the person who asked,
  so a member is told who to ask. The MCP tools are `hub_slack_channel_add`, `_list`, `_remove` and `_import`.

A reader gets what is new in the channel about once an hour, as a message in its own conversation
([slack-gateway.md](slack-gateway.md)). A bot's own `history` reads through `connectors/slack.py` follow the same
list and its `tools:` entry. Channels shared outside the workspace are always refused, whatever the list says.

**An older install** has `registry/slack-channels.yaml`. It is still read, and its channels show in the list marked as
coming from the file, until an owner or an admin presses **Import** (or runs `hub slack channel import`). After that the
file is ignored. A public install has no such file and needs none.

## What humans can do

- DM Tico to ask for anything; the router picks the right bot, or asks one clarifying question.
- Ask BotOps for a change in a DM: it acts with your rights, as in your Tico chat. A request in a channel or thread
  only gets an answer; BotOps changes nothing for you from there.
- `@Tico` in a channel; mention it again in the thread to continue.
- Reply in a thread a bot already talks in; no mention needed.
- Only humans on the roster (Humans) whose Slack email matches can wake a bot. Guests and Slack Connect
  channels are refused.

## Troubleshooting

| Tools card or Settings > Health says | Do this |
|---|---|
| Tokens saved, waiting | The service is not running: `slack` is missing from `COMPOSE_PROFILES`, or run `docker compose logs slack`. |
| `invalid_auth` | The bot token is wrong or was revoked; reinstall the app and paste the new token. |
| Socket Mode did not connect | The app-level token is wrong or lacks `connections:write`, or Socket Mode is off (the manifest turns it on). |
| Missing scopes in `docker compose logs slack` | Paste the current manifest over the app's, then reinstall it. |
| No reply in a channel | Tico must be invited to it, and the sender must be on the roster. |
| A bot is refused a channel ("not on the team's Slack channel list") | Add the channel and the bot as a reader under Tools > Slack channels, or ask BotOps. |
| Replies show as "Tico", not the bot | The `chat:write.customize` scope is missing; update the manifest and reinstall. |

Kill switch: remove `slack` from `COMPOSE_PROFILES` and run `docker compose up -d --remove-orphans`.
The server, its data and the saved tokens are untouched.

## Task results

When a bot or another human finishes or declines a task you requested, Tico DMs you:
`Finished: <title>` (or `Declined: <title>`), `by <owner name>`, the first line of the completion
note if there is one, and **Open task**, a link to the task in Tico. It uses your Tico DM even
when you created the task in the app. Each task sends each result once, including if it is reopened.

This is on by default for humans linked to Slack. On your human page, under **Profile → Notifications**,
turn off **Task results in Slack**. You can also ask BotOps to turn it off for you, or use
`POST /api/v2/humans/<id>` with `{"notify_slack_task_done": false}` (through the API or MCP's `hub_api`).
Turning it on again uses `true`. Self-completed tasks and quiet updates do not send a DM;
no linked Slack account or a disabled gateway means no Slack delivery. In-app notices continue.

## Bot messages

A bot's message to you is also copied into your Tico DM in Slack, including the report a bot writes
at the end of a run. Under **Profile → Notifications**, turn off **Bot messages in Slack** to keep every
bot's messages in Tico, or mute only some bots in the **Muted** row under it. Through the API, send
`{"notify_slack_bot_messages": false}` or `{"slack_muted_bots": ["<bot>", ...]}` (the whole list) to
`POST /api/v2/humans/<id>`.

While you are talking to a bot in Slack, it still answers there: when your latest message in that
chat came from Slack within the last day, the bot's messages in it go to Slack. Task results have
their own switch above.
