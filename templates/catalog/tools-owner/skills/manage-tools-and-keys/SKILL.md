---
name: manage-tools-and-keys
description: "Add a tool (API, website login, MCP server), get its key stored without seeing it, propose who gets Read or Read and write, and fix a key that is rejected, duplicated or never reaches a run. Read before any tool or key work."
metadata:
  function: tico
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
| A bot needs a level on a tool or repository that exists | Tico, or a person on its card | `tico access request <tool or owner/repo> --level read --for <bot> --why "..."` |
| Grant keys a person dropped in your chat, by tool, level and bots | the person, on your card | MCP `tico_tools_propose_grant` |
| Give a bot a key, set its level, take it back | you, as the person who asked | `tico tool level <tool> <bot> <read, read_write or none>`; `tico credential grant <key> --to <bot>` and `tico credential revoke <key> --from <bot>` for a key on no tool |
| Add or change an API tool, its settings, an MCP server (sign-in), share a website account with a bot | you, as the person who asked | the commands below; the person's own rights decide |
| Notes on a bot's tool (a `bot.yaml` `tools:` entry: identity, scope, note), never needed for access | you (any bot), or the bot itself | `tico tool add <bot> ...` / `tico tool update`; BotOps commits it |
| A person's key's value, a reveal, deleting a tool or key, a person's own access | the person | a card for the value; for the rest, link the exact page and say what to click |

The person's rights are the ceiling: a member who may not manage that bot is refused, and the refusal names who can.
Give keys and levels to the bot that needs them, never to yourself. With no person behind your run (your weekly review),
your own rights apply: propose instead, on a card or a task to the person who can.

A grant is all a bot needs. A key, API or MCP tool, setting or website account granted to it reaches its runs and shows
on its Tools tab (`tico tool list --bot <bot>`, marked granted) with no bot.yaml entry, MCP servers included. Never ask
anyone to register or declare a tool as a step to access, and never file a task for one: `tico tool add` for a tool the
bot already holds answers with the grant and files nothing. Read that list before you say a bot "has nothing connected".

## Every Tools change from the command line
Do the action yourself: the Tools page is never required. Each command calls the route the page calls, with the
person's rights; a refusal names who can. The MCP tools have the same names (`tico_tool_setting_add`).
- Read: `tico tool index`, `tico tool detail <tool>` (keys, levels, settings), `tico tool vendor <vendor>`,
  `tico tool website show <tool>`.
- API tools: `tico tool create <name> --service <id>` from the catalog (`tico tool templates` lists its `services`,
  each with its vendor's MCP server where it has one). You add a tool only that way, or a listed MCP server with `tico
  tool mcp add`: any other address, scheme or check path is refused to you and is the person's to add in Tools.
  `tico tool edit <tool>` with `--name`,
  `--delivery proxy|computer`, `--everyone-reads on|off`, `--audience <json>` or `--audience-everyone`; `tico tool delete
  <tool>`.
- Settings: `tico tool setting add <tool> <NAME> <value>`, `tico tool setting change <tool> <NAME> --value <value>`,
  `tico tool setting remove <tool> <NAME>`.
- Auth templates: `tico tool templates`, `tico tool template <tool> <template id>` (or `--none`), `tico tool ungroup
  <tool>`; a vendor account's values a bot holds in a file go on in one step with `tico tool paste <tool> --from-file
  <path>` (`tico tool classify --from-file <path>` first shows what each is, storing nothing).
- MCP servers: `tico tool mcp probe <url>`; `tico tool mcp add <name> <url> oauth`, then `tico tool sign-in <tool>` and
  send the person the address it prints (a token server's token comes from the person, on a card).
- Websites: `tico tool website add --site <host>`, `tico tool website edit <tool>`, `tico tool website delete <tool>`;
  `tico credential login share <tool> <account> bot:<slug> on|off`.
- Keys: `tico credential update <key> --level read` (or `--name`, `--expires-on`), `tico credential cancel <card>`. A key a
  bot already holds (one it made through the vendor's API) is stored with `tico credential add <name> --env <VAR>
  --from-env <SRC>` or `--from-file <path>`, plus `--tool <tool> --level read` and `--for-bot <bot>`: the value is read
  on that computer and never typed. Any bot stores one it holds itself the same way, with no `--for-bot`: it becomes
  that bot's own key, granted to it alone on a tool of its own under the vendor (Everyone can read off). Tell a bot that
  made a key to do that rather than waiting on you; giving its key to others stays a person's call. A person's own key
  always comes through a card.

## Words
- **Tool**: a service with an address. An **API tool** (`/api/v2/api-tools`) has a base URL, an auth scheme (`bearer`,
  `basic_user`, `basic_password`, `header:<Name>`, `query:<param>`) and a **check path**; a known service fills these in.
  A **website tool** (`/api/v2/website-tools`) has sites and accounts.
- **Key**: a vault credential on a tool, at most one per level: a **Read** key and a **Read and write** key. Its
  **variable** (`env`, like `LINEAR_API_KEY`) is the name a run reads it under. A tool with an **auth template** holds
  one key per variable per level (section 2b).
- **Auth template**: how a vendor account's several values are sent: its fields (each a variable: secret, id or
  setting) and its ways through the proxy (Basic with two fields, a header pair, query + header). Built in for Twilio,
  Google Ads, AWS, PostHog, ElevenLabs and Calendly; any other vendor's tool can take its own.
- **Level** on a tool, per bot or person: Read uses the Read key when there is one, else the Read and write key at Read;
  Read and write uses the Read and write key. Setting a level grants the one key it uses and takes the others away.
- **Delivery**: `proxy` (default when the tool has an address; Tico holds the key) or `computer` (the value goes into
  the run's environment, as every key stored before Tools did).

## 1. Find what exists first
Vendor guides, this company's notes and how to learn more: `skills/learn-tools/SKILL.md`.
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

## 2b. A vendor account with several values: one tool
Twilio's SID and secret, Google Ads' five values, AWS's key pair and region, PostHog's key, host and project id are one
**account**: one tool, never one tool per value. Its name is vendor and label ("Twilio", "PostHog · CRO").
- **New**: a credential admin pastes every value at once in Tools > Add > API (Keys box) or Keys > Paste on the tool.
  Tico names each as the template's field by its name or shape (or marks it secret, id or setting), and one click
  stores it: secrets as keys, ids and settings as Settings. In your Tools chat, a dropped value takes the template's
  variable when its shape fits one vendor (an `SK…` key is `TWILIO_API_KEY`); put an id or setting the person typed as
  words under the tool's Settings.
- **What the bot gets**: every field it holds as its own variable, unchanged in name. Through the proxy each secret is
  a proxy token and Tico builds the vendor's auth from all of them, so the bot calls as the vendor's docs say (Twilio:
  Basic with `$TWILIO_ACCOUNT_SID` as the user and its token as the password, at `$TICO_TOOL_TWILIO_URL/...`). AWS and
  Google Ads sign on the computer (SigV4; Google Ads' OAuth refresh in its client library), so their values go to
  computers together.
- **Grouped at update**: the tools that were one account's values became one tool, with every bot's variables kept
  exactly as they were; whoever held one part got the account's other values at their level, named in the tool's
  `tool.grouped` event. A sensitive vendor (AWS) or an Only listed tool where that would give someone a new value came
  to you as a task "Group <vendor>?": ask a credential admin whether each named bot should have those values; if yes,
  they set the levels; if not, leave the tools apart. Keys > Split (`POST /api/v2/api-tools/<id>/ungroup`) undoes a
  grouping.

## 2c. Login pairs: a sign-in stored as two keys
`VPM_TIDY_USERNAME` and `VPM_TIDY_PASSWORD`, `AAA_WEBFILE_*`, `CLICK2MAIL_*`, `UPFLUENCE_EMAIL` and `UPFLUENCE_PASSWORD` are
a website sign-in, not an API key. You get one task per pair ("Make <PREFIX> a website account?"). Do this, in order:
1. Find which bots read the two variables: search their repositories and skills (`grep -r <VAR>`), and the tasks that
   name them. Note each place.
2. Offer the person who manages Tools a website account on the vendor (Tools > Add > Website: its sites and a Shared
   account with that username and password; they reveal and type the values, you never see them), granted to the bots
   that use it. Tico then types it in the browser, with 2FA, and no bot sees the password.
3. For each bot that read the variables, have its code or skill moved to `tico browser open <site>` and
   `tico login fill <site> username|password|code` (BotOps commits it).
4. Keep both variables delivered until step 3 is done for every bot and you have confirmed no bot reads them; only then
   ask a credential admin to delete the two keys. Say on the task what you checked.

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
- **Everyone can read** (the Everyone row on Who can use it, on by default): every bot already reads the tool at Read,
  with no grant, wherever Read is held to reads (a Read key, or the proxy). A bot needs a grant only for Read and write,
  for a tool switched off, an Only listed tool, or one whose Read cannot be enforced (no Everyone row). Never ask anyone
  for a Read grant on a tool every bot already reads.
- One shared key per value, granted to many bots. Never a copy per bot, never the same value stored twice.
- One value per variable per bot: a bot can hold only one key under `LINEAR_API_KEY`.
- Read unless the bot's job writes and a person agrees to Read and write. With no Read key, a Read bot's proxied calls
  are limited to GET, HEAD and OPTIONS (and MCP calls that only read, where the proxy carries MCP).
- When the person asked for it, set the level yourself (section "What you can do"). Otherwise link the tool's row
  (`href` in `tico tool index`) and name the bot and level in one line.

## 4b. Access requests
A bot (or you, or BotOps, for a bot) asks with `tico access request <tool or owner/repo> --level read|read_write
--for <bot> --why "<what for>"`. Never file a task to a person for a level or repository access, and close one you
find by asking this way instead.
- Read where Tico can hold it to reads (a tool every bot could read by default; a ticked repository that is no bot's
  own) is granted at once; the person who added it gets a card with Take back.
- Everything else is one card to each person who can grant it; the first to act decides. Asking again adds nothing.
- The request closes itself once the access exists by any path (a level you set for a person counts), and the task
  that asked is woken. "Several to choose from" on a bot's Tools tab is answered by asking for the one tool it needs.

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
  Basic). Tico gives you one **Reconnect <vendor>** task for it, with a key card in its thread already addressed to the
  person who connected the tool (or a credential admin): do not open another card or ask for the key in words. The new
  value replaces the old key in place, every grant kept, and the task closes itself once it is saved or the tool reads
  Working again. Repeats of the same rejection fold into that task. Work it only when the person asks you something
  there, or the card is not enough (a key that needs a new scope, a vendor account that is gone: say so on the task).
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
