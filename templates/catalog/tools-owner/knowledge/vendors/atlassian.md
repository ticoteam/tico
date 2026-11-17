# Atlassian (Jira and Confluence Cloud)

Docs: https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/, https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/, https://developer.atlassian.com/cloud/rovo-mcp/ (checked 2026-11-16)

## Credential
- Where: `id.atlassian.com/manage-profile/security/api-tokens` > Create API token with scopes (choose Jira or Confluence, scopes, expiry) or Create API token (unscoped).
- Kinds: scoped API token (recommended), unscoped API token, OAuth 2.0 (3LO) app, service accounts (catalog).
- Read-only: pick read scopes only when creating a scoped token. Scope names are shown in that dialog (not listed here).
- Shape: Basic, user = account email, password = the token. Base64 of `email:token`.
- Variable: `ATLASSIAN_API_TOKEN`, with the email as a second value (`ATLASSIAN_EMAIL`).
- Expiry: scoped tokens 1 to 365 days, chosen at creation. Unscoped: not stated (unconfirmed).

## Check
- Unscoped: `GET https://acme.atlassian.net/rest/api/3/myself` (Jira) with Basic. Scoped: use `https://api.atlassian.com/ex/jira/<cloudId>/rest/api/3/myself`. 401: email or token wrong, or token expired. 403: valid but lacks scope or product access.

## In Tico
- Route: API tool on the proxy, Auth `basic_password` with Username set to the email. Unscoped token: base `https://acme.atlassian.net`. Scoped token: base `https://api.atlassian.com/ex/jira/<cloudId>` (Confluence: `/ex/confluence/<cloudId>`).
- Settings (not keys): site URL (`acme.atlassian.net`), cloud id for scoped tokens, the email.
- MCP: Rovo MCP server `https://mcp.atlassian.com/v2/mcp` (catalog also lists `/v1/mcp`), OAuth 2.1 sign-in, or an API token for headless use (Basic or Bearer form unconfirmed). An org admin must allow API-token auth and may restrict MCP client domains.

## Pitfalls
- A scoped token fails on the site URL; it only works on `api.atlassian.com`.
- Jira and Confluence share one token page but need separate scopes per product.
- A token acts as the person; a deactivated user breaks it.
- One Rovo MCP grant covers Jira, Confluence, Compass and Loom.
