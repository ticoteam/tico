# Vercel

Docs: https://vercel.com/docs/rest-api, https://vercel.com/docs/rest-api/authentication (checked 2026-11-16; the token-creation page was not reachable, so UI details are unconfirmed)

## Credential
- Where: Vercel dashboard > Account Settings > Tokens > Create (unconfirmed path; tokens can also be created with `POST /v3/user/tokens`).
- Kinds: account access token, created with an expiration and a scope of the personal account or one team.
- Read-only: no read-only option found. Limit the scope to one team and set an expiry; in Tico a Read level lets only GET/HEAD/OPTIONS through.
- Shape: Bearer.
- Variable: `VERCEL_TOKEN` (also what the Vercel CLI reads).
- Expiry: chosen at creation (days); renew by creating a new token.

## Check
- `GET https://api.vercel.com/v2/user`: the user. 401: not the usual answer. 403: a bad or expired token also answers 403, so a 403 here means rejected (Tico treats 401 and 403 as rejected for Vercel); a team-scoped token may also lack user access, so confirm with a team path.

## In Tico
- Route: API tool on the proxy, Auth `bearer`; or on computers for the CLI.
- Settings (not keys): `teamId` (or `slug`), sent as a query parameter on team resources (for example `?teamId=team_xxxx`). Without it calls go to the personal account.
- MCP: `https://mcp.vercel.com` by OAuth sign-in; Vercel limits it to reviewed clients, so it may refuse Tico (unconfirmed).

## Pitfalls
- 403 for a bad token, not 401: do not read it as a scope problem.
- A token scoped to a team fails on personal-account paths and the reverse; match the token's scope to the `teamId`.
- Forgetting `teamId` returns the personal account's projects, which can look empty.
