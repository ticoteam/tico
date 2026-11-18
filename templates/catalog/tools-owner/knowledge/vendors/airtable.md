# Airtable

Docs: https://airtable.com/developers/web/guides/personal-access-tokens, https://airtable.com/developers/web/api/get-user-id-scopes, https://airtable.com/developers/agents/mcp/getting-started (checked 2026-11-16)

## Credential
- Where: airtable.com/create/tokens > Create new token; name it, add scopes, add bases or workspaces.
- Kinds: personal access token (`pat...`); OAuth integration. Legacy API keys are gone.
- Read-only: choose only read scopes, such as `data.records:read` and `schema.bases:read`, and only the bases needed. The token reads and writes only the bases and workspaces assigned to it.
- Shape: Bearer.
- Variable: `AIRTABLE_API_KEY` (Tico). Airtable's own convention: unconfirmed.
- Expiry: none documented for PATs; OAuth access tokens last 60 minutes with refresh (catalog).

## Check
- `GET https://api.airtable.com/v0/meta/whoami`: returns the user `id` (and `email` with scope `user.email:read`). No scope required, so it works for a token with any scopes. 401: token invalid or revoked. 403: not expected here.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, address `https://api.airtable.com`.
- Settings (not keys): base id (`app...`) goes in the path or the task, not a key.
- MCP: `https://mcp.airtable.com/mcp`. OAuth sign-in (supports dynamic client registration) or a PAT; header format for the PAT is not stated in the docs read (Bearer expected, unconfirmed). Scopes listed: data.records:read/write, schema.bases:read/write, data.recordComments:read/write, workspacesAndBases:read.

## Pitfalls
- `whoami` succeeding does not show base access; a call to a base outside the token returns 403 or 404 (status unconfirmed).
- Changing a token's scopes or bases in the dashboard takes effect on the same token.
