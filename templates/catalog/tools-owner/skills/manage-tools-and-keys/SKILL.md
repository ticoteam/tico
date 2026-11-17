---
name: manage-tools-and-keys
description: "Add a tool (API, website login, MCP server), get its key stored without seeing it, propose who gets Read or Read and write, and fix a key that is rejected, duplicated or never reaches a run. Read before any tool or key work."
---
# Manage tools and keys

When to use: a person or bot asks to connect a service, store or rotate a key, give a bot access, change a level, or
says a tool stopped working; a message names a key as `[saved NAME]`; your weekly review finds a problem.

## What you can do, and who does the rest
When a person asks you in their own chat, or on a task they (or their Assistant, for them) gave you, **do it**: on a
Tools change your calls carry that person's rights, recorded as theirs "via Tools Owner" with your name on it. You never
see, type or repeat a value. Never answer "I can't" or file the step back to them without saying what you did or what
you will do instead.

| Step | Who | How |
|---|---|---|
| See every tool, its status and who holds which level (never a value) | you | `tico tool index` (MCP `tico_tool_index`); `tico tool list --bot <bot>` for one bot's tools, declared and granted |
| Check a tool's keys now | you | `tico tool check <id>` (MCP `tico_tool_check`) |
| Get a key typed in for a bot | the person, on your card | `tico credential request <VAR> --for-bot <bot> ...` |
| Grant keys a person dropped in your chat, by tool, level and bots | the person, on your card | MCP `tico_tools_propose_grant` |
| Give a bot a key, set its level, take it back | you, as the person who asked | `tico tool level <tool> <bot> <read, read_write or none>`; `tico credential grant <key> --to <bot>` and `tico credential revoke <key> --from <bot>` for a key on no tool |
| Add or change an API tool, its settings, an MCP server (sign-in), share a website account with a bot | you, as the person who asked | Tools routes; the person's own rights decide |
| Notes on a bot's tool (a `bot.yaml` `tools:` entry: identity, scope, note), never needed for access | you (any bot), or the bot itself | `tico tool add <bot> ...` / `tico tool update`; BotOps commits it |
| A key's value, its reveal, deleting a tool or key, a person's own access | the person, in Tools | link the exact page and say what to click |

The person's rights are the ceiling: a member who may not manage that bot is refused, and the refusal names who can.
Give keys and levels to the bot that needs them, never to yourself. With no person behind your run (your weekly review),
your own rights apply: propose instead, on a card or a task to the person who can.

A grant is all a bot needs. A key, API or MCP tool, setting or website account granted to it reaches its runs and shows
on its Tools tab (`tico tool list --bot <bot>`, marked granted) with no bot.yaml entry, MCP servers included. Never ask
anyone to register or declare a tool as a step to access, and never file a task for one: `tico tool add` for a tool the
bot already holds answers with the grant and files nothing. Read that list before you say a bot "has nothing connected".

## Words
- **Tool**: a service with an address. An **API tool** (`/api/v2/api-tools`) has a base URL, an auth scheme (`bearer`,
  `basic_user`, `basic_password`, `header:<Name>`, `query:<param>`) and a **check path**; a known service fills these in.
  A **website tool** (`/api/v2/website-tools`) has sites and accounts.
- **Key**: a vault credential on a tool, at most one per level: a **Read** key and a **Read and write** key. Its
  **variable** (`env`, like `LINEAR_API_KEY`) is the name a run reads it under.
- **Level** on a tool, per bot or person: Read uses the Read key when there is one, else the Read and write key at Read;
  Read and write uses the Read and write key. Setting a level grants the one key it uses and takes the others away.
- **Delivery**: `proxy` (default when the tool has an address; Tico holds the key) or `computer` (the value goes into
  the run's environment, as every key stored before Tools did).

## 1. Find what exists first
Read `tico tool index` and the tool's page before asking anyone for anything. If the service already has a tool with a
stored key, the bot needs a **level**, not a new key: a second key with the same value is a duplicate (section 6).

## 2. Connect a service: pick the route
How each kind of sign-in (API key, Basic, a header, OAuth, a login, a personal access token, ...) is handed to Tico
today is in `docs/auth-types.md` (the manual: `tico doc read manual:auth-types`); read it when the auth is not a plain key.
1. **An API with a key** → an API tool. A credential admin adds it in Tools → Add → API (name, address, check path), or
   a card's key lands on one by itself: a stored key goes to the first tool of its service with no Read and write key,
   else a new tool is made for it, on the proxy when Tico knows the service's address.
2. **A website people sign in to** (no API, or the work is on its pages) → a website tool: Tools → Add → Website, its
   sites (`app.example.com`; subdomains count) and accounts. A **Shared** account (a credential admin adds it; All bots
   by default) or an **Own** account (one person's or one bot's). Bots never see the password or the 2FA key: they use
   `tico browser open <site>` and `tico login fill <site> username|password|code`. Blank username or password (SSO, an
   emailed code, a passkey) means a person signs in through the live view (`tico browser needs-you`).
3. **The vendor's own remote MCP server** → an MCP tool: Tools → Add → MCP server (its address, then a pasted token or
   its sign-in), and a level for each bot that uses it. Prefer the vendor's server to a script.
4. Otherwise a small REST skill in the bot's own repository that reads the key from its variable (BotOps writes it).

## 3. Get the key stored without seeing it
- **A card** for the bot that needs it (you may name any bot but yourself):

      tico credential request LINEAR_API_KEY --for-bot <bot> --label "a Linear API key" \
          --format "lin_api_..." --help-url https://linear.app/settings/api

  `--label` finishes "<Bot> needs ..."; `--format` is the shape (`you@example.com:API token` for Basic). On a task, the
  card goes in its thread and the task is Paused until it is saved; you are woken with "<label> is saved. Continue."
  (in chat: "Saved <label>. Test the connection now."). Asking again on the same task returns the open card. Only a
  credential admin, or a person who manages that bot, can save it. Say in one line that a field is open; never ask for
  the value in words.
- **The Tools chat's key drop**: a credential admin pastes keys into your chat on the Tools page. The page saves each
  value to the vault first and you get `[saved NAME]` in its place. Propose where each goes:

      tico_tools_propose_grant {thread_id, rows: [{key: "NAME", tool: "<tool id or name>", level: "read", bots: ["<slug>"]}], note}

  1 to 20 rows, 1 to 10 named bots each; never yourself, every bot or every computer. The server builds the card; only
  the person's Grant attaches each key to its tool at that level, grants the bots, then runs the tool's check and writes
  "Connected to <tool>" or "Didn't work: <reason>" on the row. A key that replaces the tool's key at that level keeps
  every holder's access on the new key; the card names who keeps and who loses it. The tool must exist first.
- **A key in plain text** (pasted anywhere else): do not repeat or use it. Tell the person in one line it should be
  rotated, and open a card (or point them to your Tools chat) for the new value.

## 4. Grant by level
- One shared key per value, granted to many bots. Never a copy per bot, never the same value stored twice.
- One value per variable per bot: a bot can hold only one key under `LINEAR_API_KEY`.
- Read unless the bot's job writes and a person agrees to Read and write. With no Read key, a Read bot's proxied calls
  are limited to GET, HEAD and OPTIONS (and MCP calls that only read, where the proxy carries MCP).
- When the person asked for it, set the level yourself (section "What you can do"). Otherwise link the tool's row
  (`href` in `tico tool index`) and name the bot and level in one line.

## 5. How the key reaches the bot, and what the bot must do
- **Proxy** (lock on the row): the run gets a proxy token in the key's variable and the proxy's address in
  `TICO_TOOL_<ID>_URL` (`TICO_TOOL_CLOSE_CRM_URL` for `close-crm`). The bot calls `$TICO_TOOL_<ID>_URL/<vendor path>`
  with the token where the key goes, or `tico tool call <id> GET /<path>`. The token works only while that run is live.
- **Computer**: the value is in the run's environment under its variable; anyone who controls that computer can read
  it. A credential admin moves it with Move to proxy on the tool's page once the tool has an address.
- **MCP server**: an MCP tool in Tools. A bot with a level on it gets its server in each run (through the proxy when
  Tico holds its key) with no bot.yaml entry. Only a server Tools does not hold (an older `sse` one) is a `bot.yaml`
  entry with an `mcp:` block, which you register (`tico tool add`) and BotOps commits:

      tools:
        - service: linear
          mcp: {url: "https://mcp.linear.app/mcp", transport: http, headers: {Authorization: "Bearer ${LINEAR_API_KEY}"}}
          can: [read]
          env: LINEAR_API_KEY
          note: "who approved it; what is out of bounds"

  Raygun, for example: `url: "https://api.raygun.com/v3/mcp"`, `transport: http`, `headers: {Authorization: "Bearer
  ${RAYGUN_PAT}"}` with a Raygun personal access token; it answers as an SSE stream, which streamable HTTP (`http`) carries.
  `url` is https; `transport` is `http` or `sse`; a header may use `${VAR}` for the entry's own `env` and nothing else,
  never a value. The command BotOps runs: `tico tool add <bot> linear --can read --env LINEAR_API_KEY --mcp-url <url>
  --transport http --header 'Authorization: Bearer ${LINEAR_API_KEY}'` (`tico tool update <tool-id> --bot <bot>` to
  change one; never remove and add). Claude Code, Codex, Gemini CLI, Grok Build and Muse Code take MCP servers (Codex
  and Muse Code http only); Cursor, Antigravity and pi do not.
- `can:` describes intent only; the key's own vendor scope or a Read key enforces it.

## 6. Merge duplicates
Two keys with the same value (often one per bot from older imports, `RAYGUN_API_KEY (Ops)` and `RAYGUN_API_KEY (Support)`)
are one key. Keys with different values stay separate even with one variable (production and staging). To merge, for
a credential admin or BotOps as them: keep one key on the tool; for each other bot, take the copy away first (a run
takes one value per variable, so granting before revoking is refused), set the bot's level on the kept key, then delete
the copy. You cannot compare values: you find candidates by service, variable and name, and the person confirms they
are the same key.

## 7. Checks
A check is one authenticated GET to the tool's check path (a known service's own when the tool has none) with each
key: 2xx **Working**, 403 **Works (limited)** (the key works; the path needs a scope it lacks, nothing to fix), only 401
**Key rejected** (and Vercel's 403), anything else **Check failed**. A proxied tool's real calls outrank its check: a
2xx in the last 24 hours is Working, and 3 calls in a row answered 401 with no 2xx since is Key rejected. Every tool with
a check path is checked every 30 minutes, its key on the proxy or on computers. Other states: **No key**, **Key
expired**, **Expires in N days** (14 or fewer), **Not checked** (no check path). A tool with no check path: propose
one, a cheap authenticated GET such as `/me` or `/v1/user`. An id (`*_PROJECT_ID`, `*_ACCOUNT_SID`) is a setting, never
a key: put it under Settings.

## Troubleshooting
- **409 `env_in_use`**: the bot already gets that variable from another key. Same value: it is a duplicate (section 6).
  Different value: a person decides which the bot keeps, takes the other away, then grants.
- **Key rejected**: the vendor refused it (revoked, expired, wrong account, wrong format such as `email:token` for
  Basic). Open a card for the bot whose key it is, or ask for a new drop in your chat; the new value replaces the old.
  Check again after it is saved.
- **Check failed**: the address or check path is wrong, the vendor is down, or the address is not public (the proxy
  only sends to public https addresses). Read the check's detail before blaming the key.
- **Proxy 403 "<bot>'s level on <tool> is Read: only GET calls"**: the bot holds Read and the tool has no Read key. Add a
  read-only key at Read, or a person sets Read and write. **403 `tool_access` "has no access"**: no level; **"has no stored
  key"**: the tool has none. **401 `token_ended`**: the token's run is over; the next run gets a new one.
- **Turn diagnostics "<service>: MCP server not passed, <VAR> is not granted to this bot"**: bot.yaml names a server
  whose variable the bot holds no key under, and no granted MCP tool of that name stands in: give the bot a level on the
  MCP tool (or grant the variable). **"<tool>: MCP server not passed, its key did not reach this turn"**: the grant's
  key is not stored or not delivered: check the tool. **"MCP server not passed. <harness> cannot use it"**: the bot's
  harness takes no MCP servers (or not that transport): move the bot or use the REST route.
- **The bot's tool shows "pending"**: BotOps has not committed the bot.yaml entry yet, or the computer has not reported it.
- **422 `looks_like_key`** on a message to you: the person's text still held a key. Tell them to drop it in your chat
  so the page saves it first.
- **A card refused, 403**: only BotOps, the Tools page's owner and a person's own Assistant may ask for another bot's key,
  and never for themselves. **409 `no_person`**: nobody who can store it can read the task; ask in a person's chat instead.
- **A `tools:` entry's scope**: any short lowercase name (`page`, `ad_account`, `database`) with a short value or a list.
  A value or name that looks like a credential is refused: store it as a key and grant it.
