# Sentry

Docs: https://docs.sentry.io/api/auth/, https://docs.sentry.io/api/guides/create-auth-token/, https://docs.sentry.io/api/guides/ (checked 2026-11-16)

## Credential
- Where: Organization token: Settings > Organization Settings > Custom Integrations > Create New Integration > Internal Integration; pick permissions, Save, copy under Tokens (needs Manager or Admin). User token: User settings > Personal Tokens.
- Kinds: internal integration token (not tied to a person; preferred), user auth token (acts as you), organization auth token (fixed CI scopes), OAuth tokens, DSN (ingest only, not an API key).
- Read-only: pick read scopes only, for example `org:read`, `project:read`, `event:read`, `team:read`. Each endpoint lists its needed scope.
- Shape: Bearer.
- Variable: `SENTRY_AUTH_TOKEN`.
- Expiry: not stated for internal integration or user tokens (unconfirmed); OAuth access tokens last 30 days.

## Check
- `GET https://sentry.io/api/0/organizations/`: lists organizations the token can see (the current Tico check path `/api/0/` answers without a token, so it proves little). 401: token invalid. 403: valid but missing scope.

## In Tico
- Route: API tool on the proxy, Auth `bearer`.
- Settings (not keys): region host and organization slug. US data uses `https://us.sentry.io`, EU data `https://de.sentry.io`; `sentry.io` also works for most calls. Not every resource is on a regional host.
- MCP: `https://mcp.sentry.dev/mcp` by OAuth sign-in. A user token may be sent in the Authorization header with the `Sentry-Bearer` scheme, not plain `Bearer` (third-party source, unconfirmed).

## Pitfalls
- Do not use a DSN as an API key; it only sends events.
- Self-hosted Sentry has its own host.
- Organization tokens carry fixed scopes and cannot be edited.
