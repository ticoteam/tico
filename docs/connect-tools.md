# Connect common tools

A bot owns its tools and its skills. To give one a service such as Jira or Linear, prefer the vendor's own
MCP server to anything we write: the vendor keeps it current, and Tico passes it to the bot's harness. Ask
the Tools Owner in the Tools page's chat ("connect Linear to Atlas") and it does the steps below, with a card for the key and
a person's click for each grant; BotOps writes the bot's `bot.yaml`. This page says what the steps are.

**An MCP server added in Tools → Add → MCP server by OAuth sign-in is renewed by Tico.** Tico keeps the sign-in's tokens
and refreshes the access token on the server before it ends, one refresh at a time; when the vendor will not refresh it,
the tool's check says **Sign in again**. An OAuth connection declared in `bot.yaml` (the older way) is not renewed by
Tico: use a supported API credential there, or a REST skill.

| Service | Vendor MCP server | API token or key in a header? | For a bot |
|---|---|---|---|
| Jira, Confluence | `https://mcp.atlassian.com/v2/mcp` | Yes, if an org admin enables it: Basic `email:token`, or a service-account key as Bearer | MCP with the token |
| Linear | `https://mcp.linear.app/mcp` | Yes: API key as Bearer | MCP with the key |
| PostHog | `https://mcp.posthog.com/mcp` | Yes: a personal API key with the MCP Server preset, as Bearer | MCP with the key |
| Sentry | `https://mcp.sentry.dev/mcp` | Documented in the server's README only: a user auth token as `Sentry-Bearer` (its docs page says OAuth) | MCP with the token, if it works for you; else the REST API |
| GitHub | none needed | The GitHub App gives each run a short-lived token | Built in |

## How a tool is connected

1. **The vendor's MCP server, if there is one.** A credential administrator adds it in **Tools → Add → MCP server**
   (its address, then a pasted token or a sign-in) and gives each bot that needs it a level. That grant is all a bot
   needs: the server reaches its runs (through Tico's proxy when Tico holds the key) and shows on its Tools tab, with no
   `bot.yaml` entry. A server Tools does not hold (an older `sse` one, say) can instead be declared in the bot's `tools:`
   in `bot.yaml`, with its key granted as usual:

   ```yaml
   tools:
     - service: linear
       mcp: {url: "https://mcp.linear.app/mcp/readonly", transport: http, headers: {Authorization: "Bearer ${LINEAR_API_KEY}"}}
       can: [read]
       env: LINEAR_API_KEY
       note: "read issues and comments"
   ```

   - `url` is https (plain http only for localhost). `transport` is `http` (streamable HTTP, the default) or `sse`.
   - `headers` may use `${VAR}` for the entry's own `env` variable and nothing else. The value is never in the file.
   - Store the value as a key (**Tools → Add → Key**) or on a Credential card and grant it to this bot.
     Its own secrets file and credential profiles are legacy migration sources; new runs do not load them.
     Nothing from the runner's own environment is used. A server whose variable is not
     granted is left out, and the run's log says which.
   - From a shell or a chat: `tico tool add <bot> linear --can read,write --env LINEAR_API_KEY --mcp-url https://mcp.linear.app/mcp
     --header 'Authorization: Bearer ${LINEAR_API_KEY}'`. `tico tool update` takes the same three flags. When Tools
     also grants the bot an MCP tool under the same name, the bot.yaml server is used if its key reached the run, else
     the granted one, and nothing is reported missing.
2. **Otherwise a small client or skill in the bot's own repository** (`skills/<service>/SKILL.md`, or a script under
   `software/`) that calls the vendor's REST API with a key from `env`. See [A REST skill](#a-rest-skill) below.
3. **The credential.** `tico credential request <VAR> --for-bot <bot> ...` opens a card in the chat, or
   `printf '%s' "$VALUE" | tico credential set <VAR> --for-bot <bot>`, which also grants it to that bot. A value is never
   written into a file, a task or a commit ([credential-vault.md](credential-vault.md)).
4. **A read-only call** proves it (list one project, read one issue). The Tools tab shows the tool as `mcp`
   with the server's host, and whether the runner reached it: reachable, auth failed, or unreachable. `tico tool list --bot <bot>` says the same.

### Which harnesses take MCP servers

| Harness | Remote MCP servers | How |
|---|---|---|
| Claude Code | http and sse | `--mcp-config`, next to Tico's own `hub` server; the harness expands `${VAR}` itself |
| Codex | http only | `mcp_servers` config for the thread; `Authorization: Bearer ${VAR}` becomes `bearer_token_env_var`, `${VAR}` alone becomes `env_http_headers`, other shapes are filled in for the thread |
| Gemini CLI | http and sse | `mcpServers` in the bot's own settings file, with `${VAR}` left for the CLI to expand |
| Grok Build | http and sse | ACP `session/new`, headers filled in for that session |
| Muse Code | http only | `-c mcp_servers.<name>=...` for that run; a `${VAR}` header is read by the CLI from the run's environment, other shapes are filled into a variable only that run has |
| Cursor, Antigravity, pi | no | they read MCP servers only from a config file in the operator's home, or have none, so the operator's own servers would reach every bot; the bot's readiness shows a warning naming the tool |

A bot's turn gets the `hub` server, its granted MCP tools, the servers its `bot.yaml` declares and its repository's own `.mcp.json` (Claude Code),
not the computer operator's own (`~/.claude.json`, plugins, claude.ai connectors, `~/.codex/config.toml`). Grok still loads
servers in the operator's `~/.grok/config.toml` and plugins; Cursor reads `~/.cursor/mcp.json`; Antigravity its user config.

### When the vendor's server needs OAuth

Add the server in **Tools → Add → MCP server** and choose the sign-in: Tico keeps the tokens and refreshes them, and a bot
reaches the server through Tico's proxy. Some providers still require renewed human consent after a time; the tool's check
then says **Sign in again**. A server declared in `bot.yaml` is not renewed by Tico: use a supported API Credential, or
[a REST skill](#a-rest-skill) below.

## Jira and Confluence

Atlassian's remote MCP server (Jira, Confluence, Bitbucket Cloud and more) is `https://mcp.atlassian.com/v2/mcp`, over
HTTP. Use v2 for new connections; see [Atlassian's migration guidance](https://atlassian.github.io/atlassian-mcp-server/#how-it-works).
Added in Tools by sign-in, Tico renews its OAuth; declared in `bot.yaml`, it does not. An **API token works instead, if an organization admin has turned it on**, with no
consent screen:

```yaml
- service: jira
  mcp: {url: "https://mcp.atlassian.com/v2/mcp", transport: http, headers: {Authorization: "Basic ${JIRA_BASIC_AUTH}"}}
  can: [read]
  env: JIRA_BASIC_AUTH
```

- **Personal API token** (email and token, basic auth): the header is `Basic` and the base64 of `you@example.com:API_TOKEN`, so store that encoded text as
  `JIRA_BASIC_AUTH`. Make the token at id.atlassian.com/manage-profile/security/api-tokens.
- **Service account key** (better for a bot, made by an admin): `Authorization: "Bearer ${JIRA_SERVICE_KEY}"`.
- A token is not bound to one site, so the bot passes the site's `cloudId` in its calls. Some tools may be missing compared with OAuth.
- Not verified here: the exact tool list, and whether a site needs the admin setting before a token is accepted. If the server answers
  `auth failed` on the Tools tab, ask the admin.

Ask the Tools Owner: "Connect Jira to `<bot>` with a service account key." / "Give `<bot>` read access to Confluence."

## Linear

Linear's official MCP server is `https://mcp.linear.app/mcp` (streamable HTTP; SSE is its older fallback). It signs in with OAuth
or, for a script, takes an API key as `Authorization: Bearer <key>`: use the key, which does not expire on its own. Make a personal API key in Linear under
Settings, Security & access. `https://mcp.linear.app/mcp/readonly` is a read-only endpoint; use it for a `can: [read]` bot.

```yaml
- service: linear
  mcp: {url: "https://mcp.linear.app/mcp/readonly", transport: http, headers: {Authorization: "Bearer ${LINEAR_API_KEY}"}}
  can: [read]
  env: LINEAR_API_KEY
```

For a server held in Tools, a bot's level is enforced at Tico's proxy: with only a read-and-write key, a bot at **Read**
may call only the tools the server's own tool list marks `readOnlyHint`, plus the session's set-up and MCP's other read calls (listings, `resources/read`, `prompts/get`). The `can`
line in `bot.yaml` (the older way) declares intent only; Tico does not filter a vendor's MCP tools by it, so there the
endpoint or credential scopes must enforce read-only access. If `bot.yaml` names a server the same as a Tools one, the `bot.yaml` entry is used, so that server is
neither renewed nor filtered. [Linear's official guide](https://linear.app/docs/mcp) describes both options.

If you want writes, choose the write endpoint and declare that intent separately:

```yaml
- service: linear
  mcp: {url: "https://mcp.linear.app/mcp", transport: http, headers: {Authorization: "Bearer ${LINEAR_API_KEY}"}}
  can: [read, write]
  env: LINEAR_API_KEY
```

Ask the Tools Owner: "Connect Linear to `<bot>`; it may only read."

## PostHog

PostHog's MCP server is `https://mcp.posthog.com/mcp`. OAuth is its recommended sign-in, and its docs say that a client without OAuth
can use a **personal API key** instead, created with the **MCP Server** preset (which limits it to one project), sent as
`Authorization: Bearer <key>`. That is the bot's way.

```yaml
- service: posthog
  mcp: {url: "https://mcp.posthog.com/mcp", transport: http, headers: {Authorization: "Bearer ${POSTHOG_PERSONAL_API_KEY}"}}
  can: [read]
  env: POSTHOG_PERSONAL_API_KEY
```

Use `https://mcp.posthog.com/mcp` for both US and EU projects. PostHog routes authentication to the
account's region; see its [server URL and region note](https://posthog.com/docs/model-context-protocol/codex#server-url).
If authentication fails, check the personal API key's project and MCP Server preset.

## Sentry

Sentry's hosted MCP server is `https://mcp.sentry.dev/mcp`. Its docs page says every connection uses OAuth, which is wrong for a bot. Its
GitHub README (getsentry/sentry-mcp) describes a header for clients that can send one: `Authorization: Sentry-Bearer <user auth token>`
(plain `Bearer` is reserved for OAuth tokens), with a user auth token that has `org:read`, `project:read`, `project:write`,
`team:read`, `team:write` and `event:write`. We have not tried it. Whether an internal-integration token works is not stated.

```yaml
- service: sentry
  mcp: {url: "https://mcp.sentry.dev/mcp", transport: http, headers: {Authorization: "Sentry-Bearer ${SENTRY_ACCESS_TOKEN}"}}
  can: [read]
  env: SENTRY_ACCESS_TOKEN
```

If it is refused (auth failed on the Tools tab), use Sentry's REST API with the same token from a skill.

## A REST skill

When a vendor has no MCP server, or only an OAuth one, give the bot a small skill that calls its REST API with an API token
from `env`. Declare the tool, store the token as a Credential granted to the bot, and keep the first version read-only.

```yaml
- service: example
  can: [read]
  env: EXAMPLE_API_TOKEN
  note: "REST API, skill example"
```

`skills/example/SKILL.md` in the bot's repository:

````markdown
---
name: example
description: Read Example records through its REST API.
---

The token is in the environment as EXAMPLE_API_TOKEN. Never print it or write it into a file.

    example() {  # example GET /records?limit=5
      method=$1; path=$2; shift 2
      curl -sS -X "$method" "https://api.example.com/v1$path" -H "Authorization: Bearer $EXAMPLE_API_TOKEN" "$@"
    }

Check first with one read-only call.
````

The token goes in a header, not the URL, so it stays out of logs.

## GitHub

Built in: the GitHub App gives each run a short-lived token for the bot's own repository ([github-app.md](github-app.md)). No MCP
server or credential to set up; a bot needs one only for another organization's repositories, and then a declared tool
with a token is the way.

## Work with a specialist outside Tico

Some work belongs to a specialist that another company runs. One example is [TIDY](https://www.tidy.com), a
cleaning and maintenance company. A bot can ask TIDY what cleanings are planned for the office, follow up on a request
and bring the answer back to the task. The bot coordinates the work in Tico, and TIDY's own people do the job.

Connect TIDY the way this page connects any other service: as a Tool on the bot. Use TIDY's MCP server or
[a REST skill](#a-rest-skill) for its API. Either way, the token is stored as a Credential and granted to that bot.
TIDY's public pages disagree on the setup details: one offers a hosted server and another a local `npx` package, and
they list different tools. Take the exact setup from TIDY's [MCP page](https://www.tidy.com/developers/mcp),
[MCP setup guide](https://www.tidy.com/docs/developers/mcp-server) and [API page](https://www.tidy.com/developers/api).
Then prove it with one read-only call.

Start with a question, such as "What cleanings are scheduled for our office this week?", and review the answer in
Tico. Some TIDY tools can book a job, including one that takes a request in plain language, and in `bot.yaml`
`can:` does not stop it. Added in Tools with only a read-and-write key and granted at Read, the bot may call only the
tools TIDY's server marks `readOnlyHint`, so a booking tool the server does not mark is refused. A key added at Read goes
to the bot as it is: Tico does not check its scopes, so it must be read-only at TIDY. Use a read-only key or endpoint if TIDY offers one. Otherwise, have the
bot ask a person to OK it (`tico task ask --kind ok`) and send a booking or anything that costs money only after the
person OKs it, and write that rule in the bot's instructions and the tool's `note:`.

Tico doesn't run TIDY's workers or track what you spend with TIDY. It has no TIDY connector of its own: TIDY is a Tool,
like Linear.

## Hire an external AI specialist

You can hire a company to fill a defined role for your team. You give it the job, the business context and what it may
do, and the provider runs the specialist. Its service may combine AI and people. Your team reviews what comes back and
makes the decisions that need a person.

Tico keeps the owner, the requests, the returned work and the follow-ups together. For example, an operations bot can
coordinate cleaning with TIDY while a sales provider runs prospecting and a support provider answers customers in its
own service.

| Role you need | Example provider | Work the provider offers | How a bot could reach it |
|---|---|---|---|
| Property operations | [TIDY](https://www.tidy.com/developers/mcp) | Cleaning, maintenance and turnovers | Documents an MCP server and an API; see [above](#work-with-a-specialist-outside-tico) |
| Customer support | [Fin](https://fin.ai/) | Answers customer questions and hands off to your team when it can't resolve one | Documents an API: the [Fin Agent API](https://developers.intercom.com/docs/guides/fin-agent-api) |
| Reception and intake | [Smith.ai AI Receptionist](https://smith.ai/ai-receptionist) | Answers calls, screens and qualifies callers, routes them and books appointments; live agents are available | Its help docs cover an [API key](https://docs.smith.ai/category/ydtshfm8im-api), and Zapier and Make connections |
| Sales development | [11x Alice](https://www.11x.ai/worker/alice) | Engages prospects, handles replies and books qualified meetings | Its site describes an [API and webhooks](https://www.11x.ai/platform/integrations/api) |
| Business development | [Artisan Ava](https://www.artisan.co/) | Finds and researches leads, sends outreach and follow-ups, books meetings | No public API reference was found in the pages checked on October 11, 2026; the bot works alongside Artisan's app and your CRM |
| Software engineering | [Devin](https://devin.ai/), the cloud product | Takes delegated coding tasks and returns changes for review | Documents a [REST API](https://docs.devin.ai/api-reference/overview) and an [MCP server](https://docs.devin.ai/work-with-devin/devin-mcp) |
| Logistics operations | [HappyRobot](https://www.happyrobot.ai/solutions/industries/logistics-providers) | Carrier calls, appointment scheduling, and shipment tracking and tracing | Its site describes a [REST API and an MCP server](https://www.happyrobot.ai/product/developer-tools); the reference needs an access code |

These are examples, not integrations Tico has built or tested. What you hire is set by each vendor's actual scope,
setup, human involvement and terms. A sales-development provider does not close your sales, and a coding provider's
changes still need your normal review.

To bring one into your work:

1. Define the role and the result you expect, and name the person or bot that owns the relationship.
2. Agree what the provider may do, when it must ask a person, and what service and usage costs you accept. A booking
   or payment your bot makes waits for a person's OK, as [with TIDY](#work-with-a-specialist-outside-tico).
3. Connect an interface the provider documents, as a Tool, or bring its reports and results into your tasks. Confirm
   the account, the access and how requests and results travel before you count on automation.
4. Keep requests, results, escalations and decisions on the task. The provider runs its service; the owner in Tico
   keeps the work accountable.

Tico has no built-in connector for these providers. Its permissions and usage reporting do not control what a provider
does on its own or what it bills you.

An agent you run yourself is a different case. For an agent such as [OpenClaw](openclaw-agents.md) working in Tico,
see [Connect an external agent](connect-an-agent.md). This section answers another question: who can you hire to fill
this role?

## Other services

This page is about a bot reaching another service. The other way round, when your own product's backend has to file
tasks in Tico and close them (a report is ready for its account manager), give it a [service key](service-keys.md).

Look for the vendor's own MCP server first: search "`<vendor>` MCP server", open the vendor's docs, and check for an
address and whether it takes an API token or key in a header (not only OAuth). If the vendor has
none, or it is OAuth-only, write [a REST skill](#a-rest-skill) that calls its REST API with an API token. Either way, say in the tool's `note:` who approved it and what is out of bounds.

*Facts on this page were read from the vendors' own documentation on 2026-09-30; vendors change these, so when a tool shows "auth failed" or
"unreachable", check the vendor's current page first.*

When a custom tool's last declaration is removed, its saved learnings remain in Tools as a read-only page.
The Owner can still delete a learning there.

## Tools list

**Tools** (`#/tools`) lists every way the company reaches the outside world: API tools (a service bots call with a key),
mailboxes, Tico addresses and numbers, physical mail, Slack, the GitHub App, meeting importers, Listening, outgoing
webhooks, and tools bots declare in `bot.yaml` that have no API tool yet. Each row shows who can use it and, only when
something is wrong, a pill; a lock means Tico holds the key (the proxy adds it to each call and bots never see it), an
eye-off that only those listed can see the tool. The list has one row a vendor: Raygun's website login, its MCP token
and its API key are one **Raygun** row, its pill its worst tool's. The row opens the vendor's page
(`#/tools/vendor/<vendor>`, one read: `GET /api/v2/tool-vendors/<vendor>`), a card for each tool with its keys at their
levels, who can use it and its settings, each opening the tool's own page. A vendor with one tool, and anything that is
no vendor's (a mailbox, a Tico number, Tico's own tools), is its own row under its own name. Search reads tool and
vendor names. Incoming webhooks and triggers live in Automations, not here.

The owner and admins see every row. Anyone else sees their own (their mailbox, webhooks they made) and the company's rows
they may read: an API tool they were given a level on, Slack and GitHub once connected, Tico addresses and numbers in
their audience, meeting importers if they read meetings, physical mail, and Listening when they can see a listening.
The Listening row shows its worst source's state. Bots named are only those you may see.

`GET /api/v2/tool-index?limit=&cursor=` answers `{rows: [{id, kind, name, detail, way, status: {tone, text}, who,
who_total, needs_you, href, vendor, vendor_name}], counts, next}` (an API tool's row adds `service` and `delivery`), at most
500 rows and 192 KB a page; follow `next` with `cursor`. CLI: `tico tool index`; MCP: `tico_tool_index` (BotOps reads
it with the rights of the person who asked; the Tools page's owner bot reads every tool the company sees, with who holds
which level, never a key).

### API tools

An API tool (`/api/v2/api-tools`) is a service's base URL, how its key goes on a request, and a cheap check path; known
services (backend/tool_services.py) fill these in from the name. It holds at most one key per level, **Read** or **Read
and write**, each a vault credential, and **Who can use it** gives a person or bot a level: Read uses the Read key when
there is one, else the full key at Read. Only credential administrators add keys or set levels; a person with a level
reads the tool's page.

Status: **Working** (the check got 2xx, or a real call through the proxy did in the last 24 hours), **Works (limited)**
(the check got 403: the key works, the check path needs a scope it lacks), **Key rejected** (the check got 401 that does not say a permission is missing, or 3 real
calls in a row did with no 2xx since), **Check failed**, **Expires in N days** (14 or fewer), or **Not checked**. Only a real problem puts a tool under Needs you: how its key reaches runs never does (a key on
computers goes to each run's environment, as every key stored before API tools did; Move to proxy on the tool's page
changes that).

A tool's **vendor** is its `service` when one is set (a credential administrator types it on the tool's Edit; a known
service sets it itself; `aws-production` is AWS's and `google-ads` Google's), else a known vendor any word of its name or
its keys' variables names (`TIDY_AWS_READONLY` is AWS's, `GH_TOKEN` GitHub's, `GSC_*` Google's), else its address's,
MCP server's or site's domain (`raygun.com`), else its name's first word ("Raygun production"). The GitHub App and Slack rows are
GitHub's and Slack's.

**Settings** are a tool's non-secret values: a host, a region, a profile, a project id, a bucket. Each is a variable and
its plain value, shown in the clear on the tool's page, changed by credential administrators
(`POST /api/v2/api-tools/<id>/settings {name, value}`, `PATCH|DELETE …/settings/<setting id>`), and set in the
environment of every run that holds any level on the tool, on the proxy or on computers. A setting is never a key: a
value that looks like one (a key shape, a URL with a password or a token in it) is refused, and a name with TOKEN,
SECRET, PASSWORD or API_KEY in it belongs under Keys. A key the run holds under the same variable wins. Tico checks each tool with a check path every 30 minutes, on the proxy or on computers; `tico tool check
<id>` (MCP `tico_tool_check`) checks now. Checks and calls older than 90 days are deleted.

A tool's **Activity** (`GET /api/v2/api-tools/<id>/activity?since=&result=&limit=&cursor=`) lists its calls through the
proxy (who, method, path with the query's keys only, status, result: `sent` reached the vendor, `refused` was stopped
by Tico, `failed` never reached it) and, for keys on computers, each caller's last use, newest first, never a value.
`since` is `24h`, `7d` or an ISO time. A credential administrator and the Tools page's owner bot see every caller;
anyone else sees their own rows and those of the bots they may see. CLI: `tico tool activity <id> [--since 24h]
[--result refused]`; MCP: `tico_tool_activity`, which adds a summary of repeated calls and errors (ids in paths folded
to `:id`) and who added the tool's keys. The Tools Owner reads it daily to learn how each tool is used.

## Every Tools change from the CLI and MCP

Nothing on the Tools page needs the page: each change is a `tico` command and a Tico MCP tool of the same name
(`tico tool setting add` is `tico_tool_setting_add`) that calls the route the page calls, with the caller's rights.
BotOps acts as the person who asked it, the Tools Owner as that person on the changes `backend/tools_delegation.py`
lends it, and everyone else as themselves; `backend/tool_access.py` (`may_change_access`) decides every write, and a
refusal names who can. A value is never an argument: `--from-env VAR` names a variable and `--from-file PATH` a file on
the computer that runs the command, read there (those MCP tools run only in a bot's own `tico` MCP server, never the
server's endpoint), and no answer carries a value or its preview. `backend/tests/test_tools_parity.py` fails when a Tools
write route has no command, or a command calls another route.

| Action | Route | CLI | MCP |
|---|---|---|---|
| List tools, status, who can use each | `GET tool-index` | `tico tool index` | `tico_tool_index` |
| One tool: keys, levels, settings | `GET api-tools/{id}` | `tico tool detail` | `tico_tool_detail` |
| One vendor's tools | `GET tool-vendors/{vendor}` | `tico tool vendor` | `tico_tool_vendor` |
| Add an API tool | `POST api-tools` | `tico tool create` | `tico_tool_create` |
| Rename; address, auth, check path; vendor (`service`); delivery (Move to proxy); Everyone can read; Only listed | `PATCH api-tools/{id}` | `tico tool edit` | `tico_tool_edit` |
| Delete a tool | `DELETE api-tools/{id}` | `tico tool delete` | `tico_tool_delete` |
| Check now | `POST api-tools/{id}/check` | `tico tool check` | `tico_tool_check` |
| A bot's level | `PUT api-tools/{id}/access/{subject}` | `tico tool level` | `tico_tool_level` |
| Add a key to a tool | `POST api-tools/{id}/keys` | `tico credential add --tool` | `tico_credential_add` |
| Change a tool's key (level, name, end date, value) | `PATCH api-tools/{id}/keys/{key}` | `tico credential update` | `tico_credential_update` |
| Delete a tool's key | `DELETE api-tools/{id}/keys/{key}` | `tico credential delete` | `tico_credential_delete` |
| Settings | `POST`, `PATCH`, `DELETE api-tools/{id}/settings` | `tico tool setting add`, `change`, `remove` | `tico_tool_setting_*` |
| Auth templates: list, set, ungroup | `GET tool-templates`, `PUT api-tools/{id}/template`, `POST api-tools/{id}/ungroup` | `tico tool templates`, `tico tool template`, `tico tool ungroup` | `tico_tool_templates`, `tico_tool_template_set`, `tico_tool_ungroup` |
| A vendor account's values: classify, store in one step (read from a variable or file) | `POST tool-templates/classify`, `POST api-tools/{id}/paste` | `tico tool classify`, `tico tool paste` | `tico_tool_classify`, `tico_tool_paste` |
| MCP server: probe, add, start its sign-in | `POST mcp-tools/probe`, `POST mcp-tools`, `POST api-tools/{id}/sign-in` | `tico tool mcp probe`, `tico tool mcp add`, `tico tool sign-in` | `tico_tool_mcp_probe`, `tico_tool_mcp_add`, `tico_tool_sign_in` |
| Website tool | `GET`, `POST`, `PATCH`, `DELETE website-tools` | `tico tool website show`, `add`, `edit`, `delete` | `tico_tool_website_*` |
| Website account: add, change, delete | `POST`, `PATCH`, `DELETE website-tools/{id}/accounts` | `tico credential login add`, `edit`, `delete` | `tico_credential_login_*` |
| Share a website account | `PUT website-tools/{id}/accounts/{a}/access/{subject}` | `tico credential login share` | `tico_credential_login_share` |
| Keys and their grants | `GET credentials`, `POST credentials/{id}/grants`, `…/revoke`, `DELETE credentials/{id}` | `tico credential list`, `grant`, `revoke`, `delete` | `tico_credential_*` |
| Store a key a caller holds (a bot: as its own key) | `POST credentials`, `POST credential-set` (`--for-bot`) | `tico credential add` | `tico_credential_add` |
| Change a key on no tool | `POST credentials/{id}` | `tico credential update` | `tico_credential_update` |
| A person's key: open a card, withdraw it | `POST credential-requests`, `…/{id}/cancel` | `tico credential request`, `cancel` | `tico_credential_request`, `tico_credential_cancel` |
| A value a person pasted in chat | `POST credential-set` | `tico credential set` | `tico_credential_set` |
| Move a bot's file secret into the vault | `POST credential-imports` | `tico credential import` | `tico_credential_import` |
| Propose where dropped keys go | `POST tools-chat/proposals` | `tico tool propose-grant` | `tico_tools_propose_grant` |
| A bot's `bot.yaml` tool notes | `POST bots/{bot}/tools`, `…/update`, `…/delete` | `tico tool add`, `update`, `remove` | `tico_tool_add`, `tico_tool_update`, `tico_tool_remove` |
| Learnings | `POST tools/{service}/learnings`, `…/{id}/delete` | `tico tool learn`, `tico tool forget` | `tico_tool_learn`, `tico_tool_forget` |
| A tool's calls and last uses | `GET api-tools/{id}/activity` | `tico tool activity` | `tico_tool_activity` |

Only a person does these, in the app: reveal a value (`POST credentials/{id}/reveal`), type a value into a key card
(`…/save`), tap Grant on a key card (`POST tools-chat/proposals/{id}/grant`), and watch or drive a website sign-in's live
view (`…/accounts/{a}/browser`). The OAuth sign-in itself happens in the person's browser at the address
`tico tool sign-in` prints.
