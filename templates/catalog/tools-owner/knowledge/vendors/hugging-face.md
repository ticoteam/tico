# Hugging Face

Docs: https://huggingface.co/docs/hub/security-tokens, https://huggingface.co/docs/hub/hf-mcp-server (checked 2026-11-16)

## Credential
- Where: Settings > Access Tokens (`huggingface.co/settings/tokens`) > New token; pick a type and a name.
- Kinds: user access token with a role: `read`, `write` or `fine-grained`.
- Read-only: a `read` token reads repositories you can read. A fine-grained token limits access to chosen models, datasets, Spaces or organizations; use it for production. Requesting gated-repo access needs a fine-grained token with that permission.
- Shape: Bearer (`hf_...`).
- Variable: `HF_TOKEN`.
- Expiry: a token unused for a year expires; each use resets it. An email comes 30 days before; an expired token shows Invalidated and can be refreshed for a new value.

## Check
- `GET https://huggingface.co/api/whoami-v2`: the account and token role. 401: token invalid or invalidated. 403: valid but the organization denied or revoked it.

## In Tico
- Route: API tool on the proxy, Auth `bearer`, base `https://huggingface.co`. Inference Providers also take the token as Bearer.
- Settings (not keys): none.
- MCP: `https://huggingface.co/mcp` (generate the client snippet at `huggingface.co/settings/mcp`); how it authenticates a pasted token is not stated in the docs read (unconfirmed).

## Pitfalls
- In an organization with a token policy, a fine-grained token stays Pending until an admin approves it; denied or revoked tokens get 403 on that organization's resources.
- An organization may allow only fine-grained tokens; `read`/`write` tokens then get 403.
- One token per use makes revoking safe.
