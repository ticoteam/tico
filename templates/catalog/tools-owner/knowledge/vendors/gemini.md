# Gemini API

Docs: https://aistudio.google.com/docs/api-key (was ai.google.dev/gemini-api/docs/api-key), https://ai.google.dev/gemini-api/docs (checked 2026-11-16)

## Credential
- Where: Google AI Studio > API keys (https://aistudio.google.com/apikey) > Create API key. If the project is missing: Dashboard > Projects > Import projects.
- Kinds: API key (AI Studio). Vertex AI is a different route that uses IAM service accounts, not this key.
- Read-only: no read-only option. Restrict the key: API Keys page > Add restrictions > Restrict to Gemini API only (needs API Keys Admin or Editor). Application restrictions (IP etc.) in Cloud Console > APIs & Services > Credentials.
- Shape: header `x-goog-api-key: <key>` (documented). A `key` query parameter is not documented in current docs (unconfirmed); Tico knows it as `query:key`.
- Variable: `GEMINI_API_KEY` (the libraries also read `GOOGLE_API_KEY`, which wins when both are set).
- Expiry: none.

## Check
- Tico has no check path for Gemini: a bad key answers 400, so a check cannot tell good from bad. A candidate is `GET https://generativelanguage.googleapis.com/v1beta/models` (unconfirmed). Real calls decide.

## In Tico
- Route: API tool on the proxy, Auth `header:x-goog-api-key` (documented) or `query:key` (what Tico's service entry uses today), base `https://generativelanguage.googleapis.com`.
- Settings (not keys): model name goes in the call, not a setting.
- MCP: none.

## Pitfalls
- Docs say unrestricted standard keys are rejected by the Gemini API; restrict a key before use.
- A key restricted so it excludes the Generative Language API fails every Gemini call.
- Billing and free-tier limits are per Cloud project, so keys in one project share them.
