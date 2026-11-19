# Google Workspace (Gmail, Calendar, Drive, admin)

Docs: https://developers.google.com/workspace/guides/create-credentials, https://developers.google.com/gmail/api/auth/scopes, https://developers.google.com/workspace/guides/configure-mcp-servers (checked 2026-11-16)

Tico has its own built-in Google mail and calendar connection (worktree `docs/mail.md`). Use it first: Tools > Settings > Email.
- **Whole Workspace**: a Workspace admin runs one Cloud Shell command from Tico (makes a service account, turns on Gmail and Calendar, sends the key to Tico), then in Google Admin authorizes the client id and scopes Tico shows (domain-wide delegation, an admin clicks Authorize). Or upload a key file made by hand.
- **Connect your account**: one person signs in with Google (**Connect Google**).
- Tico asks only read scopes (`gmail.readonly`, `calendar.readonly`) unless an admin turns on Organize, Send or Change calendar.

## Credential (for a bot's own API use, outside the built-in connection)
- Where: Cloud Console > Google Auth platform > Clients > Create client (OAuth); or IAM & Admin > Service Accounts > Keys > Add key > JSON.
- Kinds: OAuth client + refresh token (acts as one person); service account (sees only files shared with its email, mail only through domain-wide delegation); IMAP/SMTP with an app password (needs 2-step verification).
- Read-only: scopes `gmail.readonly`, `calendar.readonly`, `drive.readonly`; `drive.file` limits Drive to files the app created or opened.
- Shape: OAuth (several fields) or service account JSON; IMAP/SMTP is user + app password.
- Variable: `GOOGLE_APPLICATION_CREDENTIALS` for the JSON; others by task.
- Expiry: refresh token until revoked (7 days if the consent screen is in Testing).

## Check
- `GET https://gmail.googleapis.com/gmail/v1/users/me/profile` needs a minted token. 401: token rejected. 403: scope missing, API disabled, or delegation not authorized.

## In Tico
- Route: built-in connection for mail and calendar; on computers for anything else.
- Settings (not keys): domain, mailbox address, user to impersonate.
- MCP: Gmail `https://gmailmcp.googleapis.com/mcp/v1`, Drive `https://drivemcp.googleapis.com/mcp/v1` (Workspace MCP developer preview; OAuth with your own Cloud project and OAuth client; catalog, not fetched).

## Pitfalls
- `gmail.readonly` and `gmail.modify` are restricted scopes: public apps need verification and a security assessment. `gmail.send` is sensitive.
- Domain-wide delegation takes only scopes authorized by name, and changes can take up to 24 hours.
- Google answers `unauthorized_client` when the domain has no delegation for that key.
