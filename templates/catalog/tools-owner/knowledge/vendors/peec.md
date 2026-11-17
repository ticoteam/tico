# Peec AI

Docs: https://docs.peec.ai/api/introduction, https://docs.peec.ai/api/authentication, https://docs.peec.ai/mcp/introduction (checked 2026-11-16)

## Credential
- Where: app.peec.ai > Account > API keys (https://app.peec.ai/api-keys). Personal Access Tokens for the MCP server are under API Keys > Personal Access Tokens.
- Kinds: API key; Personal Access Token (MCP); OAuth sign-in (MCP).
- Read-only: no read-only option documented.
- Shape: header `x-api-key: <key>`, raw key, no prefix. The `api_key` query parameter is deprecated and stops working 2026-11-23.
- Variable: `PEEC_API_KEY`.
- Expiry: none documented.

## Check
- `GET https://api.peec.ai/customer/v1/prompts`. 401: key rejected (bad-key status unconfirmed). 403: unconfirmed.

## In Tico
- Route: MCP server `https://api.peec.ai/mcp` (Streamable HTTP) with a pasted Personal Access Token as `Authorization: Bearer <token>`: preferred, the server is the vendor's full tool set. Or an API tool on the proxy, Auth `header:x-api-key`, base `https://api.peec.ai/customer/v1`.
- Settings (not keys): project id (ids are opaque `pr_...`-style strings; list projects first).
- MCP: `https://api.peec.ai/mcp`: OAuth 2.0 (consent page, one-time) or a PAT header. Either works in Tools > Add > MCP server; a PAT needs no sign-in to renew.

## Pitfalls
- API access needs a Pro, Advanced, Enterprise or agency plan; Starter has none.
- The MCP PAT and the REST API key are different credentials.
- Rate limits are not stated in the docs.
- Changing the tracked prompt set moves visibility numbers without changing what AI engines say; do not treat it as a fix.
