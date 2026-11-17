# Anthropic API

Docs: https://platform.claude.com/docs/en/api/overview, https://platform.claude.com/docs/en/manage-claude/authentication (checked 2026-11-16)

## Credential
- Where: Claude Console (platform.claude.com) > Settings > API keys > Create key. Admin API keys: Console admin keys page (docs/en/manage-claude/admin-api-keys).
- Kinds: personal key (acts as you), service account key (for shared or automated use), legacy workspace key; Admin API key (`sk-ant-admin...`) is separate.
- Read-only: no read-only option. Limit blast radius by scoping the key to one workspace when creating it, setting an expiration, and using workspace spend limits (Console > Settings > Spend limits).
- Shape: header `x-api-key` (legacy, still supported) or `Authorization: Bearer`. Plus required header `anthropic-version`.
- Variable: `ANTHROPIC_API_KEY`.
- Expiry: chosen at creation: 3 hours, 1, 7, 30 days, custom or Never (an org policy can forbid Never). Cannot be changed later. Expired key returns 401.

## Check
- `GET https://api.anthropic.com/v1/models` with `anthropic-version` set: lists models. 401: invalid, disabled or expired key. 403: not seen as the usual key failure (unconfirmed).

## In Tico
- Route: API tool on the proxy, Auth `header:x-api-key`, with fixed header `anthropic-version` (Tico uses `2023-06-01`; docs now show `2026-10-21`, the older value is unconfirmed as still current).
- Settings (not keys): header `anthropic-workspace-id` (`wrkspc_...`), needed when the key is not scoped to one workspace; without it the API returns 400.
- MCP: none (docs-only MCP).

## Pitfalls
- A personal key stops working when its owner leaves the organization; prefer a service account key.
- Admin keys call `/v1/organizations/...` only and are rejected for Messages.
- Some orgs (ZDR, Compliance API) refuse requests carrying an `Origin` header with 401 `CORS requests are not allowed`; server calls are fine.
