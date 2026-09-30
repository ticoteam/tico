# Connectors

Shared tools that talk to outside services on behalf of every employee. One credential per
service for the whole company, one file that enforces the rules.

**Bots use these tools. Never call the Slack, Gmail or Google Calendar API directly** - not with
curl, not with a vendor SDK, not from an employee's own `software/`. The policy checks, the audit
log, and the retry handling live in here; a direct call skips all three and is a policy violation,
not a shortcut.

- `slack.py` - read and post to Slack, and DM the team. Token: `SLACK_BOT_TOKEN` from
  `secrets/_shared.env`.
- `slack-app-manifest.yaml` - the Slack app definition the owner installs, with exactly the scopes
  `slack.py` and the server's Slack gateway use, Socket Mode on, and the events the gateway
  subscribes to (`app_mention`, `message.im`, `message.channels`, `message.groups`). The gateway itself is `backend/slack_gateway.py`
  on the API host (`docs/slack-gateway.md`); it is the one other thing that holds the token.
- `slack-bot-app-manifest.yaml` - one bot's own Slack app: people @mention or DM the bot itself
  and it answers as itself. Its tokens are vault credentials the gateway reads by bot variable
  name; the file lists the steps (`docs/slack-gateway.md`, "A bot's own Slack app").
- `mail/` - read and file email as ana@acme.example or legal@acme.example, and the deterministic inbox
  rules. Run it as `scripts/mail.sh`. One Google service account with domain-wide delegation,
  key at `secrets/google-sa.json`, never in a bot's environment. Employee guide: `docs/mail.md`;
  developer notes and the owner's Google setup: `connectors/mail/README.md`.

- `browser.py` - the company browser, which is Aside (aside.com): a Chromium browser with its
  own agent and password manager, signed in by the owner once per site. `repl --as <slug> "<js>"`
  runs Playwright-style JavaScript in the signed-in browser (the skill is
  `skills/aside-browser/SKILL.md`, then `aside guide repl`); `task --as <slug> "..."` hands a job
  to Aside's agent; `tabs` lists what is open on the bot's sites. A bot declares
  `service: aside` with `sites:` and `can: [read]` or `[read, act]`; hosts outside the list are
  refused, and a read-only bot may not click, type, or submit. Every call is audited in
  `runtime/browser-audit.jsonl`. Nobody drives Chrome, the Orca browser, or `aside` directly.
  The CLI installs with `curl -fsSL https://releases.aside.com/install.sh | bash`.

Each connector has a page in `integrations/` (what it is, how a bot uses it, the rules,
recipes, gotchas, and the learnings bots add): `hub tool show slack`, `hub tool show mail`,
`hub tool show aside`, or the **Integrations** page. Other outside systems the company uses
get pages in its own config (`integrations/README.md`).

Which channels exist is `registry/slack-channels.yaml`; a bot may post to any of them unless the entry says `post: false`.
What the mail rules do to new messages is `registry/mail-rules.yaml`. Which employee may post at
all, and which mailbox it may act as, is the `access:` block in its own `employee.yaml`
(`policies/access.md`). All of it is checked in code on every call.

## Installing the Slack app (the owner, once)

1. Go to <https://api.slack.com/apps>, click **Create New App**, choose **From an app manifest**,
   and pick your company's workspace.
2. Paste the contents of `connectors/slack-app-manifest.yaml`, review the scope list, and create
   the app. Both the app and bot display name are **Tico**.
   For an existing app, apply the updated manifest to that app to change its display names.
3. On the app's **Install App** page, click **Install to Workspace** and approve the bot scopes
   listed in the manifest. The manifest already carries the event subscriptions and Socket Mode;
   there are no slash commands and nothing to configure by hand.
   **If the app was created before a scope existed** (the DM scopes `users:read.email`, `im:*`,
   `mpim:*` and the Messages tab; the gateway's `app_mentions:read` and `chat:write.customize`),
   paste the current manifest over the old one on **App Manifest**, then reinstall - Slack shows
   a yellow "reinstall your app" banner until you do, and the missing calls fail with
   `missing_scope` until the token is replaced with the new one.
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`). There is no signing secret to store,
   because nothing calls us. For the gateway, also mint an **App-Level Token** with
   `connections:write` under **Basic Information** (starts with `xapp-`); it opens the Socket
   Mode connection.
5. Put the bot token in the shared env file, which every run inherits, and both tokens in the
   server's `tico/slack` secret (`docs/slack-gateway.md`, Running it):

       echo 'SLACK_BOT_TOKEN=xoxb-...' >> ~/tico-work/secrets/_shared.env

6. Lock the file down: `chmod 600 ~/tico-work/secrets/_shared.env`. Never paste the token into
   an Issue, a commit, a Slack message, or a bot prompt.
7. Invite the bot to every channel the bots read. Public channels can be joined by the bot itself
   (`connectors/slack.py join --channel <id>`); **private channels must be invited by a human**:
   in each channel type `/invite @Tico`. Today that is `#release_notes`, `#sales`,
   `#support`, `#engineering`, `#marketing`, `#marketing-alerts`, and `#agents`.
8. Verify:

       SLACK_BOT_TOKEN=xoxb-... connectors/slack.py doctor

   It prints the workspace, the bot user, and every channel the bot is a member of, and exits 1 if
   anything is wrong. Then fill any blank `id:` in `registry/slack-channels.yaml` from
   `connectors/slack.py channels`.

## Using it (bots)

`$HUB` is `~/tico-work/tico`. The token is already in your environment; never print it.

    # is Slack working, and where am I?
    $HUB/connectors/slack.py doctor

    # yesterday in one channel, with every thread, as markdown for reading
    $HUB/connectors/slack.py history --channel '#release_notes' --since yesterday --threads --format md

    # four channels at once, one section each
    $HUB/connectors/slack.py history --channel '#release_notes,#sales,#support,#engineering' \
        --since yesterday --tz America/Los_Angeles --threads --format md

    # a fixed window, machine-readable
    $HUB/connectors/slack.py history --channel <channel-id> --since 2026-09-01T09:00 --until 2026-09-01T17:00 --format json

    # list what the bot can see
    $HUB/connectors/slack.py channels

    # post (only if your employee.yaml allows it and the channel does not say post: false)
    $HUB/connectors/slack.py post --as doc-updater --channel '#agents' --text 'Shipped 3 doc updates.'
    printf '%s\n' "$BODY" | $HUB/connectors/slack.py post --as mention-desk --channel '#marketing' --text -

Exit codes: `0` ok, `1` failure (bad token, network, Slack error), `2` a hub policy refused the
action. Add `--json` (or `--format json`) to get errors as `{"ok": false, "error", "hint"}`.

A refusal is an answer, not an obstacle. If the tool says the channel is not in the registry, the
employee lacks `post` access, or the channel is externally shared, do not work around it: put the
draft on the Issue and, if it really needs to change, open an Issue with `owner:ana` and
`type:decision`.

Every accepted post is appended to `~/tico-work/runtime/slack-audit.jsonl` with the timestamp,
the employee slug, the channel, and the first 200 characters.

## DMs and the bot's inbox

Slack DMs are the one place a human and a bot talk directly.

**What a human can do.** DM **@Tico** in Slack like any colleague, or add it to a group DM.
Nothing in Slack wakes a bot: there are no event subscriptions, and Slack never calls us. The
message sits there until the next scheduled or manual run of whichever employee polls `inbox`
reads it - the dispatcher's run, or a bot's own run. If it is urgent, say it in an Issue.

**What a bot may do.** DM the humans in `registry/hub-access.yaml` (`owner` plus `allowed`) and
nobody else. Every recipient is resolved to a real Slack account and checked against that list
before a single message is sent; anyone else is refused with exit `2`. This is not an outbound
send - it is one internal Slack message to a colleague (`policies/shared-rules.md`) - but the
company list is the whole boundary, so do not try to route around it. Adding someone to
`hub-access.yaml` is the owner's call: open an Issue with `owner:ana` and `type:decision`.

The gates `dm` runs, all of them in code and all before any network call reaches Slack:

1. the employee's `employee.yaml` declares `access:` service `slack` with `post` in `can`
   (the same verb that gates channel posts);
2. every `--to` resolves to a Slack user whose email is in `registry/hub-access.yaml`;
3. bots and deactivated accounts are refused (bots talk to each other through Issues);
4. the message is 4000 characters or fewer.

```bash
# DM one person - a work email is exact, and the only form that cannot pick the wrong human
$HUB/connectors/slack.py dm --as doc-updater --to cara@acme.example --text 'Pricing doc is live.'

# @handle and U-id work too, resolved through the cached directory
$HUB/connectors/slack.py dm --as doc-updater --to '@cara' --text 'Pricing doc is live.'

# several people = one group DM (mpim), not several DMs
$HUB/connectors/slack.py dm --as doc-updater --to cara@acme.example,ana@acme.example --text 'Standup?'

# long body from stdin
printf '%s\n' "$BODY" | $HUB/connectors/slack.py dm --as doc-updater --to ana@acme.example --text -

# read what people sent the bot, then mark it seen so the next run only gets newer messages
$HUB/connectors/slack.py inbox --since 24h --format md --mark

# answer in the thread of the message you just read (`reply` is an alias for `dm`)
$HUB/connectors/slack.py reply --as doc-updater --to cara@acme.example \
    --thread 1756742400.000100 --text 'On it - Issue #214.'
```

`inbox` lists every DM and group DM the bot is in, one markdown section per person, oldest first,
with each message's `ts` and a ready-made `reply` line. The bot's own messages, Slackbot, and
other apps are skipped. `--mark` advances the per-conversation watermark in
`~/tico-work/runtime/slack-inbox.json`; without it the same messages come back next time.
`--since` (default `24h`) is only the floor for a conversation with no watermark yet, so a first
run does not drag in a year of history. `--format json` gives the same thing structured.

Accepted DMs land in the same audit log with `"kind": "dm"` and the recipients' emails.
`connectors/slack.py doctor` reports how many DM conversations the bot has and says plainly when
the DM scopes are missing (the app needs reinstalling - see the install steps above).

## Tests

    python3 -m unittest discover -s connectors -v

Covers the pure parts - time parsing, markdown rendering, the policy checks (posting, DMs, and
the `access:` allow-lists), the inbox watermark and the audit lines - with the HTTP layer mocked.
No network, no token needed.
