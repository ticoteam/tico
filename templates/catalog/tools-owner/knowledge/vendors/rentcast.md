# RentCast

Docs: https://developers.rentcast.io/reference/authentication, https://developers.rentcast.io/reference/getting-started-guide, https://developers.rentcast.io/reference/security (checked 2026-11-16)

## Credential
- Where: sign in at app.rentcast.io, API dashboard (app.rentcast.io/app/api) > Create API Key.
- Kinds: API key.
- Read-only: the API is read data. A key can be restricted by IP and by endpoint in the Security tab (CIDR allowed). Which status a blocked request gets is not documented.
- Shape: header `X-Api-Key`.
- Variable: `RENTCAST_API_KEY` (unconfirmed convention).
- Expiry: none documented.

## Check
- `GET https://api.rentcast.io/v1/properties?city=Austin&state=TX&limit=1`: returns property records (documented example). It counts as a billed request. 401/403 meanings: not documented (unconfirmed).

## In Tico
- Route: API tool on the proxy, Auth `header:X-Api-Key`, address `https://api.rentcast.io/v1`.
- Settings (not keys): none.
- MCP: none found.

## Pitfalls
- Plans have a monthly request quota (free plan: up to 50 requests per month); beyond it, paid plans bill per request. Every successful request counts, including checks. Use `limit=1`.
- An endpoint restriction on the key can make some paths fail while the check passes; the failing status is not documented.
- An IP allowlist would block the Tico server unless its address is added.
