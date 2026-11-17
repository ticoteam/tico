# Google Ads API

Docs: https://developers.google.com/google-ads/api/api-policy/developer-token, https://developers.google.com/google-ads/api/rest/auth (checked 2026-11-16)

## Credential
- Where: Google Cloud Console, the project that owns the OAuth client > Google Auth platform > Clients > Create client (Desktop or Web); enable the Google Ads API on the project. Access level is applied for from the Google Ads API Overview page in Cloud Console.
- Kinds: OAuth client + user refresh token (a person signs in once), or a service account. Calls also carry a developer token header in the classic setup; the access-level doc says access is now tied to the Cloud project and no developer token or manager account is needed to apply (the `developer-token` header is not covered there: unconfirmed whether it is still required).
- Read-only: no read-only key. Scope `https://www.googleapis.com/auth/adwords` is all-or-nothing; give the signing-in user Read-only access on the account in Google Ads (Admin > Access and security).
- Shape: OAuth. Per call: `Authorization: Bearer <access token>`, `developer-token: <token>`, and `login-customer-id: <manager id, no hyphens>` only when a manager (MCC) account calls a client account.
- Variables (Tico template `google-ads`, manage skill 2b): secrets `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET` (`GOCSPX-...`), `GOOGLE_ADS_REFRESH_TOKEN` (`1//...`); ids `GOOGLE_ADS_LOGIN_CUSTOMER_ID` and `GOOGLE_ADS_CUSTOMER_ID` (also `GOOGLE_ADS_CUSTOMER_IDS`), both optional. The client libraries read these names.
- Expiry: access token ~1 hour; refresh token until revoked (7 days if the consent screen is in Testing).

## Access levels
- Test: granted when the API is enabled; test accounts only (15,000 operations/day). A production account returns `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` (v25+) or `ACTION_NOT_PERMITTED` (v24 and earlier).
- Explorer: production accounts, 2,880 operations/day, automated review.
- Basic: needs brand verification, automated review. Standard: manual review. Daily limits for these not stated in the doc.

## Check
- No proxy check: the key alone cannot call. Bots verify with `GET https://googleads.googleapis.com/<version>/customers:listAccessibleCustomers` after minting a token (endpoint from the API reference, version number current at use; not fetched today). 401: token or developer token rejected. 403: valid token, no access to that account or project not approved for production.

## In Tico
- Route: one Google Ads tool, delivery on computers: paste the values together; all six go to the run's environment and the bot's client library refreshes the token. The proxy carries none of its calls and Tico has no check for it. Finance's ad spend import uses the same values.
- Settings (not keys): `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (the manager, MCC) and `GOOGLE_ADS_CUSTOMER_ID`.
- MCP: Google's `googleads/google-ads-mcp` is local and read-only (Application Default Credentials + developer token); no remote server.

## Pitfalls
- Customer ids are 10 digits without hyphens in headers and URLs.
- Free-trial or billing-suspended Cloud projects have been rejected for Basic/Explorer; apply from another brand-verified project.
