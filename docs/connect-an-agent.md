# Connect an external agent

Your own external agent (Grok, Muse, Claude, Cursor, Codex or any other that speaks MCP) can work in Tico
as you: read and act on your tasks, goals, docs and bots, with your rights and no more. It connects
to Tico's MCP server with a personal token.

This page covers an agent outside Tico that comes in to use it. When a bot in Tico needs a service that runs
elsewhere, such as a cleaning company's MCP server, give the bot a Tool instead:
[Work with a specialist outside Tico](connect-tools.md#work-with-a-specialist-outside-tico).

**Connect an external agent** opens from **Add existing bot** in the **+** menu beside **Team** or on any group (and
is a step in Finish setup). Any human can
make a personal token, and it sees what they see; the owner may limit tokens to admins (Settings > People).

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
| Auth | `Authorization: Bearer <token>`, or [OAuth sign-in](#oauth-sign-in) for agents that cannot send a header |
| Tools | The `tico` command set (`clients/hubtools.py`): tasks, goals and KPIs, docs, bots, messages, asks and OKs, updates, SQL. Each call runs as the token's human through the same routes the web app uses |
| Instructions | The server's `initialize` reply carries the "who needs me" skill (`skills/who-needs-me/SKILL.md`), so nothing else needs pasting |

A personal token can use `tico_api` on v2 routes and `tico_bot_update` with the human's own rights. It never sends
`on_behalf_of`; Agent Ops alone uses that to act for the human who asked it. Friendly tools also archive bots, docs and files,
delete meetings, and run Routines now.

`tico_api` supports bodyless writes, including DELETE. A file read returns its content type and
either `text` or `base64`, with `bytes` and `truncated`; at most 1 MiB is returned. A human may close
any task they can edit with `tico_task_close`; `wake: false` closes without waking a bot. Agent Ops uses
the requester's rights for that tool. `tico_bot_model` accepts a `harness` from the model's list.
For a correction to your active Agent Ops request, send `tico_message_send` with `in_reply_to` set to
the original message id, or `steer: true` in the same thread. The message stays queued until
the Computer delivers it into the active run; thread status shows when the follow-up is added.

The token cannot create tokens, make other credentials (a bot's agent credential, a pairing approval) or click
Confirm cards. Those need the human signed in to Tico. It can list its human's own tokens and revoke them
(`tico token list`, `tico token revoke <id>`, or `tico_token_list` / `tico_token_revoke`), itself included.
Ask Agent Ops to collect Credential values through your thread card. The API pass-through retains the
human's existing Credential permissions, including storing or revealing values when authorized. A Credential administrator can import a bot's existing Credential with
`tico_credential_import`, grant or revoke access, and delete it with `tico_credential_delete`; values never pass through the
agent during import. Bot deletion, outside-domain invites by an owner or admin, Team rule changes by the Owner, and Tico
updates run directly when requested.

Use `tico_assistant_read` and `tico_assistant_send` for your private Assistant DM. `assistant` is also a friendly recipient
for `tico_message_send`. These always use your own DM; another human's Assistant DM stays private.

Tools that need a local repository or make a local network call are available only on a Computer: `tico_bot_copy`,
`tico_bot_update_from_original`, `tico_bot_suggest_to_original`, `tico_skill_copy`, `tico_skill_submit` (docs/skills.md) and `tico_doc_fetch`. Run them on the Computer
holding the repository, or ask Agent Ops. The server explains this when one is called.

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

**Dots.** OpenAI's always-on agents reach apps through ChatGPT plugins, which sign in with OAuth and cannot send a
token. Add a connector with the MCP server URL; Dots opens Tico's sign-in, you press **Allow**, and it works as you
([OAuth sign-in](#oauth-sign-in)). [External agent sync](external-agent-sync.md#dots) then puts it on the team chart
under you. ChatGPT itself (developer mode, **Settings > Security and login > Developer mode**, then **Plugins > +**)
connects the same way.

**Muse** (Meta)
- The Muse app: ask Muse, in a chat, to make a custom connector for the MCP server at the URL. It signs in to Tico
  ([OAuth sign-in](#oauth-sign-in)), or takes the `Authorization` header. The app has no MCP settings page; it builds
  connectors in chat. [External agent sync](external-agent-sync.md#muse) then puts it on the team chart under you.
- Muse Code: add to `~/.config/muse/settings.json` (it must keep `"schema_version": 1`):
  `{"schema_version": 1, "mcp_servers": {"tico": {"transport": "streamable_http", "url": "<URL>", "headers": {"Authorization": "Bearer <token>"}}}}`

**Claude** (Anthropic)
- Claude Code: `claude mcp add --transport http tico <URL> --header "Authorization: Bearer <token>"`.
- The Claude app: **Customize > Connectors > Add custom connector**, paste the URL, and sign in to Tico when it
  asks ([OAuth sign-in](#oauth-sign-in)). On Team and Enterprise an owner adds the connector first under
  **Organization settings > Connectors**.

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

## OAuth sign-in

For an agent that cannot send a header (Dots, ChatGPT, the Claude app), the server speaks the MCP authorization flow
(`backend/mcp_oauth.py`): OAuth 2.1 with PKCE, dynamic client registration and no client secret.

1. The agent's first call gets 401 with `WWW-Authenticate: Bearer resource_metadata="<runner URL>/api/v2/oauth/protected-resource"`.
2. It reads the server's metadata (`<runner URL>/api/v2/oauth/.well-known/openid-configuration`) and registers itself.
3. Your browser opens `<team URL>/oauth/authorize` behind your usual sign-in: **Allow** or **Deny**.
4. The agent gets an access token (an hour) and a refresh token (90 days from its last use).

The result is a personal token: the agent is you, with your rights, and nothing more. It is one line in **Settings >
Computers > API tokens** named after the agent and the day; **Revoke** there stops it and its refresh at once. The
owner's rule that limits personal tokens to admins limits this too. Every machine-facing path is under `/api/v2/`, so a
runner hostname that passes only `/api/v2` needs nothing more; `/oauth/authorize` is on the team's own address.

## Handed over from setup

On a server Tico hosts, your own coding agent can finish setup for you with nobody signed in. The install gives the
server a one-time setup secret (the installer reads it from `TICO_SETUP_HANDOFF` in its environment, never a command
line argument, and the server keeps only its hash, `TICO_SETUP_HANDOFF_HASH`); Tico's team passes the secret to your
agent. The agent trades it once for a personal token of the owner:

```
POST <team URL>/api/v2/setup/handoff        (no sign-in; the secret is the credential)
{"secret": "<the setup secret>"}

200 {"token": "tico_pt_...", "token_id": "...", "label": "Setup agent · 2026-11-07", "expires_at": "...",
     "mcp_url": "https://<team hostname>/api/v2/mcp", "team_url": "https://<team hostname>"}
```

Then it connects as [any other agent](#the-server) with `Authorization: Bearer <token>`. The token is the owner's own: it
is named `Setup agent · <date>`, lasts 90 days, is listed under **Settings > Computers > API tokens**, and **Revoke**
there stops it at once. It has the owner's rights and the limits of every personal token (it cannot make or revoke
tokens). The owner's person row exists from the server's first start, so this works before the owner has ever signed in.

- The secret works once and for 7 days after the server first starts. It is refused after that, and after the owner turns
  it off (`POST /api/v2/setup/handoff/off` from a signed-in browser, which also revokes the token it made). `GET
  /api/v2/setup/handoff` tells the owner where it stands: `none`, `waiting`, `used`, `expired` or `off`.
- Every refusal is the same `410 gone`, whether the secret is wrong, used, expired or off. A wrong secret more than 10 times
  in 15 minutes from one address answers `429 rate_limited`. A server that was given no secret answers `404`.
- The secret is never logged, shown or kept: the server stores its hash and the token's hash, and the audit trail records
  only that a token was made.

What the agent can do from there, for each part of **Finish setup**:

| Step | How |
|---|---|
| See where setup stands | `tico_setup_show`: whether the welcome wizard is finished, its saved answers, the people the company file names, and the getting-started checklist |
| Finish the welcome wizard | `tico_setup_complete` (the wizard's Create my team); the picks are saved first with `tico_api` `PUT /api/v2/setup`, or come from the company file the install was given |
| Invite the people from the company file | `tico_setup_invite` with `person_key` (from `tico_setup_show`) and `email`. They go on the roster, and Tico mails them when the install signs people in with a Tico account |
| Choose models (your subscriptions or the Gateway default) | `tico_settings_set` on `models.providers` (`tico_settings_find` and `tico_settings_show` read it first); the Gateway's own setting is `models.gateway` |
| Add a Tico Cloud computer | `tico_api` `POST /api/v2/bots/<bot>/cloud-machine` with `{"mode": "cloud", "placement": "shared"}`: the company's shared cloud machine is made when its first bot runs there. `tico_bot_place` puts a bot on a computer that is already connected |
| Skip an optional getting-started step | `tico_api` `POST /api/v2/setup/getting-started/state` with `{"skip": "subscription"}`, the one optional step (not on Tico Cloud). GitHub is required: bots need it for their repositories. The other steps tick by themselves as they come true |
| Recommend a Claude or ChatGPT subscription | Not the agent's step: the person signs it in on their computer (**Settings > Computers**). Say it is recommended, for their own bots and within the provider's terms. `tico_setup_show` lists it as `subscription` in the getting-started items until it is signed in |

## An agent that is a bot, not you

An agent that should be a bot on the team, with its own thread, credential and heartbeat, instead of acting as
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
            print((await session.call_tool("tico_whoami", {})).content[0].text)
```

The token's **Last used** in the dialog moves on the first call.

## Tool naming

Most MCP tools use `tico_<thing>_<action>`. Four established helpers keep their short names:
`tico_whoami` (identity), `tico_sql` (read-only query), `tico_classify` (Decision questions) and `tico_brief` (build a brief).
Before 0.6.0 every tool was named `tico_*`; there is no alias for the old names, so update any saved allow rule
(`mcp__tico__tico_*` becomes `mcp__tico__tico_*`).

## Task pipelines

Every task is in a pipeline and one of its stages. `tico_task_create` takes a pipeline; `tico_task_move` moves a task
to the next stage, done, closed or back open; `tico_task_ask` asks a person for an OK, or a question (a ❓ in the task's chat), and `tico_task_answer` gives an OK's answer;
`tico_task_snooze` sets it aside until a time. The CLI mirrors them with `tico task create --pipeline`, `tico task move`,
`tico task ask`, `tico task answer` and `tico task snooze`. See [Tasks](tasks.md#pipelines-and-stages) and
[CLI and MCP](tasks.md#cli-and-mcp).

## Reach someone: @mentions, replies and sessions

A task's comments are a log: a plain comment wakes and notifies nobody, not even the agent holding the task. To reach
someone, @mention them, Reply to what they wrote, or write to an agent's session. All three are one route,
`POST /api/v2/tasks/{id}/comments`, with an optional `in_reply_to` or `to`. (One exception: on a task filed into a
shared chat, which has no thread of its own, every comment goes to the agent holding it.)

### Handles

The server finds @mentions in the text (`mentions` in `backend/threads.py`), ignoring case:

- An agent: its slug (`@ops`), its name or its display name. An archived agent is never matched.
- A person: their id (`@ben`), their email before the @ (`@ben.ortiz` for `ben.ortiz@acme.example`), their full name
  (`@Ben Ortiz`), or their first name when no one else on the team has it (`@Ben`).

The @ must start a word, so an email address is no mention. A handle ends at a space or punctuation, but not at `-` or
at a `.` followed by a letter or digit: `@sam.` at the end of a sentence is sam, while `@sam.lee` is someone else and
never sam. Write an agent's slug and a person's id: those are unique.

Look handles up with `GET /api/v2/org` (each person's `id`, `name` and `email`; each agent's `slug` and
`display_name`), MCP `tico_team_show`, or `tico team show`.

### Examples

`$TICO` is your team's address, `$TOKEN` your personal token and `<task>` a task id, short id or number.

**(a) An @mention in a task comment.** A person is notified; an agent is woken in its own session, and its answer
shows under the comment.

```sh
curl -X POST "$TICO/api/v2/tasks/<task>/comments" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"text": "@finance can you check the May totals? @ben for your review."}'
```

- MCP: `tico_task_comment` with `{"id": "<task>", "text": "@finance can you check the May totals?"}`.
- CLI: `tico task comment <task> "@finance can you check the May totals?"`.

**(b) A Reply.** It reaches the author of the comment it answers as an @mention would, with nothing added to the text,
and shows under that comment. The comment's `id` is in `GET /api/v2/tasks/{id}/comments`.

```sh
curl -X POST "$TICO/api/v2/tasks/<task>/comments" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"text": "Please add a test for the refund path.", "in_reply_to": "<comment id>"}'
```

- MCP: `tico_task_comment` with `{"id": "<task>", "text": "...", "reply_to": "<comment id>"}` (MCP calls it `reply_to`).
- CLI: `tico task comment <task> "Please add a test for the refund path." --reply-to <comment id>`.

**(c) A message in an agent's session.** `to` names an agent on the task; the comment goes to that agent's session and
wakes it. Only someone who may direct the task sends one (`can_direct` on the task read; 403 otherwise). `to` must be
an active agent you may see that can read the task (422 otherwise), and it goes without `public`, `ask`, `goal` or
`in_reply_to`.

```sh
curl -X POST "$TICO/api/v2/tasks/<task>/comments" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"text": "Use the August numbers.", "to": "ops"}'
```

- MCP: `tico_task_comment` with `{"id": "<task>", "text": "Use the August numbers.", "to": "ops"}`.
- CLI: `tico task comment <task> "Use the August numbers." --to ops`.

**(d) Answer an agent's question.** An agent asks a person with a ❓ (`tico_task_ask`). It waits in their Needs you:
in `GET /api/v2/needs-you` it is an item with `kind` `question`, `id` `qmark:<id>`, `task_id`, `line`, `from_id` (the
agent) and `choices` (up to three answers to tap; to pick one, send its text). Answer it by name with `ask`: that ❓
alone is settled and the agent that asked is woken. A second answer gets `409 answered`.

```sh
curl -X POST "$TICO/api/v2/tasks/<task_id>/comments" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"text": "Use the main bank feed.", "ask": "qmark:<id>"}'
```

Any other comment of yours on that task answers too: it settles all your open ❓s there and reaches each agent that
asked (with `to`, only that agent's). Only the person asked answers, by their own hand; a personal token counts, Agent
Ops or an automation writing as them does not. A ❓ in a direct chat (`task_id` null) is answered by replying in that
chat (`POST /api/v2/threads/{conversation_id}/messages`, `tico_thread_post`, `tico thread post`).

- MCP: `tico_needs_you_show` lists it; `tico_task_comment` with
  `{"id": "<task_id>", "text": "Use the main bank feed.", "ask": "qmark:<id>"}` answers it (leave out `ask` to answer
  as a plain comment).
- CLI: `tico needs-you show`, then `tico task comment <task_id> "Use the main bank feed." --ask qmark:<id>`.

### Who can be reached, and how to tell

- An @mention reaches someone who can read the task. On a private task, your @mention of a person adds them to its
  Who can see (`shared_with`), as a task event anyone on the task can undo; an agent's @mention adds only a teammate
  in its reach, never a group, and a task holds at most 50 readers. An agent that cannot read the task is not reached.
- In a DM or a chat (`POST /api/v2/messages`, `tico_message_send`, thread posts), an @mention of someone outside it
  reaches nobody and grants nothing. The sent message says so:
  `"warnings": [{"rule": "mention_outside", "actor": "human:cara", "actor_name": "Cara"}]`.
- A comment's answer says whom it reached. `woke` is true when it queued an agent's turn and `delivered` when it
  reached a person; `sent_as` (`{"delivery": "steer", "until": null}`) is how it reached the agent, and
  `comment.recipient_id` is the first agent it went to. A plain comment answers `"woke": false, "delivered": false`.
  `held` means an agent's comment to an agent waits for that agent's next run (`needs_action: true` wakes it now).
- To check first, `GET /api/v2/threads/{conversation_id}/reach?text=...&in_reply_to=...` (the task's
  `conversation_id` is on its read) lists whom it would reach (`reaches`: `actor`, `reason`, `mode`, `adds_reader`)
  and the @mentions it would not (`outside`). It writes nothing.

How the comment arrives for an agent (steer, queue or defer) is in [Tasks](tasks.md#conversation).
