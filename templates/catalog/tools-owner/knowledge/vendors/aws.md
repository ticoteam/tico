# AWS

Docs: https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html, https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html (checked 2026-11-16)

## Credential
- Where: IAM console > Users > the user > Security credentials > Create access key. The secret is shown once; a user holds at most two keys.
- Kinds: IAM user access key pair (long-lived, `AKIA...`); temporary STS credentials (key pair plus session token) from an assumed role or IAM Identity Center (SSO). AWS recommends temporary credentials over long-term keys.
- Read-only: attach an AWS managed policy to the user or role: `ReadOnlyAccess` (read across services) or `ViewOnlyAccess` (metadata and listing only, narrower). Never create keys for the root user.
- Shape: SigV4. Every request is signed from the key pair plus region and service, so it cannot go through the proxy.
- Variables (Tico template `aws`, manage skill 2b): secrets `AWS_ACCESS_KEY_ID` (`AKIA`/`ASIA` + 16), `AWS_SECRET_ACCESS_KEY` (40 characters), `AWS_SESSION_TOKEN` (optional, temporary credentials only); settings `AWS_REGION` (also `AWS_DEFAULT_REGION`) and `AWS_PROFILE`, both optional.
- Expiry: IAM user keys none (rotate by policy). STS and SSO credentials last hours. Tico does not refresh them yet, so a person renews them.

## Check
- On computers: `aws sts get-caller-identity` returns account id, user ARN and user id. It needs no IAM permission, so any valid key passes.
- `InvalidClientTokenId`: access key id unknown or deleted. `SignatureDoesNotMatch`: secret wrong (often a pasted space or newline), or the wrong secret for that id. Both mean the key is bad.
- `AccessDenied` / `UnauthorizedOperation`: the key is valid but its policy lacks that action (Works limited, not rejected).
- `ExpiredToken`: temporary credentials ran out.

## In Tico
- Route: one AWS tool, delivery on computers: paste the values together; the keys and settings reach the run and the bot's SDK or CLI signs (SigV4). The proxy carries none of its calls. AWS is a sensitive vendor: grouping older separate AWS keys into one tool came to you as a "Group AWS?" task, never automatic.
- Settings (not keys): `AWS_REGION` (or `AWS_DEFAULT_REGION`; `AWS_REGION` wins) and `AWS_PROFILE`, on the tool's Settings.
- MCP: AWS MCP server `https://aws-mcp.us-east-1.api.aws/mcp` (catalog; regional endpoints exist), reached through a local SigV4-signing proxy or AWS sign-in. Not usable with a pasted token; use the CLI route.

## Pitfalls
- Pass both halves; an id without its secret gives `SignatureDoesNotMatch`, not a missing-key error.
- A key attached to `ViewOnlyAccess` cannot read object contents or most data; use `ReadOnlyAccess` when the bot must read data.
- Temporary credentials (`AWS_SESSION_TOKEN`) can be stored, but Tico does not renew them: they stop when they expire. Use an IAM user key for a bot that runs unattended.
- `AWS_PROFILE` names a profile in a config file the computer must hold; with only env keys leave it unset.
