# HubSpot

Docs: https://developers.hubspot.com/changelog/service-keys, https://developers.hubspot.com/docs/apps/legacy-apps/private-apps/overview, https://developers.hubspot.com/mcp (checked 2026-11-16)

## Credential
- Where, new: Settings > Integrations > Service Keys (or Development > Keys > Service Keys) > create, name it, pick scopes. Public beta since 2026-02-10. Needs Developer Tools access; a key can only get scopes its creator has.
- Where, legacy: Development > Legacy apps > the app > Auth tab > Show token (super admin). From 2026-10-26 no new legacy private apps can be created (2026-09-28 for accounts made after that date). Existing ones keep working.
- Kinds: service key, legacy private app token, OAuth app.
- Read-only: choose only `.read` scopes (for example `crm.objects.contacts.read`).
- Shape: Bearer `Authorization: Bearer <token>` (legacy token confirmed; service-key header format not on the changelog page, believed the same, unconfirmed).
- Variable: `HUBSPOT_ACCESS_TOKEN`.
- Expiry: none. Rotation has a 7-day grace period; HubSpot recommends every 6 months.

## Check
- `GET https://api.hubapi.com/account-info/v3/details`: returns the portal id and time zone. 401: token wrong or rotated out. 403: key lacks the scope this path needs (unconfirmed which). If so, check `GET /crm/v3/objects/contacts?limit=1` with a contacts-read key.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, base `https://api.hubapi.com`.
- Settings (not keys): none.
- MCP: `https://mcp.hubspot.com`, OAuth 2.0. An account admin first creates a user-level app on the new developer platform with read scopes (client id and secret, PKCE); no dynamic registration. Admin connects first, then others. No pasted-token option documented.

## Pitfalls
- Service keys do not support webhooks. Webhooks need a project-based app or a legacy app.
- Legacy app scopes can drop if the account is downgraded, with the token string unchanged.
- A legacy token's scopes can be read with `POST /oauth/v2/private-apps/get/access-token-info`.
