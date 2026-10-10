---
service: aside
title: Aside
kind: browser
summary: Signed-in websites through the dedicated bot browser, Aside, with the sites and verbs a bot may use declared in its bot.yaml.
access: "`$HUB_DIR/connectors/browser.py` — nobody drives Chrome, the Orca browser or `aside` directly"
credentials:
  - none in a bot's environment — the owner signs in to each site once, in Aside; sessions and re-logins are Aside's business
  - ASIDE_CLI (optional) — path to the `aside` binary when it is not ~/.local/bin/aside
declared_as: |
  - service: aside
    identity: the team browser (Aside), the owner's sessions
    account: u0                       # optional Aside account
    sites: [app.example.com]               # hosts it may open; a subdomain of a listed host counts
    can: [read]                       # [read] looks; [read, act] clicks, types, submits, runs `task`
writes: allowed
owner: owner
aliases: [browser]
---

## What it is

Aside (aside.com) is a Chromium browser with its own agent, password manager and CLI.
`aside repl` runs Playwright-style JavaScript in the signed-in browser; `aside exec` hands a
job to Aside's agent. Tico's Tool, `connectors/browser.py`, is the only way a bot reaches
either: it checks the bot's `tools:` entry, refuses hosts outside `sites:`, refuses actions
for a read-only bot, and appends every call to `<projects>/runtime/browser-audit.jsonl`. The
skill that teaches a model the REPL is `skills/aside-browser/SKILL.md`; run `aside guide repl`
before writing REPL code. The CLI installs with
`curl -fsSL https://releases.aside.com/install.sh | bash`.

## What data it has

Whatever the signed-in sites show. Typical declarations: the team's own app (read only, for
support-style bots); social sites and news (a listening bot, read); ad dashboards such as
`ads.google.com` (read); a design tool (act). Each is a `sites:` entry in the bot's own
`bot.yaml`.

**The owner's social sessions are the listening bot's alone**. The Tool refuses
any other bot that names a social site (X, Reddit, LinkedIn, Facebook, Instagram, TikTok,
YouTube and the rest of `SOCIAL_HOSTS` in `connectors/browser.py`) in its `sites:` or its code;
ad-account dashboards on those domains are not social reading. The listening bot saves what it reads in Tico (`hub listening save`), the decision model routes
each post to the inboxes that want it (the team's `registry/listening.yaml`), and every other bot works its
inbox (`hub listening item list`, `hub listening item resolve`) or asks the listening bot by task for a lookup.

## How a bot uses it

```bash
$HUB_DIR/connectors/browser.py doctor
$HUB_DIR/connectors/browser.py tabs --as <slug>                                      # what is open on the bot's sites
$HUB_DIR/connectors/browser.py repl --as <slug> "const p = await openTab('https://app.example.com/queue'); ..."
$HUB_DIR/connectors/browser.py repl --as <slug> --file steps.js
$HUB_DIR/connectors/browser.py task --as <slug> "Find the pricing page on app.example.com and ..."   # Aside's own agent; needs act
```

Exit codes: `0` ok, `1` failure (CLI missing, Aside not running, timeout), `2` a Tico policy
refused it. The REPL stops after 120 s; a `task` after 900 s (`ASIDE_TASK_TIMEOUT`).

## Rules

- The bot's `bot.yaml` must declare `service: aside` with `sites:`. Every URL literal in
  the code, and the text of a `task`, must stay on those hosts; anything else is refused before
  the browser is touched.
- `can: [read]` allows REPL reads only. The Tool refuses `.click`, `.dblclick`, `.fill`,
  `.type`, `.press`, `.check`, `.uncheck`, `.selectOption`, `.setInputFiles`, `.dragTo`,
  `.hover`, `.tap`, `.evaluate`, `.evaluateHandle`, `.route`, `.goto`, `page.keyboard`,
  `page.mouse` and the `task` command. `can: [read, act]` allows them.
- Messages to outsiders stay drafts until a person who manages it turns mail sending on in Tico. With sending on, a bot acts
  within the requested work, its Instructions and granted Tools; no per-action approval is required.
  See `policies/approvals.md` and `policies/shared-rules.md`.
- Logins are never a bot's job. The owner signs in once in Aside; a login page, a captcha or a
  challenge means stop and say so.
- Never drive Chrome, the Orca browser or the `aside` CLI directly; the Tool is the only
  audited path.

## Recipes

- Read a page: `openTab(url)` then read titles, text or a snapshot in the REPL; keep the code
  in a `--file` when it is more than a line so the audit shows what ran.
- Watch a dashboard read-only (ads, an email tool, your own app): open the tab, read the numbers, never
  Edit, Save, Apply, Enable, Pause or Create.
- Hand a research job to Aside's agent (act only): `task --as <slug> "..."` with the sites
  named in the prompt; the agent works inside the same host allow-list.

## Gotchas

- A subdomain of a listed host counts; a different host does not, even for a redirect.
- The read-only check is textual: a read-only bot cannot smuggle a click through a helper,
  and `.evaluate` counts as an action.
- A `1` from `doctor` usually means Aside is not running or the CLI is outdated
  (`aside --update`, then `aside guide` again).

## Learnings

What bots and people learn about this Tool is added with `hub tool learn aside "…"` and
shown under this page; a person folds it into the page over time. The page is the rule.
