# Linear

Docs: https://linear.app/developers/graphql, https://linear.app/docs/mcp (checked 2026-11-16)

## Credential
- Where: Settings > Account > Security & access (`linear.app/settings/account/security`) > create a personal API key.
- Kinds: personal API key (`lin_api_...`); OAuth 2 app (acts as a user, or as an app actor with `actor=app`).
- Read-only: create the key with only the Read permission (and optionally restrict to teams). Scope names for OAuth are not covered here (unconfirmed).
- Shape: header `Authorization` holding the raw key, with no `Bearer` prefix. OAuth access tokens do use `Bearer`.
- Variable: `LINEAR_API_KEY`.
- Expiry: none for personal keys.

## Check
- `POST https://api.linear.app/graphql` (GraphQL only: no GET path, so Tico's check path is empty). A real call with body `{"query":"{ viewer { id } }"}` is the proof. 401: key invalid. 403: valid but restricted.

## In Tico
- Route: API tool on the proxy, Auth `header:Authorization` (key as the raw value, no Bearer), base `https://api.linear.app`, calls go to `/graphql`. A read-only proxy level blocks POST, which GraphQL needs, so use a Read-permission key at the vendor and a full proxy level.
- Settings (not keys): none.
- MCP: `https://mcp.linear.app/mcp` by OAuth sign-in; an API key or OAuth token also works as `Authorization: Bearer <key>` there. Read-only endpoint: `https://mcp.linear.app/mcp/readonly`.

## Pitfalls
- Adding `Bearer` to a personal key breaks the call; the MCP server is the one place a Bearer key is accepted.
- Every GraphQL call is POST, so the proxy's Read level cannot be used.
- A key is tied to a person's account and access.
