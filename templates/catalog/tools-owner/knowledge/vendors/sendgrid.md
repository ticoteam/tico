# SendGrid

Docs: https://www.twilio.com/docs/sendgrid/ui/account-and-settings/api-keys, https://www.twilio.com/docs/sendgrid/api-reference/how-to-use-the-sendgrid-v3-api/authentication (checked 2026-11-16)

## Credential
- Where: SendGrid console > Settings > API Keys > Create API Key; name it, pick a permission level, Create & View. The key is shown once.
- Kinds: Full Access, Custom (restricted per endpoint), Billing Access (billing endpoints only), Email Address Validation Access. The key starts with `SG.`.
- Read-only: choose Custom and give only read levels (such as Mail Send off, reads on). Scope names were not in the fetched docs (unconfirmed).
- Shape: Bearer.
- Variable: `SENDGRID_API_KEY`.
- Expiry: none.

## Check
- Tico uses `GET https://api.sendgrid.com/v3/user/profile`, which needs a user profile read scope (`user.profile.read`, per the assignment; unconfirmed in fetched docs). A restricted key without it gets 403 (Works (limited)). A scope-free alternative is unconfirmed; `GET /v3/scopes` lists a key's own scopes (unconfirmed). 401: key invalid or revoked.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, address `https://api.sendgrid.com`.
- Settings (not keys): none; EU-region subusers may use `api.eu.sendgrid.com` (from the catalog, unconfirmed in docs).
- MCP: none.

## Pitfalls
- A mail-only key (Mail Send) gets 403 on the profile check but sends fine.
- The 401 vs 403 meanings are not spelled out in the docs read; 403 on a valid key is the missing scope.
- Billing keys cannot send mail; use a separate key.
- Event webhook signatures use ECDSA; the public key is a separate secret and not a tool key.
