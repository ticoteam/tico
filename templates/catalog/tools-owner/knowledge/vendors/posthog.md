# PostHog

Docs: https://posthog.com/docs/api, https://posthog.com/docs/api/personal-api-keys (checked 2026-11-16)

## Credential
- Where: Settings > User > Personal API keys (`us.posthog.com/settings/user-api-keys`) > Create a personal API key; label it, choose scopes, copy it at once (shown once). Up to 10 keys.
- Kinds: personal API key (`phx_...`, for private endpoints); project token (`phc_...`, ingestion only, public POST endpoints, needs no secret); project secret API key (beta, server-to-server, one project, only the scopes granted).
- Read-only: choose only `:read` scopes (for example `insight:read`); `*` means full access.
- Shape: Bearer (`Authorization: Bearer <key>`).
- Variables (Tico template `posthog`, manage skill 2b): `POSTHOG_API_KEY` (secret, `phx_...`; also `POSTHOG_PERSONAL_API_KEY`), `POSTHOG_PROJECT_API_KEY` (secret, `phc_...`, optional), `POSTHOG_HOST` (setting, also `POSTHOG_API_HOST`), `POSTHOG_PROJECT_ID` (id). A second account of one company is a second tool whose variables carry its label (`POSTHOG_CRO_API_KEY`).
- Expiry: none stated.

## Check
- `GET https://us.posthog.com/api/users/@me/` (EU: `https://eu.posthog.com`): the user. 401: key invalid. 403: valid but the scope is missing; a key without the user scope may get 403 here, so a project path is a better check (unconfirmed).

## In Tico
- Route: one PostHog tool on the proxy; the proxy sends `Authorization: Bearer` with `POSTHOG_API_KEY`. Paste key, host and project id together: the key becomes a key, host and project id Settings.
- Settings (not keys): `POSTHOG_HOST` (`https://us.posthog.com`, `https://eu.posthog.com` or a self-hosted domain) and `POSTHOG_PROJECT_ID`, used in paths under `/api/projects/<id>/`.
- MCP: `https://mcp.posthog.com/mcp` by OAuth sign-in or a personal key as Bearer (catalog).

## Pitfalls
- Private endpoints use `us.posthog.com`; `us.i.posthog.com` is the public capture host and rejects private calls.
- A `phc_` token cannot read data.
- Without a project, results come from the last project used in the UI; a 400 with `attr: project_id` means no project could be chosen.
