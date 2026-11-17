# QuickBooks Online

Docs: https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/faq, https://developer.intuit.com/app/developer/qbo/docs/get-started/start-developing-your-app (checked 2026-11-16; Intuit's pages render client-side, so figures below come from search excerpts)

## Credential
- Where: developer.intuit.com > My Hub > App dashboard > create an app, take its Client ID and Client Secret (Keys and credentials; sandbox and production keys are separate), then run the OAuth 2.0 authorization-code flow (the OAuth Playground can do it by hand) to get an access token, a refresh token and the `realmId` (the company id).
- Kinds: OAuth 2.0 only. There is no static API key or personal token.
- Read-only: scopes on the app. `com.intuit.quickbooks.accounting` covers accounting (read and write, no read-only variant known).
- Shape: Bearer access token. Fields: client id, client secret, refresh token, realm id.
- Variable: `QUICKBOOKS_ACCESS_TOKEN` (Tico's convention; not in the service list).
- Expiry: access token 1 hour (`expires_in` 3600). Refresh token 100 days rolling, extended on use, and a newer hard cap of about 5 years is reported (unconfirmed). Intuit returns a new refresh token value periodically; save the newest from every refresh, the old one stops working.

## Check
- `GET https://quickbooks.api.intuit.com/v3/company/<realmId>/companyinfo/<realmId>?minorversion=<n>` (sandbox host `sandbox-quickbooks.api.intuit.com`). 401: access token expired or revoked (expected hourly). 403: scope or app permission missing (unconfirmed).

## In Tico
- Route: Tico does not run the refresh flow for an API tool yet, and a 1-hour access token is not a static key. Use on computers: store client id, client secret and refresh token as keys; the bot refreshes at `https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer` and saves the new refresh token (a person must re-seed it if it is lost). Or an MCP sign-in if one is available.
- Settings (not keys): `realmId`, and environment (sandbox or production).
- MCP: none hosted and verified. Intuit's `quickbooks-online-mcp-server` is local and needs your own Intuit app.

## Pitfalls
- Refresh token rotation: a bot that refreshes and does not store the new token loses access after the next refresh.
- Production keys need Intuit's app review; sandbox companies hold test data only.
- Every request URL carries the realmId, so one token reaches one company.
