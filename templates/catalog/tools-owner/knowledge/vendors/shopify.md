# Shopify

Docs: https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant, https://shopify.dev/docs/apps/build/storefront-mcp, https://shopify.dev/docs/apps/build/authentication-authorization/legacy/admin-custom-apps (checked 2026-11-16; the last was read through search only)

## Credential
- Where, legacy custom app (stores that already have one): Shopify admin > Settings > Apps and sales channels > Develop apps > the app > API credentials > Admin API access token `shpat_...`. Shown once; to get another, uninstall and reinstall the app. Shopify says new admin-created custom apps can no longer be created (from 2026-01-01); existing ones keep working.
- Where, new apps: Dev Dashboard (dev.shopify.com) > create an app, set access scopes on a version, install it on the store. No static token appears.
- Kinds: legacy custom app token; Dev Dashboard app via client credentials (`client_id` + `client_secret`, exchange for a token); authorization code or token exchange.
- Read-only: choose `read_*` scopes only (for example `read_products`, `read_orders`).
- Shape: header `X-Shopify-Access-Token: <token>`.
- Variable: `SHOPIFY_ACCESS_TOKEN`.
- Expiry: legacy token none. Dev Dashboard client-credentials token 24 hours (`expires_in` 86399).

## Check
- `GET https://<shop>.myshopify.com/admin/api/<version>/shop.json`: returns shop details (version such as `2026-10`, unconfirmed). 401: token wrong or expired (a Dev Dashboard token after 24 hours). 403: scope missing for the path.

## In Tico
- Route: legacy token = API tool on the proxy, Auth `header:X-Shopify-Access-Token`, base `https://<shop>.myshopify.com`. Dev Dashboard app = Tico does not run client-credentials exchange yet: on computers, with the client id and secret as keys and the bot exchanging for a token (`POST https://<shop>.myshopify.com/admin/oauth/access_token`, `grant_type=client_credentials`), same-organization stores only.
- Settings (not keys): the shop domain (`acme-shop.myshopify.com`), API version.
- MCP: Admin API MCP: none. Storefront (UCP) MCP is per shop, `https://<shop>/api/ucp/mcp`, customer-facing (catalog, cart), not for admin data.

## Pitfalls
- `shpss_` is the app's client secret, not an access token.
- `shop_not_permitted` on client credentials: app and store are in different Dev Dashboard organizations.
- A webhook secret is not a tool key.
