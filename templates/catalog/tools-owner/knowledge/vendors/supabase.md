# Supabase

Docs: https://supabase.com/docs/guides/api/api-keys, https://supabase.com/docs/reference/api/introduction, https://supabase.com/docs/guides/getting-started/mcp (checked 2026-11-16)

## Credential
- Where: Project API keys: Dashboard > Settings > API Keys. Management PAT: `https://supabase.com/dashboard/account/tokens` > generate.
- Kinds: (1) publishable key `sb_publishable_...` (low privilege, RLS applies; legacy `anon` JWT); (2) secret key `sb_secret_...` (bypasses RLS; legacy `service_role` JWT); (3) management API PAT (scoped or "Legacy" full-account); (4) Postgres connection URL. Legacy keys are being deprecated by end of 2026.
- Read-only: publishable key plus RLS policies. A scoped PAT limits organizations, projects and permissions. For SQL, MCP offers `read_only=true`.
- Shape: project keys in header `apikey` (not Bearer; they are not JWTs). Management PAT as Bearer to `https://api.supabase.com`.
- Variable: `SUPABASE_ACCESS_TOKEN` (PAT), `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_ROLE_KEY` (project key), `DATABASE_URL`.
- Expiry: PAT expiry is chosen at creation; project keys none.

## Check
- Management: `GET https://api.supabase.com/v1/projects` (unconfirmed path; it lists projects). 401: PAT invalid. 403: scoped PAT lacks that organization or project.
- Project: `GET https://<project-ref>.supabase.co/rest/v1/` with header `apikey`.

## In Tico
- Route: API tool on the proxy. Management PAT: Auth `bearer`, base `https://api.supabase.com`. Project keys: Auth `header:apikey`, base `https://<project-ref>.supabase.co` (project ref is a Setting). Database URL: a key on computers.
- Settings (not keys): project ref.
- MCP: `https://mcp.supabase.com/mcp` by OAuth sign-in, or a PAT as `Authorization: Bearer`. URL params `project_ref=<id>` and `read_only=true`.

## Pitfalls
- A secret key sent from a browser user agent gets 401; call from a server.
- Secret and `service_role` keys bypass RLS: grant only to bots that need it.
- Three unrelated credentials: a PAT does not work on a project's REST API, and a project key does not work on the management API.
