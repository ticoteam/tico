# PostHog

Docs: https://posthog.com/docs/api, https://posthog.com/docs/api/personal-api-keys (checked 2026-11-16)

## Credential
- Where: Settings > User > Personal API keys (`us.posthog.com/settings/user-api-keys`) > Create a personal API key; label it, choose scopes, copy it at once (shown once). Up to 10 keys.
- Kinds: personal API key (`phx_...`, for private endpoints); project token (`phc_...`, ingestion only, public POST endpoints, needs no secret); project secret API key (beta, server-to-server, one project, only the scopes granted).
- Read-only: choose only `:read` scopes (for example `insight:read`); `*` means full access.
- Shape: Bearer (`Authorization: Bearer <key>`).
- Variable: `POSTHOG_API_KEY` (personal key). Keep the `phc_` project token as a separate value.
- Expiry: none stated.

## Check
- `GET https://us.posthog.com/api/users/@me/` (EU: `https://eu.posthog.com`): the user. 401: key invalid. 403: valid but the scope is missing; a key without the user scope may get 403 here, so a project path is a better check (unconfirmed).

## In Tico
- Route: API tool on the proxy, Auth `bearer`.
- Settings (not keys): region host (`https://us.posthog.com` or `https://eu.posthog.com`; self-hosted uses its own domain) and project id, used in paths like `/api/projects/<id>/...` (the path form is unconfirmed in these docs).
- MCP: `https://mcp.posthog.com/mcp` by OAuth sign-in or a personal key as Bearer (catalog).

## Pitfalls
- Private endpoints use `us.posthog.com`; `us.i.posthog.com` is the public capture host and rejects private calls.
- A `phc_` token cannot read data.
- Without a project, results come from the last project used in the UI; a 400 with `attr: project_id` means no project could be chosen.
