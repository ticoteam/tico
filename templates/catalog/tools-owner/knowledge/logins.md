# Website sign-ins: what Tico can do

How common website sign-ins work with a **website tool** (Tools > Add > Website: its sites and accounts) and Tico's
browser. Sources: `docs/credential-vault.md#website-logins`, `integrations/website.md` (`tico doc read` them),
`docs/computers.md#browser-host`.

## What Tico's browser does
- A website runs in a Chrome profile per account on the **browser host** (one computer an admin picks), else on the
  bot's own computer, never for a **central only** site (banks). The profile stays signed in: its browser closes after
  15 minutes idle and the sign-in is kept, so a person's sign-in usually lasts until the site itself ends the session.
- A bot drives it a verb at a time (`tico browser open|read|click|type ...`) and signs in only with `tico login fill
  <site> username|password|code`: Tico types the value on the tool's own sites over https, never shows it. The 2FA key
  never leaves the server; Tico sends only the current 6 to 8 digit code. At most 20 fills, copies and codes an hour
  per account.
- When a step needs a person, the bot runs `tico browser needs-you <site> "<what to do>"` and ends its turn. The person
  watches or takes control from the tool's page (the **live view**), does the step, clicks Done, and the bot is woken.
- An account may have no password or username: then fill is refused (`no_password`) and a person signs in through the
  live view each time the site asks.

## Pattern by pattern

| Sign-in | Bot alone? | What to set up |
|---|---|---|
| Username + password | Yes | A Shared (or Own) account with both. |
| Password + authenticator app (TOTP) | Yes | Paste the site's 2FA setup key or `otpauth://` link as the account's **2FA key** when 2FA is turned on (or turned on again). The best setup for any site a bot uses. |
| Password + SMS code | No: a person, each time the site asks | `needs-you`; the person reads the text and types it in the live view. Switch the account to an authenticator app if the site offers one. Never route the code to a bot. |
| Password + emailed code | No: a person | As SMS. Prefer the authenticator app. |
| Email magic link (no password) | No: a person | Leave the password blank. The person opens the link on the browser host's session through the live view (paste the link into its address bar), or the site offers a code to type instead. |
| Sign in with Google / Microsoft (SSO) | Usually no | Leave the password blank; a person signs in through the live view and ticks "Stay signed in". Google often refuses automated browsers outright. A bot's fill types only on the tool's own sites, so it never fills the identity provider's page; never list that page as a site (its password opens everything the account reaches). |
| Company SSO (Okta, Entra, Google Workspace SAML) | No: a person | As above; ask whether the vendor also issues an API key, which is usually better. |
| Passkey only | No | A passkey lives on the person's device; the live view shows a browser on another computer, which cannot reach it. Add a password + authenticator app as a second sign-in method if the site allows; else use the vendor's API or MCP server. |
| Hardware security key | No | As passkey. |
| CAPTCHA | No: a person | `needs-you`. A bot never solves one. Repeated captchas mean the bot moves too fast or signs in too often. |
| "Is this you?" / new device approval | No: a person, once per device | `needs-you`; the person approves (on their phone or email). Tick "Remember this device" so it does not come back. |
| Shared login used by people too | Yes, one at a time | One teammate at a time per account; a busy answer names who has it. A bot never signs out or changes the password or 2FA. |

## Choose the route
1. The vendor has an API key or MCP server: use that instead of a website tool (see the vendor's guide).
2. A site with no API: a website tool with password + authenticator 2FA. That is the only setup a bot runs alone.
3. SSO, passkeys, SMS or emailed codes: a website tool with a person in the loop. Say so when you set it up: who signs
   in, how often the site asks (ask the person; note it in `knowledge/company/<vendor>.md`).

## What the Tools Owner does
- Add the site and the account's fields in Tools (a credential administrator adds a Shared account; a person adds
  their Own or a bot's Own). You never see or type a password or a 2FA key.
- When a bot reports "No account", share an account with it or ask a person to add one; when it reports
  `no_password`, a person signs in through the live view.
- Record how each company site signs in (who, which method, how often) in `knowledge/company/<vendor>.md`.
