# Notion

Docs: https://developers.notion.com/docs/authorization, https://developers.notion.com/docs/get-started-with-mcp (checked 2026-11-16)

## Credential
- Where: Developer portal (app.notion.com/developers/connections) > create a connection > Configuration tab > copy the token. Workspace owner only.
- Kinds: internal integration secret (`ntn_...`, older `secret_...`; prefix not confirmed in the fetched docs); public integration via OAuth; MCP token for the hosted MCP.
- Read-only: choose capabilities on the connection (read, update, insert, comments); leave only Read content.
- Shape: Bearer, plus required header `Notion-Version`.
- Variable: `NOTION_API_KEY`.
- Expiry: none.

## Check
- `GET https://api.notion.com/v1/users/me`: returns the bot user. 401: token invalid or revoked. 403: capability missing. The check passes even when no page is shared.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, address `https://api.notion.com`, fixed header `Notion-Version` (Tico sets `2022-06-28`; current docs example uses `2026-03-11`).
- Settings (not keys): none.
- MCP: `https://mcp.notion.com/mcp` (Streamable HTTP). OAuth sign-in by a workspace member (Tools > Add > MCP server, Sign in). Owners on Business or Enterprise can create an MCP token in the Developer portal (New MCP token) and send it as Bearer: a pasted token, no sign-in to renew.

## Pitfalls
- A page or database must be shared with the connection (page ... menu > Add connections). Otherwise reads error (404 object not found is the usual form; docs say only "an error").
- A working check proves nothing about content access.
- The legacy MCP `/sse` endpoint is retired on 2027-02-23.
