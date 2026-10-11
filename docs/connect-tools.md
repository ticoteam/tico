# Connect common tools

A bot owns its tools and its skills. To give one a service such as Jira or Linear, prefer the vendor's own
MCP server to anything we write: the vendor keeps it current, and Tico passes it to the bot's harness. Ask
BotOps ("connect Linear to Atlas") and it does the steps below; this page says what they are.

**Tico does not currently manage OAuth renewal for bots.** Prefer a vendor-supported API credential or a connection with
supported automatic refresh. Some providers require renewed human consent; check their documentation. When Tico cannot
maintain the MCP connection, a REST skill with a supported API credential is an option.

| Service | Vendor MCP server | API token or key in a header? | For a bot |
|---|---|---|---|
| Jira, Confluence | `https://mcp.atlassian.com/v2/mcp` | Yes, if an org admin enables it: Basic `email:token`, or a service-account key as Bearer | MCP with the token |
| Linear | `https://mcp.linear.app/mcp` | Yes: API key as Bearer | MCP with the key |
| PostHog | `https://mcp.posthog.com/mcp` | Yes: a personal API key with the MCP Server preset, as Bearer | MCP with the key |
| Sentry | `https://mcp.sentry.dev/mcp` | Documented in the server's README only: a user auth token as `Sentry-Bearer` (its docs page says OAuth) | MCP with the token, if it works for you; else the REST API |
| GitHub | none needed | The GitHub App gives each run a short-lived token | Built in |

## How a tool is connected

1. **The vendor's MCP server, if there is one.** Declare it in the bot's `tools:` in `bot.yaml`:

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
   - Store the value in **Tools → Credentials** or a Credential card and grant it to this bot.
     Its own secrets file and credential profiles are legacy migration sources; new runs do not load them.
     Nothing from the runner's own environment is used. A server whose variable is not
     granted is left out, and the run's log says which.
   - From a shell or a chat: `hub tool add <bot> linear --can read,write --env LINEAR_API_KEY --mcp-url https://mcp.linear.app/mcp
     --header 'Authorization: Bearer ${LINEAR_API_KEY}'`. `hub tool update` takes the same three flags.
2. **Otherwise a small client or skill in the bot's own repository** (`skills/<service>/SKILL.md`, or a script under
   `software/`) that calls the vendor's REST API with a key from `env`. See [A REST skill](#a-rest-skill) below.
3. **The credential.** `hub credential request <VAR> --for-bot <bot> ...` opens a card in the chat, or
   `printf '%s' "$VALUE" | hub credential set <VAR> --for-bot <bot>`, which also grants it to that bot. A value is never
   written into a file, a task or a commit ([credential-vault.md](credential-vault.md)).
4. **A read-only call** proves it (list one project, read one issue). The Tools tab shows the tool as `mcp`
   with the server's host, and whether the runner reached it: reachable, auth failed, or unreachable. `hub tool list --bot <bot>` says the same.

### Which harnesses take MCP servers

| Harness | Remote MCP servers | How |
|---|---|---|
| Claude Code | http and sse | `--mcp-config`, next to Tico's own `hub` server; the harness expands `${VAR}` itself |
| Codex | http only | `mcp_servers` config for the thread; `Authorization: Bearer ${VAR}` becomes `bearer_token_env_var`, `${VAR}` alone becomes `env_http_headers`, other shapes are filled in for the thread |
| Gemini CLI | http and sse | `mcpServers` in the bot's own settings file, with `${VAR}` left for the CLI to expand |
| Grok Build | http and sse | ACP `session/new`, headers filled in for that session |
| Cursor, Antigravity, pi | no | they read MCP servers only from a config file in the operator's home, or have none, so the operator's own servers would reach every bot; the bot's readiness shows a warning naming the tool |

The operator's own MCP servers never reach a bot through Tico. The bot's own `.mcp.json` (Claude Code) still applies.

### When the vendor's server needs OAuth

An OAuth connection may renew automatically when the provider and client support refresh. Tico does not currently
manage that renewal for bots. Use a supported API Credential, or a connection that manages automatic refresh, and check
whether the provider requires renewed human consent. [A REST skill](#a-rest-skill) below is the API Credential route.

## Jira and Confluence

Atlassian's remote MCP server (Jira, Confluence, Bitbucket Cloud and more) is `https://mcp.atlassian.com/v2/mcp`, over
HTTP. Use v2 for new connections; see [Atlassian's migration guidance](https://atlassian.github.io/atlassian-mcp-server/#how-it-works).
Tico does not manage its OAuth renewal. An **API token works instead, if an organization admin has turned it on**, with no
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

Ask BotOps: "Connect Jira to `<bot>` with a service account key." / "Give `<bot>` read access to Confluence."

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

`can` declares intended operations; Tico does not filter the vendor's MCP tool list by it. The endpoint or credential scopes
must enforce read-only access. [Linear's official guide](https://linear.app/docs/mcp) describes both options.

If you want writes, choose the write endpoint and declare that intent separately:

```yaml
- service: linear
  mcp: {url: "https://mcp.linear.app/mcp", transport: http, headers: {Authorization: "Bearer ${LINEAR_API_KEY}"}}
  can: [read, write]
  env: LINEAR_API_KEY
```

Ask BotOps: "Connect Linear to `<bot>`; it may only read."

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
Tico. Any TIDY tool can book a job, including one that takes a request in plain language, and `can:` does not stop it:
Tico does not filter a vendor's tools by `can:`. Use a read-only key or endpoint if TIDY offers one. Otherwise, have the
bot ask with an approval (`hub approval request --kind spend`) and send a booking or anything that costs money only after a person
approves it, and write that rule in the bot's instructions and the tool's `note:`.

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
| Business development | [Artisan Ava](https://www.artisan.co/) | Finds and researches leads, sends outreach and follow-ups, books meetings | No public API; the bot works alongside Artisan's app and your CRM |
| Software engineering | [Devin](https://devin.ai/), the cloud product | Takes delegated coding tasks and returns changes for review | Documents a [REST API](https://docs.devin.ai/api-reference/overview) and an [MCP server](https://docs.devin.ai/work-with-devin/devin-mcp) |
| Logistics operations | [HappyRobot](https://www.happyrobot.ai/solutions/industries/logistics-providers) | Carrier calls, appointment scheduling, and shipment tracking and tracing | Its site describes a [REST API and an MCP server](https://www.happyrobot.ai/product/developer-tools); the reference needs an access code |

These are examples, not integrations Tico has built or tested. What you hire is set by each vendor's actual scope,
setup, human involvement and terms. A sales-development provider does not close your sales, and a coding provider's
changes still need your normal review.

To bring one into your work:

1. Define the role and the result you expect, and name the person or bot that owns the relationship.
2. Agree what the provider may do, when it must ask a person, and what service and usage costs you accept. A booking
   or payment your bot makes waits for a person's approval, as [with TIDY](#work-with-a-specialist-outside-tico).
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
