# Google (index)

Docs: https://developers.google.com/workspace/guides/create-credentials, https://developers.google.com/google-ads/api/rest/auth (checked 2026-11-16)

Tico groups every Google tool under one vendor, `google` (Google Ads, Search Console, Analytics, Gmail, Calendar, Drive,
Cloud). Read the guide for the product, not this one, to set it up.

## Which guide
- Ads spend and campaigns: `google-ads.md`
- Search Console (queries, pages, sitemaps): `google-search-console.md`
- Analytics 4 reports: `google-analytics.md`
- Gmail, Calendar, Drive, Workspace admin: `google-workspace.md`
- Gemini models: `gemini.md`

## Credential kinds (they are not interchangeable)
- **API key**: one static string, no user. Works only for APIs that allow keys (Gemini, Maps). Goes on the proxy.
- **OAuth client** (client id + secret) plus a **refresh token**: acts as one person who signed in. Several values; Tico
  does not run the OAuth flow for a plain API tool, so it is on computers.
- **Service account**: a robot identity with a JSON key file (`client_email`, `private_key`). The Google library signs a
  JWT itself. On computers, as a file key with variable `GOOGLE_APPLICATION_CREDENTIALS`. It sees nothing until the
  resource is shared with its email (a Search Console property, a GA4 property, a Drive file) or an IAM role is granted.
- **Domain-wide delegation**: a service account a Workspace super admin authorizes (Admin console > Security > Access and
  data control > API controls > Manage Domain Wide Delegation: client id + comma-separated scopes). It then acts as any
  user in the domain. Changes can take up to 24 hours. Only Workspace domains; not for personal Gmail.

## In Tico
- Route: on computers for service-account JSON and OAuth; API tool on the proxy only for API keys.
- Settings (not keys): project id, property id, site URL, customer id.
- MCP: Google publishes remote MCP servers per product (see the product guide); none covers all of Google.

## Pitfalls
- IAM roles on a Cloud project do not give access to Workspace data or to Search Console or GA4 properties.
- Service-account key creation is often blocked by an organization policy.
- An OAuth consent screen left in "Testing" kills refresh tokens after 7 days.
