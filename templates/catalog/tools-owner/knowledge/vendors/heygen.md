# HeyGen

Docs: https://developers.heygen.com/docs/quick-start, https://developers.heygen.com/docs/usage-limits (checked 2026-11-16)

## Credential
- Where: platform.heygen.com/api-keys > create a key (exact button labels unconfirmed).
- Kinds: API key. Remote MCP uses OAuth instead.
- Read-only: no read-only option (the catalog notes no scoping).
- Shape: header `X-Api-Key`.
- Variable: `HEYGEN_API_KEY`.
- Expiry: none documented.

## Check
- Tico uses `GET https://api.heygen.com/v2/user/remaining_quota`. The current quick start names `GET /v3/users/me` as the key test; the quota path was not found in the fetched docs, so confirm it before relying on either. 401: key invalid. 403: not documented.

## In Tico
- Route: API tool on the proxy, Auth `header:X-Api-Key`, address `https://api.heygen.com`.
- Settings (not keys): none.
- MCP: `https://mcp.heygen.com/mcp/v1/` by OAuth sign-in only; uses plan credits. Connect it in Tools > Add > MCP server with Sign in.

## Pitfalls
- Whether API credits and web plan credits are the same pool: not confirmed in current docs; check the account's billing page before promising renders.
- Limits are per user and per endpoint class; 429 with `X-RateLimit-Remaining`. Pay-as-you-go allows 10 concurrent workflows, Enterprise 20.
- Renders and avatar videos cost credits per call; use a Read level for lookups.
