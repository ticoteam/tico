# Bright Data

Docs: https://docs.brightdata.com/api-reference/authentication, https://docs.brightdata.com/ai/mcp-server/remote/oauth (checked 2026-11-16)

## Credential
- Where: control panel > Account settings (brightdata.com/cp/setting/users) > API key section > Add API key; set user, permissions, expiration, Save. Admin accounts only. The key is shown once.
- Kinds: API key (Bearer) for the Web Scraper, SERP and Unlocker APIs; a zone with its own username and password is a separate credential for the proxy network.
- Read-only: no read-only option. Permission roles per key: Admin, Finance, Ops, Limit, User. User = API usage without billing or product configuration (the least privilege).
- Shape: Bearer for the API (`https://api.brightdata.com`). Proxy network: Basic-style username built from customer id + zone + password (several fields).
- Variable: `BRIGHTDATA_API_KEY` (unconfirmed convention); proxy: separate keys for customer id, zone, password.
- Expiry: set at creation; a key can be refreshed, which invalidates the old one. One key per user.

## Check
- A list path such as `/zone/get_active_zones` was not found in the fetched docs (unconfirmed); the only documented endpoint is `POST /request`. Use a documented GET from the API reference. 401: key invalid or expired. 403: key role lacks the product.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, address `https://api.brightdata.com`. The proxy network (host, port, username, password) goes on computers: three or four keys, each with its own variable.
- Settings (not keys): customer id and zone names (they are part of the proxy username).
- MCP: `https://mcp.brightdata.com/mcp` (Streamable HTTP; also `/sse`). Auth: `?token=<API key>` in the URL (a secret in the URL, not supported yet for bot MCP) or OAuth 2.1 (PKCE, scope `mcp`).

## Pitfalls
- Default Python User-Agent is blocked on brightdata.com with a 403 HTML page; set a User-Agent.
- Calls spend account credits.
