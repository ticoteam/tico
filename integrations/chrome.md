---
service: chrome
title: Chrome
kind: browser
summary: A real Chrome of the bot's own on its Linux computer, driven with the chrome-devtools tools or Playwright, with Tico typing granted logins and 2FA codes into the page.
access: "the `chrome-devtools` MCP tools, or Playwright `connect_over_cdp(os.environ['TICO_BROWSER_URL'])`; logins through `hub_browser_fill` / `hub browser fill`"
credentials:
  - none in a bot's environment — website logins live in Tools → Credentials (kind login) and are granted to the bot; the runner types them into the page and never hands the value back
  - TICO_BROWSER_URL — set by the runner for each run; a loopback address with a secret for that run, dead once the run ends
  - TICO_BROWSER_LANG (default en-US), TICO_BROWSER_TZ, TICO_BROWSER_PROXY — optional, on the runner
declared_as: |
  - service: chrome
    identity: the bot's own Chrome on its computer
writes: allowed
owner: owner
aliases: [google-chrome, chromium]
---

## What it is

Each bot that declares `service: chrome` gets one Google Chrome of its own on its Linux computer,
headful on the computer's display, with a profile that survives runner restarts and updates, so sites
stay signed in. All of that bot's runs share it; it keeps running between runs and restarts if it crashes.
The runner installs Chrome and `chrome-devtools-mcp` into runner-home the first time a bot needs them
(about 450 MB, checked against a pinned checksum). Aside stays the browser on Macs; on a Mac this Tool
reports itself unsupported in health.

## What data it has

Whatever the sites the bot signs in to show, plus the Chrome profile in the bot's folder under
`browsers/<bot>` on the computer. Downloads land in that profile's `Downloads`.

## How a bot uses it

- Browse with the `chrome-devtools` MCP tools (navigate, click, fill a form field, read the page,
  screenshots), which every Claude Code, Codex and MuseCode run of a chrome bot has.
- Or script it: `playwright.chromium.connect_over_cdp(os.environ["TICO_BROWSER_URL"])` and use the
  existing context (`browser.contexts[0]`); never launch a browser of your own.
- Sign in: click the username field, then `hub_browser_fill(credential="<login name>", field="username")`;
  the same for `password` and, at the 2FA prompt, `totp`. Pass `selector` to focus a field first.
  Submit the form yourself.
- `hub_browser_status()` / `hub browser status`: running, version, display, profile, downloads, and a
  `problem` when Chrome cannot run.

```bash
hub browser fill Acme username --selector '#email'
hub browser fill Acme password --selector '#password'
hub browser fill Acme totp
hub browser status
```

## Rules

- A fill answers only `{"ok": true}` or `{"ok": false, "error": ...}`; values never come back to the bot.
  It refuses a login not granted to the bot (`not_granted`), a page whose top-level host is not one of the
  login's sites (`site_mismatch`, a subdomain of a listed site counts), a field inside a frame from
  another host, and a run that has ended (`no_run`).
- No login yet: ask the owner with `hub_credential_request` kind `login` and the site. A captcha or a
  check only a person can pass: `hub_browser_needs_you(host, reason)`; the person takes over from the
  computer's screen and presses Done.
- Messages to outsiders follow the same sending rules as mail (`policies/approvals.md`).
- `TICO_BROWSER_URL` is this run's own; never write it to a file, a message or a log.

## Recipes

- Sign up for a service: fill the form with the chrome-devtools tools, wait for the emailed code
  with `hub inbound wait` (Tico Inbox), then ask the owner to save the login through the credential card.
- Check a dashboard: navigate, read the numbers with a snapshot, leave without saving anything.

## Gotchas

- `no_field`: nothing editable is focused. Click the field or pass `selector`.
- `browser_unavailable` or a `problem` in status: Chrome is still installing (the first run on a
  computer waits for the download) or could not start; the problem says which.
- One Chrome per bot: two runs that drive the same tab step on each other. Open a tab per run.

## Learnings

What bots and people learn about this Tool is added with `hub tool learn chrome "…"` and
shown under this page; a person folds it into the page over time. The page is the rule.
