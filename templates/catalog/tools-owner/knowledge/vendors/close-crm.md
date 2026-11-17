# Close CRM

Docs: https://developer.close.com/topics/authentication/, https://developer.close.com/mcp (checked 2026-11-16)

## Credential
- Where: Close > Settings > Developer > API Keys > generate a key. Each key is tied to one user and organization.
- Kinds: API key (recommended for scripts and internal integrations), OAuth app (scope `all.full_access` per catalog).
- Read-only: the docs page does not mention read-only API keys (none known). Only the MCP route has levels (`Close-Scope`).
- Shape: Basic, the key as the user and an empty password (`curl -u yourapikey:`). Header `Authorization: Basic base64("key:")`.
- Variable: `CLOSE_API_KEY`.
- Expiry: none.

## Check
- `GET https://api.close.com/api/v1/me/`: returns the key's user and organizations. 401: key wrong or deleted. 403: not documented.

## In Tico
- Route: API tool on the proxy, Auth `basic_user`, base `https://api.close.com`. A Read level holds the bot to GET even though the key can write.
- Settings (not keys): none.
- MCP: `https://mcp.close.com/mcp` (Streamable HTTP, no SSE). OAuth with dynamic registration (recommended), or headers `Close-API-Key: <key>` plus `Close-Scope`: `mcp.read`, `mcp.write_safe` (read and write), `mcp.write_destructive` (also delete). `mcp.read` is the way to get a read-only key. Header route: an API tool keyed `header:Close-API-Key`, with Close-Scope set in the bot's entry.

## Pitfalls
- The key acts as its user, with that user's access. Create it under a service-like seat, not a person who may leave.
- Close takes the key as Basic user; Bearer will not work.
