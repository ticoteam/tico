# AWS

Docs: https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html, https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html (checked 2026-11-16)

## Credential
- Where: IAM console > Users > the user > Security credentials > Create access key. The secret is shown once; a user holds at most two keys.
- Kinds: IAM user access key pair (long-lived, `AKIA...`); temporary STS credentials (key pair plus session token) from an assumed role or IAM Identity Center (SSO). AWS recommends temporary credentials over long-term keys.
- Read-only: attach an AWS managed policy to the user or role: `ReadOnlyAccess` (read across services) or `ViewOnlyAccess` (metadata and listing only, narrower). Never create keys for the root user.
- Shape: SigV4. Every request is signed from the key pair plus region and service, so it cannot go through the proxy.
- Variable: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (two keys, one per field); `AWS_SESSION_TOKEN` only for temporary credentials.
- Expiry: IAM user keys none (rotate by policy). STS and SSO credentials last hours. Tico does not refresh them yet, so a person renews them.

## Check
- On computers: `aws sts get-caller-identity` returns account id, user ARN and user id. It needs no IAM permission, so any valid key passes.
- `InvalidClientTokenId`: access key id unknown or deleted. `SignatureDoesNotMatch`: secret wrong (often a pasted space or newline), or the wrong secret for that id. Both mean the key is bad.
- `AccessDenied` / `UnauthorizedOperation`: the key is valid but its policy lacks that action (Works limited, not rejected).
- `ExpiredToken`: temporary credentials ran out.

## In Tico
- Route: on computers. Two keys (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`), both granted to the bot; the bot's SDK or CLI signs.
- Settings (not keys): `AWS_REGION` (or `AWS_DEFAULT_REGION`; `AWS_REGION` wins) and `AWS_PROFILE` are Settings on the tool (plain values every run with a level gets), never keys.
- MCP: AWS MCP server `https://aws-mcp.us-east-1.api.aws/mcp` (catalog; regional endpoints exist), reached through a local SigV4-signing proxy or AWS sign-in. Not usable with a pasted token; use the CLI route.

## Pitfalls
- Pass both halves; an id without its secret gives `SignatureDoesNotMatch`, not a missing-key error.
- A key attached to `ViewOnlyAccess` cannot read object contents or most data; use `ReadOnlyAccess` when the bot must read data.
- Assumed-role and SSO credentials are not supported yet; use an IAM user key for now.
- `AWS_PROFILE` names a profile in a config file the computer must hold; with only env keys leave it unset.
