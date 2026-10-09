# Connect an external agent

Your own external agent (Grok, Muse, Claude, Cursor, Codex or any other that speaks MCP) can work in Tico
as you: read and act on your tasks, goals, docs and bots, with your rights and no more. It connects
to Tico's MCP server with a personal token.

**Connect an external agent** is the plug button beside your email (and a step in Finish setup). Until one of your
tokens has reached Tico, your own row on the team chart also shows **Connect**, which opens the same dialog. Any human can
make a personal token, and it sees what they see; the owner may limit tokens to admins (Settings > Humans).

1. Pick the agent.
2. **Create token**. It is named after the agent and the day (`Grok · 2026-09-30`), lasts 90 days, and
   is shown once. Copy it now; Tico keeps only its hash.
3. Copy the **MCP server URL** and follow that agent's steps below. The dialog fills the token and
   URL into each block to paste.
4. The dialog shows **Connected** once the agent's first call reaches Tico. It checks every 3 seconds
   for 5 minutes; **Check again** starts another 5 minutes.

Connected external agents are listed under the tiles with when each was last used. **Revoke** stops one at
once. The same tokens are under **Settings > Computers > API tokens**, where the owner and the Admins also see everyone's
tokens and revoke any of them.

## The server

| | |
|---|---|
| URL | `https://<your team hostname>/api/v2/mcp`: `TICO_RUNNER_URL`, or `TICO_PUBLIC_URL` when that is unset. The dialog shows the right one |
| Transport | Streamable HTTP, JSON replies, stateless (no session id; `GET` answers 405) |
| Auth | `Authorization: Bearer <token>`. No OAuth |
| Tools | The `hub` command set (`clients/hubtools.py`): tasks, goals and KPIs, docs, bots, messages, approvals, updates, SQL. Each call runs as the token's human through the same routes the web app uses |
| Instructions | The server's `initialize` reply carries the "who needs me" skill (`skills/who-needs-me/SKILL.md`), so nothing else needs pasting |

A personal token can use `hub_api` on v2 routes and `hub_bot_update` with the human's own rights. It never sends
`on_behalf_of`; BotOps alone uses that to act for the human who asked it. Friendly tools also archive bots, docs and files,
delete meetings, and run Routines now.

`hub_api` supports bodyless writes, including DELETE. A file read returns its content type and
either `text` or `base64`, with `bytes` and `truncated`; at most 1 MiB is returned. A human may close
any task they can edit with `hub_task_close`; `quiet: true` closes without waking a bot. BotOps uses
the requester's rights for that tool. `hub_bot_model` accepts a `harness` from the model's list.
For a correction to your active BotOps request, send `hub_message_send` with `in_reply_to` set to
the original message id, or `steer: true` in the same conversation. The message stays queued until
the Computer delivers it into the active run; conversation status shows when the follow-up is added.

The token cannot create or revoke tokens, make other credentials (a bot's agent credential, a pairing approval) or click
Confirm cards. Those need the human signed in to Tico.
Ask BotOps to collect Credential values through your chat card. The API pass-through retains the
human's existing Credential permissions, including storing or revealing values when authorized. A Credential administrator can import a bot's existing Credential with
`hub_credential_import`, grant or revoke access, and delete it with `hub_credential_delete`; values never pass through the
agent during import. Bot deletion, outside-domain invites by an owner or admin, Team rule changes by the Owner, and Tico
updates run directly when requested.

Use `hub_assistant_read` and `hub_assistant_send` for your private Assistant chat. `assistant` is also a friendly recipient
for `hub_message_send`. These always use your own room; another human's Assistant room stays private.

Tools that need a local repository or make a local network call are available only on a Computer: `hub_bot_copy`,
`hub_bot_update_from_original`, `hub_bot_suggest_to_original`, `hub_skill_copy` and `hub_doc_fetch`. Run them on the Computer
holding the repository, or ask BotOps. The server explains this when one is called.

For a complete Needs you walkthrough, see [Needs you batches](needs-you-batches.md).

## Sign in to the model first

Codex and Claude Code must already be signed in to their own model provider (or configured with its API key).
The Tico personal token authorizes Tico tools; it does not sign the CLI in to OpenAI or Anthropic.
Check with `codex login status` or `claude auth status`, and sign in with `codex login` or `claude auth login`.
See [Codex authentication](https://learn.chatgpt.com/docs/auth) and [Claude Code authentication](https://code.claude.com/docs/en/authentication).

For an isolated test, use a separate Codex configuration directory, for example
`CODEX_HOME=/tmp/tico-codex-test codex -c 'cli_auth_credentials_store="file"' login`, and use that same directory and `-c 'cli_auth_credentials_store="file"'` setting for every test command, including
`codex mcp add` and `codex`.
Sign in there separately and remove the test directory when finished. Alternatively, use a separate OS account for either CLI.
Your regular CLI configuration stays in its own directory.

## Each agent

**Grok** (xAI). To put every Grok Bot on the team chart under you, see [External agent sync](external-agent-sync.md#grok-bot).
- A Grok Bot: ask it to add a custom MCP server with the dialog's block (name, URL, `Authorization`
  header). Grok adds it to the whole account.
- grok.com: **Connectors > New Connector > Custom**, paste the URL, then give the token if it asks. On
  Grok Business an admin adds it first under **console.x.ai > Grok Business > Connectors**. xAI's
  docs do not say whether this form takes a bearer header, and grok.com refuses private and
  localhost URLs.
- Grok Build: `grok mcp add --transport http tico <URL> --header "Authorization: Bearer <token>"`, or
  `[mcp_servers.tico]` with `url` and `headers` in `~/.grok/config.toml`.

**Dots.** OpenAI's always-on agents reach apps through ChatGPT plugins, which sign in with OAuth or
not at all and cannot send a token. The dialog gives Dots the generic steps; until Tico's MCP server
offers OAuth, Dots is not expected to connect. Once connected, [External agent sync](external-agent-sync.md#dots) puts
it on the team chart under you. ChatGPT itself (developer mode, **Settings > Security
and login > Developer mode**, then **Plugins > +**) is in the same position, so it has no tile.

**Muse** (Meta)
- The Muse app: ask Muse, in a chat, to make a custom connector for the MCP server at the URL with the
  `Authorization` header. The app has no MCP settings page; it builds connectors in chat.
- Muse Code: add to `~/.config/muse/settings.json` (it must keep `"schema_version": 1`):
  `{"schema_version": 1, "mcp_servers": {"tico": {"transport": "streamable_http", "url": "<URL>", "headers": {"Authorization": "Bearer <token>"}}}}`

**Claude** (Anthropic)
- Claude Code: `claude mcp add --transport http tico <URL> --header "Authorization: Bearer <token>"`.
- The Claude app: **Customize > Connectors > Add custom connector**, paste the URL, choose **No
  sign-in**, and under **Request headers** add `authorization` = `Bearer <token>`. Request headers are
  a beta that not every plan has. On Team and Enterprise an owner adds the connector first under
  **Organization settings > Connectors**. Without request headers, use the **Other** JSON in
  **Settings > Developer > Edit Config** (Claude Desktop).

**Cursor.** In `~/.cursor/mcp.json` (or a project's `.cursor/mcp.json`), merged with any servers
already there: `{"mcpServers": {"tico": {"url": "<URL>", "headers": {"Authorization": "Bearer <token>"}}}}`.
`${env:TICO_TOKEN}` works in place of the token.

**Codex** (OpenAI): `export TICO_TOKEN=<token>` (keep it in your shell profile), then
`codex mcp add tico --url <URL> --bearer-token-env-var TICO_TOKEN`. That writes
`[mcp_servers.tico]` with `url` and `bearer_token_env_var` to `~/.codex/config.toml`; Codex refuses a
token written into the file.

**Other.** Any agent that takes a remote MCP server: the URL, Streamable HTTP, and the header
`Authorization: Bearer <token>`. An agent that only runs local servers from a JSON config can use
[`mcp-remote`](https://github.com/geelen/mcp-remote), which the dialog's JSON block sets up:
`npx -y mcp-remote <URL> --header "Authorization:${TICO_AUTH}"` with `TICO_AUTH` set to `Bearer <token>`.
Leave no space around that `:`.

## An agent that is a bot, not you

An agent that should be a bot on the team, with its own chat, credential and heartbeat, instead of acting as
you, pairs with a code: [Hermes agents](hermes-agents.md) and [OpenClaw agents](openclaw-agents.md). Both install
a "Tico sync" skill and one scheduled job on the agent, so it looks at its messages and tasks on its own schedule
(`--sync 15m|1h|daily|off`). Tico never starts it.

## Behind Cloudflare Access or another sign-in proxy

An outside agent cannot pass a sign-in page. When Access guards the hostname in the MCP URL, it
answers the agent with its login page, and the dialog says so under the URL. Either:

- In Cloudflare Zero Trust, add an Access application for `<hostname>/api/v2/mcp` with a **Bypass**
  policy (Everyone). Tico still refuses any call without a valid token (401). Or
- Serve the runner hostname without Access and set `TICO_RUNNER_URL` to it: the MCP URL follows.

An AWS load balancer with Cognito needs the same: a listener rule that forwards requests carrying
`Authorization: Bearer` without the authenticate action. The built-in sign-in (`TICO_AUTH_PROXY=oidc`)
and a loopback server need nothing.

An agent that runs in its maker's cloud (Grok Bots, the Grok and Claude apps, Muse) must reach the
URL from the internet; a loopback server is for agents on the same computer (Claude Code, Cursor,
Codex, Grok Build, Muse Code).

## Check it by hand

With the official MCP Python SDK (`pip install mcp`), from the same computer as a loopback server:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

async def main(url, token):
    client = create_mcp_http_client(headers={"Authorization": "Bearer " + token})
    async with client, streamable_http_client(url, http_client=client) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(len((await session.list_tools()).tools), "tools")
            print((await session.call_tool("hub_whoami", {})).content[0].text)
```

The token's **Last used** in the dialog moves on the first call.

## Tool naming

Most MCP tools use `hub_<thing>_<action>`. Four established helpers keep their short names for compatibility:
`hub_whoami` (identity), `hub_sql` (read-only query), `hub_classify` (Decision questions) and `hub_brief` (build a brief).
The `hub` prefix is a command identifier; the product is Tico.

## Task pipelines

Existing agents can keep setting `status` on tasks. `hub_task_types` lists optional task types and
steps; `hub_task_create` and `hub_task_update` also accept `type` and `step`. A step sets its status
under the same task permissions. The CLI mirrors them with `hub task types` and
`hub task update <id> --step "Legal review"`. See [Tasks](tasks.md) for the mapping rules.
