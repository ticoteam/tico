# OpenAI

Docs: https://platform.openai.com/docs/api-reference/authentication, https://help.openai.com/en/articles/8867743-assign-api-key-permissions (checked 2026-11-16; both pages blocked direct fetch, facts from the catalog and search excerpts)

## Credential
- Where: platform.openai.com > Dashboard > API keys > Create new secret key (pick the project; exact menu labels unconfirmed).
- Kinds: project API key (user or service account); a separate admin key for the organization Admin API.
- Read-only: when creating a key set Permissions to Read only (read on all endpoints), or Restricted to pick None / Read / Write per endpoint. All is the default.
- Shape: Bearer.
- Variable: `OPENAI_API_KEY`.
- Expiry: none.

## Check
- `GET https://api.openai.com/v1/models`: lists models. 401: key invalid or revoked. 403: a Restricted key without the Models read permission (key valid, limited).

## In Tico
- Route: API tool on the proxy, Auth `bearer`. Level Read works with a Read only key or on any key (only GET goes through).
- Settings (not keys): optional headers `OpenAI-Organization` and `OpenAI-Project` when a key must act in a specific org or project (unconfirmed in current docs; the catalog lists them as optional).
- MCP: none (only a docs MCP exists).

## Pitfalls
- A project key only sees its own project's resources; usage and billing endpoints and org management need an admin key, a different credential.
- Admin keys are not for model calls; keep them as a separate tool.
- A Restricted key missing a permission gets 403 on that endpoint, so a Working or Works (limited) check says nothing about other endpoints.
- The secret is shown once at creation.
