# Cloudflare

Docs: https://developers.cloudflare.com/fundamentals/api/get-started/create-token/, https://developers.cloudflare.com/agents/model-context-protocol/mcp-servers-for-cloudflare/ (checked 2026-11-16)

## Credential
- Where: User token: My Profile > API Tokens > Create Token. Account-owned token: Manage Account > API Tokens (account scope) > Create Token. Pick a template or start from scratch, add policies, optional expiration and IP filter, then Create. The secret shows once.
- Kinds: API token (recommended; new ones start `cfut_`), account-owned API token (a service token not tied to a person), Global API Key (legacy, full access).
- Read-only: choose only `Read` permission groups (for example Zone DNS Read) and restrict to specific accounts or zones. No separate read-only switch.
- Shape: Bearer for tokens. The Global API Key uses two headers, `X-Auth-Email` and `X-Auth-Key`; avoid it.
- Variable: `CLOUDFLARE_API_TOKEN` (what the Cloudflare CLI reads).
- Expiry: none unless you set one.

## Check
- `GET https://api.cloudflare.com/client/v4/user/tokens/verify`: `status: active` for a valid user token. 401: token invalid. 403: valid but not allowed here.
- An account-owned token is verified at `/accounts/<account id>/tokens/verify` instead (unconfirmed); the user path can reject it. Then use a path the token can read.

## In Tico
- Route: API tool on the proxy, Auth `bearer`. The Global API Key needs two headers and is not supported through the proxy; use on computers.
- Settings (not keys): account id, zone id.
- MCP: `https://mcp.cloudflare.com/mcp` by OAuth sign-in, or an API token as Bearer (user and account tokens both work). Product servers (docs, containers, browser, dex, casb) are listed in the docs and use OAuth.

## Pitfalls
- A token limited to some zones gets 403 on others.
- Tokens made under "My Profile" belong to a person; if they leave, the token goes. Prefer account-owned tokens.
- Error 9106 means the legacy headers were missing.
