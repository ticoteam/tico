# Slack

Docs: https://docs.slack.dev/authentication/tokens, https://docs.slack.dev/ai/slack-mcp-server/, https://docs.slack.dev/authentication/verifying-requests-from-slack; Tico: docs/slack.md (checked 2026-11-16)

## Credential
- Tico already has its own Slack connection: Tools > Slack takes a bot token (`xoxb-`) and an app-level token (`xapp-`, scope `connections:write`) from an app made with the manifest Tools > Slack hands out; DM Tico or `@Tico`. Do not make a second API tool for chat. See docs/slack.md.
- Where (other uses): api.slack.com/apps > your app > Install App (Bot User OAuth Token); Basic Information > App-Level Tokens > Generate.
- Kinds: bot `xoxb-` (not tied to a person), user `xoxp-` (acts as that member), app-level `xapp-` (Socket Mode; no expiry unless rotation is on), configuration tokens (manifest APIs).
- Read-only: scopes picked on the app (for example `channels:history`, `channels:read`); reinstall after adding a scope.
- Shape: Bearer `Authorization: Bearer xoxb-...`.
- Variable: `SLACK_BOT_TOKEN` (convention).
- Expiry: none by default. Optional token rotation (cannot be turned off later) makes bot and user tokens short-lived with refresh tokens.

## Check
- `GET https://slack.com/api/auth.test`: Slack answers 200 with `"ok": false` for a bad token, not 401. Tico sets no check path for Slack; judge by the body (`invalid_auth`, `token_revoked`, `missing_scope`).

## In Tico
- Route: built-in connection first. For extra reads of a workspace: API tool on the proxy, Auth `bearer`, base `https://slack.com/api` (every Web API call answers 200; read `ok` and `error`).
- Settings (not keys): none (the first connection pins the workspace via `SLACK_TEAM_ID`).
- MCP: `https://mcp.slack.com/mcp`, Streamable HTTP only. No dynamic registration and no bot-token option: needs a directory-published or internal Slack app with client id and secret, and each person signs in (user tokens, per-tool scopes such as `search:read.public`). Tools > Add > MCP server takes that client id and secret for Sign in; the sign-in is one person's.

## Pitfalls
- The signing secret (Basic Information > Signing Secret) verifies requests Slack sends; it is never a tool key. A bot gets it as itself.
- A bot token only sees channels it was invited to (`/invite @Tico`).
- A user token acts as that person and shows their writes as theirs.
