---
service: website
title: Website tools
kind: browser
summary: Sites the company signs in to (a supplier portal, a bank, an ad dashboard), driven through Tico's own browser with `tico browser` and signed in with `tico login`.
access: "`tico browser <verb> <site>` and `tico login fill|copy|code <site>` — never Chrome, a port or a cookie directly"
credentials:
  - none in a bot's environment — each account's username, password and 2FA key stay in Tools → the website's page; a bot gets them typed for it, never shown
declared_as: |
  # Nothing in bot.yaml. A person adds the site in Tools (Add → Website) and either
  # shares an account with the bot or adds the bot's own account there.
writes: allowed
owner: owner
aliases: [websites, browser]
---

## What it is

A website tool is a site the company signs in to, added in Tools (Add → Website), with its accounts. An account is
**shared** (available to all current and future bots by default, plus people it is shared with) or **own** (one person's or one bot's, used by them alone).
Turn off **All bots** in **Who can use it** to choose individual bots instead. Existing accounts keep their saved access. Tico
picks the account for you: your own on that site, else the shared one shared with you. The browser runs on the
company's **browser host** (one computer an admin picks); without one, on your own computer, unless the site is
**central only** (banks), which runs on the browser host or not at all. Opening chooses the computer and every
later command stays there; if that computer goes away, you are told to open the website again. You drive it a verb at a time with
`tico browser`, and sign in with `tico login`. You never see the password, the 2FA key, the cookies or the browser
itself.

## What data it has

Whatever the account sees on the site. The tool's page in Tools shows its Activity: each open, fill, copy, code,
download, upload and ask, with who, the page and where it ran (own or central). Never a value.

## How a bot uses it

```bash
tico browser open app.example.com                      # the first site's home page, signed in as your account
tico browser open app.example.com https://app.example.com/billing
tico browser read app.example.com                      # the page as text; each control as [e12] button "Sign in",
                                                       # each link with where it goes: [e3] link "2:54 PM" -> https://…
tico browser click app.example.com e12
tico browser type app.example.com e13 "March invoices" --submit
tico browser press app.example.com Escape
tico browser scroll app.example.com --dy 600           # negative scrolls up
tico browser back app.example.com
tico browser screenshot app.example.com                # prints the JPEG's path (./screenshots/…)
tico browser download app.example.com --out reports/   # the newest download; prints its path
tico browser upload app.example.com e20 reports/march.csv   # a file in your checkout, at most 10 MB
tico browser close app.example.com                     # let go of the account; the sign-in stays
tico login fill app.example.com username --ref e13
tico login fill app.example.com password --ref e14
tico login fill app.example.com code --ref e15         # the current 2FA code, when the account has a 2FA key
tico login copy app.example.com password               # onto your own computer's clipboard for 30 s
tico login code app.example.com                        # prints the code and the seconds it has left
tico browser needs-you app.example.com "Type the code texted to the office phone"
```

`<site>` is the site (`app.example.com`), a page URL on it, or the tool's name or id. Each command waits up to 40 s
and prints one thing: the page (`<title> — <url>`), the page text, a file path, or a refusal as one sentence with what
to do next. `--json` prints the answer as JSON. Exit 0 is done, 2 refused, 1 when the browser did not answer in time.

## Rules

- Sign in only with `tico login fill`. Never type a password or a 2FA code with `tico browser type`, never ask anyone
  for one in a message or task, and never read the clipboard after `tico login copy`.
- `tico login fill` types only on the tool's own sites over https, and a password only into a password box. Anything
  else is refused and the value is dropped.
- Never solve a captcha, a code sent by text or email, or a new-device check yourself. Run
  `tico browser needs-you <site> "<what to do>"`, end the turn, and read the page again when their Done wakes you.
- One teammate at a time per account. Your commands hold it for 2 minutes after the last one; `tico browser close`
  when you are done. A busy answer names who has it: try again in a minute. While a person has taken control, wait
  until they give it back.
- Stay signed in. Never sign out, change the password, turn 2FA off or change the account's security settings.
- Go at a person's pace: read before you act, one action at a time, no rapid loops, nothing beyond the task's work.
- Act within the requested work, your Instructions and `policies/approvals.md`; the site is not a way around them.
- A central-only site refuses `tico login copy` and `tico login code`; use `tico login fill`.

## Recipes

- Sign in:
  1. `tico browser open <site>`, then `tico browser read <site>`. Already signed in? You are done.
  2. `tico login fill <site> username --ref <box>`, `tico login fill <site> password --ref <box>`, then click the
     sign-in button. A refusal `no_password` (or `no_username`) means a person signs in to this account by hand
     (Google or SSO sign-in, an emailed code, a passkey): run `tico browser needs-you <site> "Sign in to <site>"`,
     end the turn, and read again when their Done wakes you.
  3. Read again. A 2FA box: `tico login fill <site> code --ref <box>`, then `tico browser press <site> Enter`.
  4. A texted or emailed code, a captcha or "is this you?": `tico browser needs-you`, then end the turn.
  5. Read again to check you are in.
- A sign-in on two pages (username, then Next, then password): fill the username, click Next, read again (the refs
  change), fill the password.
- Get a report: open its page, read, click the download link, then `tico browser download <site> --out reports/`.
- A page the text does not explain (a chart, a layout): `tico browser screenshot <site>` and look at the file.

## Gotchas

- Refs last until the next read or page change. "That element is gone" means read the page again.
- A subdomain of a listed site counts; another host does not, even after a redirect.
- `tico login fill` without `--ref` types into the focused box: click it first.
- A password box reads with no value, even after a fill.
- "No account" means nobody shared one with you: ask a person on your task to share an account or add yours.
- An account may have no password (or no username): a person signs in to it by hand. Fill and copy of it are refused
  with `no_password`; never ask for a password, use `tico browser needs-you` so they sign in through the live view.
- The browser host offline means wait, or ask a person to start it. On your own computer the browser needs Google
  Chrome (or Chromium on Linux) installed.
- Screenshots and downloads are saved on the computer you run on; a download never overwrites a file of the same name
  (it gets `-1`, `-2`).

## Learnings

What bots and people learn about this Tool is added with `tico tool learn website "…"` and shown under this page; a
person folds it into the page over time. The page is the rule.
