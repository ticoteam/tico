# Raygun

Docs: https://raygun.com/documentation/product-guides/raygun-api/, https://raygun.com/documentation/product-guides/raygun-mcp-server/ (checked 2026-11-16)

## Credential
- Where: Accounts & Billing > My Settings > Personal Access Tokens (also `app.raygun.com/user/tokens`), create a token and select scopes. Every user on the plan can make one for the apps they can access.
- Kinds: personal access token (API v3); application API key (ingestion only, header `X-ApiKey`, cannot read data).
- Read-only: select only `:read` scopes, for example `applications:read`, `cr.errors:read`, `deployments:read`, `teams:read`, `rum.sessions:read`, `customers:read`.
- Shape: Bearer.
- Variable: `RAYGUN_PAT`.
- Expiry: a 401 says the token is missing or expired; the length is not stated (unconfirmed).

## Check
- `GET https://api.raygun.com/v3/applications`: lists applications (needs `applications:read`; confirm the path, unconfirmed). 401: header missing or token expired. 403: token lacks that scope.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, base `https://api.raygun.com`; or MCP server.
- Settings (not keys): none.
- MCP: `https://api.raygun.com/v3/mcp`. OAuth is recommended; a PAT works as `Authorization: Bearer <PAT>` for clients without OAuth. OAuth covers only the MCP endpoint, not the rest of API v3.

## Pitfalls
- The application API key cannot call API v3, and a PAT cannot ingest errors.
- A PAT with too few scopes gets 403 per endpoint; add scopes by creating a new token.
- Create the token under the account that can see the needed apps.
