# ElevenLabs

Docs: https://elevenlabs.io/docs/api-reference/authentication, https://elevenlabs.io/docs/eleven-api/guides/how-to/best-practices/security, https://elevenlabs.io/docs/api-reference/voices/search (checked 2026-11-16)

## Credential
- Where: elevenlabs.io > profile menu > API keys (UI path unconfirmed; the docs page did not spell it out).
- Kinds: user API key; service account keys in a workspace (Workspace settings, unconfirmed path).
- Read-only: a key can be restricted by endpoint scope, a credit quota and an IP allowlist (CIDR). Scope names such as `voices_read`, `user_read` appear in third-party docs only (unconfirmed). Turn Restrict Key on and give only what is needed.
- Shape: header `xi-api-key`.
- Variable (Tico template `elevenlabs`): `ELEVENLABS_API_KEY` (`sk_` + 48 hex; also read as `ELEVENLABS_KEY`, `ELEVENLABS_XI_API_KEY`).
- Expiry: optional, 15 minutes to 30 days at creation; editable later.

## Check
- Tico's current check `GET https://api.elevenlabs.io/v1/user` needs the User permission. Third-party reports say a restricted key without it fails (the exact status is unconfirmed; the task notes 401, which Tico reads as Key rejected).
- Better for a restricted key: `GET https://api.elevenlabs.io/v2/voices` (documented; its required permission is not stated, expected to be Voices read, unconfirmed). 401: key invalid or lacks the scope (see Pitfalls). 403: request from an IP outside the key's allowlist.

## In Tico
- Route: API tool on the proxy; the proxy puts the key in `xi-api-key`. Address `https://api.elevenlabs.io`.
- Settings (not keys): regional host if the workspace is not US: the docs list EU, India and Singapore servers (hosts unconfirmed).
- MCP: `https://api.us.elevenlabs.io/v1/mcp`; remote auth unverified, so prefer the API tool.

## Pitfalls
- A restricted key can read as Rejected on `/v1/user` while working for the scopes it has. Point the check at a path the key can read, or trust real calls over the check.
- User permission reportedly changed from No access/Read/Write to No access/Access; an old key set to Read can have lost it.
- A key with an IP allowlist fails from the Tico server unless its address is listed.
