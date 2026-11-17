# Brex

Docs: https://developer.brex.com/docs/authentication, https://developer.brex.com/docs/mcp (checked 2026-11-16)

## Credential
- Where: dashboard.brex.com as an account admin or card admin > Developer > Settings > accept the Developer API agreement > Create Token > name it, pick scopes > copy it (not shown again).
- Kinds: user token (per user). OAuth is for partners (not covered by the user-token page).
- Read-only: pick only read scopes in the scope picker. Third-party guides show names ending `.readonly` (for example `transactions.readonly`) (unconfirmed; the official scope list is not on the docs page, read the picker).
- Shape: Bearer `Authorization: Bearer <user token>`.
- Variable: `BREX_TOKEN` (Tico's name; Finance's Brex sync uses the same).
- Expiry: expires if not used for an API call for 90 days. Stops working if the user is no longer active. Revoke from the Developer page.

## Check
- `GET https://api.brex.com/v2/users/me`: returns the token's user. 401: token rejected, revoked, or expired (including 90 days unused). 403: valid token without the scope this path needs. Prefer a path the token's scopes cover if the token has no user-read scope.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, base `https://api.brex.com`. A Read level lets only GET through.
- Settings (not keys): none.
- MCP: `https://api.brex.com/mcp` (beta). OAuth with dynamic registration (any employee), or the same user token as Bearer (admins only). Enable Settings > Beta features > "Brex in AI assistants" and accept the Developer API agreement first. A missing scope removes the matching tools. Approvals and card management are not in MCP.

## Pitfalls
- Run a call at least every 90 days or the token dies silently. The Tools Owner should treat a 401 on an old token as expiry and ask a person to create a new one.
- Older third-party docs show `platform.brexapis.com`; Brex now names `https://api.brex.com`.
- A token follows its user: if that admin leaves or is deactivated, the token stops. Create it as a durable admin.
