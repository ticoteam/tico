# External agent sync: Grok Bot and Dots

Bots a human runs on another platform, shown on the team chart under that human, with their history and
instructions kept in Tico in case the account goes away:

| Platform | What it is | On the team chart |
|---|---|---|
| **Grok Bot** (grok.com/bot) | xAI's cloud agents, any number on one account | A **Grok Bot** cluster under the human, with Grok's own sections (Pinned, and any section made there) nested inside |
| **Dots** | OpenAI's always-on agent, one per account | A single **Dots** row under the human |

Neither platform has an API, export or webhook for these bots. The agent itself can call Tico's MCP tools as its
human on a routine, so the sync runs on the platform, with the human's own sign-in, and Tico only receives
(`backend/external_sync.py`).

## What Tico does with a sync

- A bot it has not seen becomes a bot with the `grokbot` or `dots` harness, **under the human who synced it**
  (`reports_to: human:<them>`), active, with them as owner. Move it anywhere on the team chart afterwards and it
  becomes an ordinary row there; a later sync never moves it back. It also joins its human's group (a bot reporting to a
  human does not inherit their group on its own), until someone puts it in another group or none.
- Any human may link their own bots. A new bot counts toward a member's bot limit, like any bot they add.
- Name, description and section follow the platform. The full instructions are kept in the bot's
  `config_json.grok.instructions` (Dots: `config_json.dots`), with when they last changed, so the bot can be rebuilt
  on another runtime. Sending an explicit empty description or instructions clears it; leaving either field out
  preserves it.
- The transcript is copied into the human's own chat with the bot, already read, with no job queued. Resending a
  message adds nothing. Each bot in the reply carries `synced_through`: send only newer messages next time.
- A sync with `inbox: true` (`hub_external_sync` sends it) also gets `inbox` on each bot: what the human wrote to it
  in Tico since the last sync, oldest first, given once and then counted as delivered. The routine hands those to
  that bot; its answer comes back with the next transcript. Without `inbox: true` (a routine set up before, on
  `hub_grokbot_sync`), `inbox` is empty and the messages keep waiting. Messages from other humans stay in Tico.
- Images in a message are fetched once (public https only, up to 10 MB each), stored like a chat attachment and
  shown inline. One Tico cannot fetch stays in the message as a link, as do links past the first 50 in one sync.
  Nothing is fetched for a sync Tico would refuse (a bot, a malformed body, or new bots past the member's limit).
- Grok Bots named "Tico …" in Grok read "Grok …" here: in Grok the word marks the Bot as one of ours; here it says
  where the bot runs. The Grok name is kept in `config_json.grok.name`.
- Presence is the last sync: synced in the last 26 hours shows as synced, older as not synced lately. Nothing is
  dispatched to these bots.

The tool is `hub_external_sync` with `provider` `grokbot` or `dots` (`POST /api/v2/external/sync`;
`hub external sync --provider … --file …`). `hub_grokbot_sync` and `POST /api/v2/grokbot/sync` are the same with
`provider: grokbot` and no inbox, for routines set up before.

## Grok Bot

### Connect it (once per human)

1. In Tico, use [Connect an external agent](connect-an-agent.md) beside your email and pick **Grok**, or create a
   token under **Settings → Computers → API tokens**.
2. In Grok, tell one of your Bots (for example Groky) to add a custom MCP server with the dialog's block: the
   **MCP server URL** and the header `Authorization: Bearer <that token>`. Grok adds it to the whole account, so
   every Bot can use the Tico tools. Type the token yourself.
3. Paste the routine below into that Bot and ask it to run it once now, then daily.

### The routine

> **Tico sync.** Use the Tico MCP tools.
> 1. List every Bot on my account, including yourself, and the section each is in on my Bots list (Pinned, a
>    section I made, or none).
> 2. Call `hub_external_sync` with `provider` `grokbot`, those Bots and no messages, each with `id` (the id in
>    grok.com/bot/<id>), `name`, `description`, `section` (empty when none) and the full `instructions` verbatim,
>    and `source` set to your own name.
> 3. For each Bot, read its conversations and collect every message newer than the `synced_through` Tico returned
>    for it (all of them if it is empty), oldest first, as `role` (user or bot), `text` verbatim, `at` (ISO time)
>    and `id` if Grok gives one, and `images` for any image in the message: its https `url`, or for a file on your
>    computer `content_base64` (under 5 MB) with a `name`. Send them with `hub_external_sync`, at most 200
>    messages per Bot per call, repeating until all are sent.
> 4. For each Bot with `inbox` messages in any of those replies, give them to that Bot as from me, in order (each is given only once).
> 5. Tell me in one line per Bot what Tico added and what you passed on. Change nothing else in Grok.

## Dots

Dots reaches outside tools through ChatGPT plugins, which sign in with OAuth. Once your Dots can call Tico's MCP
server ([Connect an external agent](connect-an-agent.md#each-agent)), give it this routine to run daily:

> **Tico sync.** Use the Tico MCP tools.
> 1. Call `hub_external_sync` with `provider` `dots`, one bot with `name` (your name), `description` and your full
>    `instructions` verbatim, and the messages of our conversation newer than the `synced_through` Tico returned
>    (all of them the first time), oldest first, as `role` (user or bot), `text` verbatim and `at` (ISO time). At
>    most 200 messages per call; repeat until all are sent.
> 2. Treat each `inbox` message in the reply as from me, in order.
> 3. Tell me in one line what Tico added.

Dots needs no `id`: a human has one Dots, and a later sync updates the same bot.

## Recovering a bot

The bot's Tico chat holds its history and `config_json.grok.instructions` (or `config_json.dots.instructions`)
holds its instructions. To bring a Grok Bot back, paste those instructions into a new Grok Bot (or share the old
one to another account, which copies its instructions but not its history), or move the Tico bot to the `grok`
runtime on a registered computer with the same instructions.
