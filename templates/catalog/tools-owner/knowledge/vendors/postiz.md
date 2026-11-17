# Postiz

Docs: https://docs.postiz.com/public-api/introduction (checked 2026-11-16)

## Credential
- Where: Postiz app > Settings > API key (location not shown in the fetched page: unconfirmed).
- Kinds: Public API key; OAuth tokens (start with `pos_`) for apps built for other Postiz users.
- Read-only: no read-only option documented.
- Shape: header `Authorization: <key>` with the raw key, no `Bearer` prefix.
- Variable: `POSTIZ_API_KEY`.
- Expiry: none documented.

## Check
- `GET https://api.postiz.com/public/v1/integrations` (lists connected social channels). 401: key rejected (status for a bad key unconfirmed). 403: unconfirmed.

## In Tico
- Route: API tool on the proxy, Auth `header:Authorization`, base `https://api.postiz.com/public/v1` for cloud.
- Settings (not keys): the base URL for a self-hosted Postiz; the docs list web addresses (`http://localhost:4200` source, `http://localhost:4007` Docker compose) but do not state the self-hosted API base (unconfirmed; usually `<host>/public/v1`).
- MCP: none confirmed.

## Pitfalls
- Rate limit is on create-post only: 90 requests/hour (100 on cloud). Batch posts into one request. Self-hosters change it with `API_LIMIT`.
- A self-hosted Postiz on a private address cannot be reached by the proxy (public https only).
- Channel connections (X, LinkedIn, etc.) are authorized inside Postiz by a person; the API key does not create them.
