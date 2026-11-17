# OpenRouter

Docs: https://openrouter.ai/docs/api-reference/authentication, https://openrouter.ai/docs/api-reference/limits, https://openrouter.ai/docs/guides/overview/auth/management-api-keys (checked 2026-11-16)

## Credential
- Where: openrouter.ai/keys > Create key (name, optional credit limit). Management keys: openrouter.ai/settings/management-keys > Create New Key.
- Kinds: API key (`sk-or-v1-...`); management key (called provisioning key in older docs) for key, workspace, guardrail and budget administration; OAuth PKCE mints a user-owned key for an app.
- Read-only: API keys have no read-only mode; set a credit limit (with daily, weekly or monthly reset) to cap spend. Management keys have a fixed access level, read/write or read only.
- Shape: Bearer.
- Variable: `OPENROUTER_API_KEY`.
- Expiry: none by default; keys made during onboarding expire after 180 days; management keys should get an expiry and expire with 401 `API key expired`.

## Check
- `GET https://openrouter.ai/api/v1/key`: returns `data` with `label`, `limit`, `limit_remaining`, `usage`, `is_free_tier`. 401: key invalid, expired or out of access. Docs do not say if a management key is accepted here (unconfirmed).

## In Tico
- Route: API tool on the proxy, Auth `bearer`, address `https://openrouter.ai`.
- Settings (not keys): none.
- MCP: none.

## Pitfalls
- Management keys cannot call completion endpoints; they only administer.
- A key at its credit limit gets rejected on requests until the limit is raised; the check still passes.
- A key without a limit can spend the whole balance including auto top-ups.
