# Mercury

Docs: https://docs.mercury.com/reference/getting-started-with-your-api, https://docs.mercury.com/docs/mcp (checked 2026-11-16)

## Credential
- Where: Mercury dashboard > organization name (top left) > All Settings > Tokens > Create an API Token. Needs a user with permission to do so.
- Kinds: Read Only, Read and Write, Custom (pick scopes).
- Read-only: choose Read Only. It needs no IP allowlist. Scopes are fixed at creation (not editable, per the catalog).
- Shape: Bearer `Authorization: Bearer <token>`; Basic with the token as user and an empty password also works.
- Variable: `MERCURY_API_KEY`.
- Expiry: none stated. Tokens can be deleted for inactivity; any call keeps one active. The inactivity period is not stated (unconfirmed).

## Check
- `GET https://api.mercury.com/api/v1/accounts`: lists bank accounts. 401: token rejected, deleted, or from the wrong source. 403: not documented; with a Read Only token expect none on this path. A write-capable token called from a non-allowlisted address is refused (status code unconfirmed).

## In Tico
- Route: API tool on the proxy, Auth `bearer`, base `https://api.mercury.com/api/v1`. Use a Read Only token with a Read level.
- Settings (not keys): none.
- MCP: `https://mcp.mercury.com/mcp`, OAuth sign-in, read-only per the catalog notes (not confirmed on Mercury's page). Sessions reportedly re-authenticate about every 3 days (unconfirmed).

## Pitfalls
- Read and Write tokens require an IP allowlist ("Requires an IP whitelist for security purposes"). Calls through Tico's proxy leave from the Tico server address; calls on computers leave from that computer. Allowlist the one the tool uses, and give a bot a Read Only token when you can.
- Custom tokens with a payment scope (for example `RequestSendMoney`) start payments that still need admin approval.
