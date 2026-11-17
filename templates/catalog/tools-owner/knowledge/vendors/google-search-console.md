# Google Search Console API

Docs: https://developers.google.com/webmaster-tools/v1/how-tos/authorizing, https://support.google.com/webmasters/answer/7687615, https://developers.google.com/workspace/guides/create-credentials (checked 2026-11-16)

## Credential
- Where: (1) Cloud Console > IAM & Admin > Service Accounts > Create service account; open it > Keys > Add key > Create new key > JSON (downloadable once). Enable the Search Console API on the project. (2) Search Console > the property > Settings > Users and permissions > Add user: the service account's `client_email`, permission Full or Restricted. Only a property owner can add users.
- Kinds: service account JSON (no human sign-in), or OAuth refresh token for one person.
- Read-only: scope `https://www.googleapis.com/auth/webmasters.readonly` (read/write is `.../auth/webmasters`). A Restricted user in Search Console is the least-privilege role.
- Shape: service account JSON file; the Google library signs a JWT and sends a Bearer access token.
- Variable: `GOOGLE_APPLICATION_CREDENTIALS` (path to the file key).
- Expiry: the key does not expire; access tokens last ~1 hour.

## Check
- `GET https://www.googleapis.com/webmasters/v3/sites` (list of properties the account can see; endpoint from the API reference, not fetched). It returns `{}` or no `siteEntry` when the account was added to no property. 401: bad or unsigned token. 403: valid token but API not enabled on the project or scope missing.

## In Tico
- Route: on computers, file key. Tico cannot sign the JWT on the proxy.
- Settings (not keys): the site URL as Search Console names it: `https://www.acme.example/` (URL prefix) or `sc-domain:acme.example` (Domain property).
- MCP: none from Google.

## Pitfalls
- Adding the service account in IAM does nothing; it must be a user on the property itself.
- An email group cannot be added as a user; a service account email is accepted as a Google account email (confirm it appears in the user list).
- Domain properties and URL-prefix properties are different properties with different ids.
- A property takes up to 100 non-owner users.
