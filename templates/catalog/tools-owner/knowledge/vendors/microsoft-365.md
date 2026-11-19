# Microsoft 365 / Microsoft Graph

Docs: https://learn.microsoft.com/en-us/graph/auth-v2-service, https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app (checked 2026-11-16)

## Credential
- Where: Microsoft Entra admin center (entra.microsoft.com) > Entra ID > App registrations > New registration (single tenant). Record Application (client) ID and Directory (tenant) ID. API permissions > Add a permission > Microsoft Graph > Application permissions > pick them > Grant admin consent. Certificates & secrets > New client secret (menu path from the portal, not in the fetched docs).
- Kinds: app registration with client secret, certificate, or federated credential (app-only); or delegated (a signed-in person).
- Read-only: choose only `.Read`/`.Read.All` application permissions (for example `User.Read.All`, `Mail.Read`). Application permissions always need admin consent and apply tenant-wide; Exchange RBAC for Applications can limit mailboxes (catalog).
- Shape: three fields (tenant id, client id, secret). The bot POSTs `grant_type=client_credentials`, `scope=https://graph.microsoft.com/.default` to `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`, then sends `Authorization: Bearer <access token>`.
- Variable: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (the Azure SDK convention).
- Expiry: client secret max 24 months (catalog); access token ~1 hour (3599 s in the doc).

## Check
- `GET https://graph.microsoft.com/v1.0/organization` with a minted token (needs `Organization.Read.All`); a least-privilege app can instead `GET /v1.0/users?$top=1` if it holds `User.Read.All` (unconfirmed which needs less). 401: token invalid or expired. 403: permission not consented.

## In Tico
- Route: on computers (three keys, one per field). Tico cannot run client credentials or hold the short-lived token on the proxy.
- Settings (not keys): tenant id and client id are not secret: Settings on the tool.
- MCP: Enterprise MCP `https://mcp.svc.cloud.microsoft/enterprise` is read-only Entra/Graph, OAuth with a static client (catalog): Tools > Add > MCP server, Sign in with the Entra app's client id and secret. Agent 365 servers (mail, calendar, Teams, SharePoint) are per-tenant URLs.

## Pitfalls
- Changing permissions means admin consent again; the portal edit alone does nothing.
- Delegated permissions need a person signed in; use application permissions for a bot.
- An expired secret returns 401 `invalid_client`; put the renewal date in the task.
- Use the secret's Value, shown once, not its Secret ID.
