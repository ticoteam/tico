# Meta (Marketing API and Pages)

Docs: https://developers.facebook.com/docs/marketing-api/system-users, https://developers.facebook.com/docs/marketing-api/overview/authorization, https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived (checked 2026-11-16)

## Credential
- Where: developers.facebook.com > My Apps > create a Business app. Then Business Manager (Business settings) > Users > System users > Add; assign the ad account and Pages as assets; Generate token (Business Manager help covers the steps; exact button labels unconfirmed).
- Kinds: system user token (no person tied to it; best for bots); user token; Page token. Meta's system users page says system-user actions cannot be attributed to a person.
- Read-only: permission `ads_read` (reports) vs `ads_management` (read and manage). Request both only if you need both. For Pages, `pages_*` permissions per Page (names unconfirmed).
- Shape: Bearer access token (`Authorization: Bearer <token>`).
- Variable: `META_ACCESS_TOKEN` (Tico service `meta-ads`).
- Expiry: user token short-lived, exchange for long-lived (~60 days) via `GET oauth/access_token?grant_type=fb_exchange_token` with app id and secret, from a server. A long-lived Page token (from `{user-id}/accounts` with a long-lived user token) has no expiry date but is invalidated under some conditions. System-user token expiry: catalog says it does not expire (unconfirmed in current docs).

## Check
- `GET https://graph.facebook.com/v26.0/me`: returns the token's user or system user. 401 (code 190): token expired or invalid. 403: permission or asset missing. In the query string a bad token answers 400, so send it as Bearer.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, base `https://graph.facebook.com/v26.0`.
- Settings (not keys): ad account id with prefix, `act_1234567890`; Page id.
- MCP: `https://mcp.facebook.com/ads`, OAuth sign-in with a Meta Business login (open beta per catalog; third-party source, unconfirmed). Not for Pages. Connect it in Tools > Add > MCP server with Sign in.

## Pitfalls
- Standard access works on ad accounts you own. Managing other people's accounts needs Advanced access: App Review plus business verification, and 500 Marketing API calls in 15 days with under 15% errors to keep it.
- An `appsecret_proof` requirement (HMAC of the token with the app secret) can make calls fail; it cannot be done on the proxy (catalog note).
- The Graph version in the URL is retired after about two years; update it.
