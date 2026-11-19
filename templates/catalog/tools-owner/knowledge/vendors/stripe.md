# Stripe

Docs: https://docs.stripe.com/keys, https://docs.stripe.com/keys/restricted-api-keys, https://docs.stripe.com/mcp (checked 2026-11-16)

## Credential
- Where: Dashboard > Developers > API keys (dashboard.stripe.com/apikeys) > Create restricted key. Switch the sandbox/live toggle first: each mode has its own keys.
- Kinds: restricted key `rk_live_...` / `rk_test_...` (preferred), secret key `sk_live_...` / `sk_test_...` (full access), organization key `sk_org_...`. Publishable `pk_...` keys cannot read account data.
- Read-only: on a restricted key set each resource to Read (default is None). Write implies Read. GET needs read, POST/DELETE need write.
- Shape: Bearer `rk_...` (Stripe also accepts Basic with the key as user and an empty password).
- Variable: `STRIPE_API_KEY` (Tico's name).
- Expiry: none. A live key is shown once at creation. Rotate gives the old key up to 7 days. A key unused for payouts or transfers for 180 days loses that ability (Restore access).

## Check
- `GET https://api.stripe.com/v1/balance`: returns the balance. 401: bad or expired key. 403: not seen in docs; a restricted key without Balance read gets an invalid-request error that names the missing permission (read the body). Give the key Balance: Read, or check another path it can read.

## In Tico
- Route: API tool on the proxy, Auth `bearer` (or `basic_user`), base `https://api.stripe.com`. Use a Read level with a read-only restricted key.
- Settings (not keys): none. Connect platforms act on a connected account with a `Stripe-Account: acct_xxxx` header (set per call).
- MCP: `https://mcp.stripe.com`. OAuth sign-in, or an agent API key as `Authorization: Bearer`. Agent key = a restricted key created with "Authorizing agent access to your account" selected; it is subject to Stripe approval rules for sensitive actions. From December 2026 the MCP stops accepting full-access secret keys and untagged restricted keys (401 with an OAuth challenge). Use the REST route for plain restricted keys.

## Pitfalls
- Test and live keys are separate worlds. Name the mode in the tool name (Stripe test, Stripe live).
- A webhook signing secret (`whsec_...`, Developers > Webhooks > the endpoint) is never a tool key. Store it as its own key (`STRIPE_WEBHOOK_SECRET`) for the bot itself.
- Stripe may block calls from outside a key's access policy (IP or ASN); Tico's proxy calls leave from the Tico server address.
- Secret keys cannot be limited. Prefer a restricted key with only the resources the bot needs.
