# Grok Bot sync

A human's Grok Bots (xAI's cloud agents, grok.com/bot) as bots on the team chart, with their
history and instructions kept in Tico in case the Grok account goes away.

Grok Bot has no API, export or webhook for its Bots. One of the human's own Bots can list their
other Bots, read each one's full instructions and transcripts, call a custom MCP server, and do
it on a routine. So the sync runs inside Grok, with the human's own personal token, and Tico
only receives (`backend/grokbot.py`).

## What Tico does with a sync

- A Grok Bot it has not seen becomes a bot with the `grokbot` harness, **under the human who
  synced it** (`reports_to: human:<them>`), active, with them as owner. On the team chart it is a chip
  beside their name (one by name, then "+N") rather than a row under them. Move it
  anywhere on the team chart afterwards and it becomes an ordinary row; a later sync never moves it back.
- Name and description follow Grok. The full instructions are kept in the bot's
  `config_json.grok.instructions` (with when they last changed), so the bot can be rebuilt on
  another runtime. Sending an explicit empty description or instructions clears it; leaving either field out preserves it.
- The transcript is copied into the human's own chat with the bot, already read, with no job
  queued. A message's id includes the syncing human, Grok Bot and message ID, or the full text and attachments when no message
  ID is supplied. Resending it adds nothing; similar long messages and another human's import stay distinct. Each bot in the reply carries `synced_through`: send only newer messages next time.
- Images in a message are fetched once (public https only, up to 10 MB each), stored like a
  chat attachment and shown inline. One Tico cannot fetch stays in the message as a link.
- Grok Bots named "Tico …" in Grok read "Grok …" here: in Grok the word
  marks the Bot as one of ours; here it says where the bot runs. The Grok name is kept in
  `config_json.grok.name`.
- Presence is the last sync: synced in the last 26 hours shows as synced, older as not synced
  lately. Nothing is dispatched to these bots; a message written to one in Tico waits
  unread (relaying it back into Grok is not built yet).

Only the owner and bot administrators may sync.

## Connect it (once per human)

1. In Tico, **Settings → Computers → API tokens**: create a token labelled "Grok Bot sync", or use
   [Connect an external agent](connect-an-agent.md) beside your email.
2. In Grok, tell one of your Bots (for Ana: Groky) to add a custom MCP server:
   Use the **MCP server URL** from Tico's dialog (normally `https://tico.example.com/api/v2/mcp`),
   header `Authorization: Bearer <that token>`. A separate hostname is needed only when an external sign-in proxy blocks agent access;
   [proxy setup](connect-an-agent.md#behind-cloudflare-access-or-another-sign-in-proxy) explains the options.
   Grok adds it to the whole account, so every Bot can use the Tico tools. Type the token
   yourself.
3. Paste the routine below into that Bot and ask it to run it once now, then daily.

## The routine

> **Tico sync.** Use the Tico MCP tools.
> 1. List every Bot on my account. Keep the ones whose name, description or full instructions
>    contain "tico" in any capitalisation. Do not include yourself unless you match.
> 2. Call `hub_grokbot_sync` once with those Bots and no messages, each with `grok_id` (the id
>    in grok.com/bot/<id>), `name`, `description` and the full `instructions` verbatim, and
>    `source` set to your own name.
> 3. For each Bot, read its conversations and collect every message newer than the
>    `synced_through` Tico returned for it (all of them if it is empty), oldest first, as
>    `role` (user or bot), `text` verbatim, `at` (ISO time) and `id` if Grok gives one, and
>    `images` for any image in the message: its https `url`, or for a file on your computer
>    `content_base64` (under 5 MB) with a `name`.
>    Send them with `hub_grokbot_sync`, at most 200 messages per Bot per call, repeating until
>    all are sent.
> 4. Tell me in one line per Bot what Tico added. Change nothing in Grok.

## Recovering a bot

The bot's Tico chat holds its history and `config_json.grok.instructions` holds its
instructions. To bring it back, paste those instructions into a new Grok Bot (or share the old
one to another account, which copies its instructions but not its history), or move the Tico
bot to the `grok` runtime on a registered computer with the same instructions.
