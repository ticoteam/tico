# GitHub

Docs: https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api, https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens, https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md, worktree docs/github-app.md (checked 2026-11-16)

## Credential
- Where: first, Tico's own GitHub App connection (see In Tico); it needs no key. Otherwise Profile photo > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token (classic: Tokens (classic) > Generate new token (classic)).
- Kinds: fine-grained PAT (recommended by GitHub), classic PAT, GitHub App installation token, OAuth app token.
- Read-only: fine-grained PAT, choose repositories and set each permission to Read (Write includes Read). Classic scopes are broad (`repo`); no read-only repo scope.
- Shape: Bearer (`Authorization: Bearer <token>`; `token <token>` also works). Send `Accept: application/vnd.github+json`.
- Variable: `GITHUB_TOKEN` (the `gh` CLI also reads `GH_TOKEN`).
- Expiry: fine-grained default 30 days, 1 to 366 days or none unless an org caps it. Classic: chosen date. Unused tokens are removed after a year.

## Check
- `GET https://api.github.com/user`: the token's user. 401: token invalid, expired or revoked (repeated bad tries can turn into 403 for a while). 403: valid but blocked (rate limit, SSO not authorized, missing permission).
- A fine-grained PAT may lack access to `/user`-only data; the response is still 200 for a valid token. Classic tokens return an `X-OAuth-Scopes` header (unconfirmed in current docs) listing scopes.

## In Tico
- Route: built-in connection first (GitHub App; bots get short-lived tokens per run, see docs/github-app.md). Fallback: API tool on the proxy, Auth `bearer`, or on computers for `gh`/git.
- Settings (not keys): none.
- MCP: `https://api.githubcopilot.com/mcp/` (read-only: `/mcp/readonly` or header `X-MCP-Readonly: true`; toolsets via `X-MCP-Toolsets`). A PAT as Bearer is accepted per the catalog; the OAuth route needs a registered GitHub OAuth app (no dynamic registration).

## Pitfalls
- A private repo you cannot see returns 404, not 403. Read 404 as "no access" or "wrong name".
- Fine-grained PAT on an org that requires approval stays `pending` and reads only public data until an owner approves it. Owners' own tokens are approved automatically.
- An org can block classic tokens or require fine-grained ones; the rejected token gets 403.
- A fine-grained PAT covers one owner (user or one org) per token.
