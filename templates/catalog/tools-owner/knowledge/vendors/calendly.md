# Calendly

Docs: https://developer.calendly.com/docs/authentication/scopes, https://developer.calendly.com/calendly-mcp-server (checked 2026-11-16)

## Credential
- Where: Calendly > Integrations & apps > API and webhooks > Personal access tokens (path from memory of the UI, not on the fetched pages; unconfirmed). Developer pages: developer.calendly.com.
- Kinds: personal access token (PAT), OAuth app (authorization code; PKCE).
- Read-only: scopes. New PATs get no access until scopes are requested and approved; old PATs keep full access until refreshed. Read scopes: `scheduled_events:read`, `event_types:read`, `users:read`, `organizations:read`, `webhooks:read`. A `:write` scope includes its `:read`.
- Shape: Bearer `Authorization: Bearer <token>`.
- Variable (Tico template `calendly`): `CALENDLY_API_KEY` (a JWT-shaped `eyJ...` token; also read as `CALENDLY_TOKEN`, `CALENDLY_API_TOKEN`, `CALENDLY_ACCESS_TOKEN`, `CALENDLY_PERSONAL_ACCESS_TOKEN`, `CALENDLY_PAT`).
- Expiry: none stated for PATs.

## Check
- `GET https://api.calendly.com/users/me`: returns the token's user and organization URI. 401: token invalid. 403: token valid but the scope or role does not cover the path.

## In Tico
- Route: API tool on the proxy; the proxy sends `Authorization: Bearer` with the token. Base `https://api.calendly.com`. The user's organization URI from `/users/me` is a query value for list calls, not a key.
- Settings (not keys): none.
- MCP: `https://mcp.calendly.com`. OAuth 2.1 with dynamic registration and PKCE only (scopes `mcp:scheduling:read` and `mcp:scheduling:write`). PATs are not accepted there: connect it in Tools > Add > MCP server with Sign in.

## Pitfalls
- 403 on organization-wide paths (scheduled events for the whole org, webhook subscriptions at org scope, memberships) means the token's user is not an admin or owner of the organization, or the token lacks the scope (`organizations:read`, `webhooks:write`). Community answers say so; Calendly's pages do not state the role rule (unconfirmed). Use `scope=user` calls, or a token from an admin.
- Webhook subscriptions need a paid plan (reported by users; not confirmed on a Calendly docs page). Some plans may lack API access (unconfirmed).
- The webhook signing key is not a tool key.
