# Intercom

Docs: https://developers.intercom.com/docs/build-an-integration/learn-more/authentication, https://developers.intercom.com/docs/guides/mcp (checked 2026-11-16)

## Credential
- Where: app.intercom.com > Developer Hub > create an app > Configure > Authentication > copy the access token (also under Test & Publish > Your Workspaces). Set the app's permissions there first.
- Kinds: access token of a Developer Hub app (issued to the teammate who installed it or last regenerated it), OAuth for public apps.
- Read-only: grant only read permissions in the app's permission list (conversation access is also limited by that teammate's own permissions).
- Shape: Bearer `Authorization: Bearer <token>`, `Accept: application/json`.
- Variable: `INTERCOM_ACCESS_TOKEN`.
- Expiry: not stated on the docs page.

## Check
- `GET https://api.intercom.io/me`: returns the token's admin and workspace. 401: token wrong or revoked. 403: not documented; expect a missing permission.

## In Tico
- Route: API tool on the proxy, Auth `bearer`. Base by region: US `https://api.intercom.io`, EU `https://api.eu.intercom.io`, AU `https://api.au.intercom.io` (`api.intercom.io` routes to the region by itself; use the regional host for data residency).
- Settings (not keys): region (US, EU, AU) = the base address.
- MCP: US `https://mcp.intercom.com/mcp`, EU `https://mcp.eu.intercom.com/mcp` (AU not supported yet). OAuth (recommended) or the API token as Bearer. A token needs the "Read admins" permission for tools that act as you (internal notes, articles). Fin Operator tools are OAuth only.

## Pitfalls
- Wrong region host gives failures that look like bad keys; match the host to the workspace region (app.intercom.com = US, app.eu.intercom.com = EU).
- The token belongs to a teammate's permissions; if that teammate is removed the token may stop.
- Webhook signatures (`X-Hub-Signature`) use the app's client secret, never a tool key.
