# Twilio

Docs: https://www.twilio.com/docs/usage/requests-to-twilio, https://www.twilio.com/docs/iam/api-keys, https://www.twilio.com/docs/iam/api-keys/keys-in-console (checked 2026-11-16)

## Credential
- Where: Console > Settings > Account settings > API keys & auth tokens > Create API key (legacy Console: Admin > Account management > Keys & credentials > API keys & tokens). Copy the secret before finishing. The Auth Token is on the same page.
- Kinds: API key (SID starting `SK`, plus a secret), or Account SID (`AC...`) + Auth Token. Key types: Main (same power as SID + Auth Token, Console only), Standard (everything except Accounts and Keys), Restricted (per-resource permissions; REST creation for v1 only).
- Read-only: a Restricted key with read permissions only. No read-only flag on Standard.
- Shape: Basic. API key: user = API key SID, password = secret. Or user = Account SID, password = Auth Token. Twilio prefers API keys for applications.
- Variables (Tico template `twilio`, manage skill 2b): `TWILIO_ACCOUNT_SID` (id, `AC` + 32 hex), `TWILIO_AUTH_TOKEN` (secret), `TWILIO_API_KEY` (secret, `SK` + 32 hex; also read as `TWILIO_API_KEY_SID`, `TWILIO_API_SID`), `TWILIO_API_SECRET` (secret; also `TWILIO_API_KEY_SECRET`). The token and the API key pair are each optional; one pair is needed.
- Expiry: none stated.

## Check
- `GET https://api.twilio.com/2010-04-01/Accounts.json`: lists the account (and subaccounts). 401: SID or secret wrong. 403: a Standard or Restricted key cannot read Accounts (the docs say it needs a Main key or SID + Auth Token), so Works (limited) is normal. A better check for a Standard key: `/2010-04-01/Accounts/<AccountSid>/Messages.json?PageSize=1`.

## In Tico
- Route: one Twilio tool on the proxy, base `https://api.twilio.com`. Paste every value at once (Tools > Add > API, Keys box, or Keys > Paste); Tico stores the secrets as keys and the Account SID as a Setting. The proxy builds Basic from `TWILIO_API_KEY` + `TWILIO_API_SECRET` when both are there, else `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN`; the bot calls `$TICO_TOOL_TWILIO_URL/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/...` with Basic as the docs say, its secrets being proxy tokens.
- Settings (not keys): `TWILIO_ACCOUNT_SID`: the bot gets it as it is (it is in every path); subaccount SIDs.
- MCP: remote `https://mcp.twilio.com/docs` is docs only, no auth. API tools use the local `@twilio-alpha/mcp` (on computers).

## Pitfalls
- With an API key the user is the `SK` SID, but the paths still use the Account SID.
- A subaccount needs its own SID and token, or a parent Main key. Do not use parent credentials where a guide wants the subaccount's.
- Webhooks are signed `X-Twilio-Signature` with the Auth Token; that is a signature check, not a tool key.
- Twilio test credentials are separate from live ones.
