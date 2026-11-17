# Geocodio

Docs: https://www.geocod.io/docs/#authentication (checked 2026-11-16; the page is very long, only the authentication section was read)

## Credential
- Where: Geocodio dashboard > API Keys (exact menu label unconfirmed). An account can hold several keys.
- Kinds: API key.
- Read-only: by default a key can only call single and batch geocoding (write-only to your data; a lost key cannot read geocoded data). Lists API and distance endpoints are enabled per key in the dashboard. Use separate keys for geocoding and for lists.
- Shape: query `api_key`, or `Authorization: Bearer <key>`. Prefer Bearer so the key stays out of URLs.
- Variable: `GEOCODIO_API_KEY` (unconfirmed convention).
- Expiry: none documented.

## Check
- A key-check endpoint was not found in the docs read (unconfirmed). Use a cheap geocode: `GET https://api.geocod.io/v2.1/geocode?q=1+Main+St+Springfield` (costs a lookup against the daily allowance). 401 or 403 meaning for a bad key: unconfirmed. A valid key on a restricted endpoint returns 403.

## In Tico
- Route: API tool on the proxy, Auth `bearer` (or `query:api_key`), address `https://api.geocod.io/v2.1` (the version prefix is required).
- Settings (not keys): none.
- MCP: none (only a prototype; third-party wrappers exist).

## Pitfalls
- A 403 from a valid key means the endpoint is not enabled on that key (lists, distance), not that the key is bad.
- The base URL is versioned (`v2.1`); a newer version needs the address changed.
- A check call counts as a lookup.
