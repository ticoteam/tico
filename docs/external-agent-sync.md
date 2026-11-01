# External agent sync: Grok Bot, Dots and Muse

> **Beta**: external agents (Hermes, OpenClaw, Grok Bot, Dots and Muse sync) work today and may still change. See [Beta features](support.md#beta-features).

Bots a human runs on another platform, shown on the team chart under that human, with their history and
instructions kept in Tico in case the account goes away:

| Platform | What it is | On the team chart |
|---|---|---|
| **Grok Bot** (grok.com/bot) | xAI's cloud agents, any number on one account | One ordinary row per Bot, initially under the human; place each Bot anywhere on the org chart |
| **Dots** | OpenAI's always-on agent, one per account | A single **Dots** row under the human |
| **Muse** | Meta's always-on agent, one per account | A single **Muse** row under the human |

The agent itself calls Tico's MCP tools as its human on a routine, so the sync runs on the platform, with the human's
own sign-in (`backend/external_sync.py`). Grok Bot also supports [HTTP-triggered routines](https://cursor.com/help/grok-bot/routines),
but Tico does not dispatch webhook calls through this sync integration.

## What Tico does with a sync

- A bot it has not seen becomes a bot with the `grokbot`, `dots` or `muse` harness, **under the human who synced it**
  (`reports_to: human:<them>`), active, with them as owner. Every synced Bot is an ordinary movable row. Move it anywhere
  on the team chart afterwards; a later sync never moves it back. It also joins its human's group (a bot reporting to a
  human does not inherit their group on its own), until someone puts it in another group or none.
- Any human may link their own bots. A new bot counts toward a member's bot limit, like any bot they add.
- Name, description and source section follow the platform. The source section is retained as metadata; it does not
  create groups, reorder Bots or override reporting relationships on Tico's org chart. The full instructions are kept in the bot's
  `config.grok.instructions` (Dots: `config.dots`), with when they last changed, so the bot can be rebuilt
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
  where the bot runs. The Grok name is kept in `config.grok.name`.
- Presence is the last sync: synced in the last 26 hours shows as synced, older as not synced lately. Nothing is
  dispatched to these bots, except a Grok Bot [delivered through the Grok Mac app](#deliver-through-the-grok-mac-app).

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

### Deliver through the Grok Mac app

The daily routine makes a message wait up to a day. With app delivery on, a Grok Bot is a bot like any other in Tico:
a message to it reaches it within a minute, it shows as working while it answers, its reply streams into its Tico
chat, and what it says on its own in Grok (a scheduled result, a follow-up) shows up there within a few minutes.

It runs on a Mac of yours that stays signed in, with the Grok app installed and signed in to your account:

1. Run the native runner on that Mac (the "Use a Tico checkout instead" install), not the Docker one: Docker cannot
   see Mac apps. `tico-appdriver` ships beside `tico-runner`.
2. Give the runner Accessibility permission when **Settings → Computers →** that Mac shows **Desktop apps: Grok —
   Needs Accessibility permission**: System Settings → Privacy & Security → Accessibility, and turn on the entry that
   row names. The row reads **Ready** once Grok is installed, signed in and answers in the background.
3. In the bot's settings, set **Deliver through the app on** to that Mac. Only the person who synced the bot can set
   it, and only to a computer of theirs where Grok is ready. Clear it to go back to the daily sync.

The runner works only through macOS Accessibility: it sets the Grok Bot's message box and presses Send, then reads
the reply. It never moves the pointer, types through the keyboard or brings Grok to the front, so you can keep using
the Mac. It presses nothing but the bot's row, the message box and Send. When a reply asks for something only you can
do in Grok (an approval, a sign-in, a payment), Tico asks you instead. When Grok is signed out, missing its
permission, or changed its layout, Health names the problem for that Mac and the bot's chat says why the message
didn't go; the Health line goes once the runner next reads the conversation without a problem.

The routine keeps working and stays optional; with app delivery on it is only needed to copy the bot's instructions
and section. A message delivered through the app is not handed to the routine again, and the routine does not copy
the app exchange a second time. xAI's terms may not allow automated use of its app; turning this on is the account
owner's call.

## Dots

Dots reaches outside tools through ChatGPT plugins, which sign in with OAuth. Add Tico's MCP server URL to your Dots as
a connector and press **Allow** when Tico asks ([OAuth sign-in](connect-an-agent.md#oauth-sign-in)); then give it this
routine to run daily:

> **Tico sync.** Use the Tico MCP tools.
> 1. Call `hub_external_sync` with `provider` `dots`, one bot with `name` (your name), `description` and your full
>    `instructions` verbatim, and the messages of our conversation newer than the `synced_through` Tico returned
>    (all of them the first time), oldest first, as `role` (user or bot), `text` verbatim and `at` (ISO time). At
>    most 200 messages per call; repeat until all are sent.
> 2. Treat each `inbox` message in the reply as from me, in order.
> 3. Tell me in one line what Tico added.

Dots needs no `id`: a human has one Dots, and a later sync updates the same bot.

## Muse

Muse makes connectors when you ask it in a chat. Ask it to add a custom connector for Tico's MCP server URL and sign in
to Tico when it asks ([OAuth sign-in](connect-an-agent.md#oauth-sign-in)); if it wants a header instead, use a token
from [Connect an external agent](connect-an-agent.md). Then give it the Dots routine above with `provider` `muse`. Like
Dots, a human has one Muse, and it needs no `id`.

## Recovering a bot

The bot's Tico chat holds its history and `config.grok.instructions` (or `config.dots` / `config.muse`)
holds its instructions. To bring a Grok Bot back, paste those instructions into a new Grok Bot (or share the old
one to another account, which copies its instructions but not its history), or move the Tico bot to the `grok`
runtime on a registered computer with the same instructions.
