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
