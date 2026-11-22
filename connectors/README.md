# Connectors

Shared tools that talk to outside services on behalf of every employee. One credential per
service for the whole company, one file that enforces the rules.

**Bots use these tools. Never call the Slack, Gmail or Google Calendar API directly** - not with
curl, not with a vendor SDK, not from an employee's own `software/`. The policy checks, the audit
log, and the retry handling live in the tools (for mail and calendar, on the server); a direct call
skips all three and is a policy violation, not a shortcut.

- `slack.py` - read and post to Slack, and DM the team. It holds no token: it asks Tico
  (`backend/slack_bots.py`), and Tico's Slack gateway does the Slack calls.
- `slack-app-manifest.yaml` - the Slack app definition the owner installs, with exactly the scopes
  the server's Slack gateway uses, Socket Mode on, and the events the gateway
  subscribes to (`app_mention`, `message.im`, `message.channels`, `message.groups`, `link_shared`). The gateway itself is `backend/slack_gateway.py`
  on the API host (`docs/slack-gateway.md`); it is the one thing that holds the token.
- Mail and calendar are not here: they run on the Tico server (`backend/gmail_live.py`, `backend/gcal_live.py`,
  `backend/gmail_watch.py`, shared helpers in `backend/mail_lib/`). Bots and people use them through `tico email` and
  `tico calendar`. The Google key and each person's Connect Google grant stay sealed on the server, so no computer
  holds one. Employee guide and the owner's Google setup: `docs/mail.md`.

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
recipes, gotchas, and the learnings bots add): `tico tool show slack`, `tico tool show mail`,
`tico tool show aside`, or the **Integrations** page. Other outside systems the company uses
get pages in its own config (`integrations/README.md`).

Which channels bots may use is the list under Settings > Tools > Slack (`tico slack channel list`), kept in Tico's database; a bot may post to any of them unless an owner or admin turned posting off for it. A run reads the list from the hub.
What happens to a new message is its inbox's Message automation (`docs/routines.md`). Which employee may post at
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
   `mpim:*` and the Messages tab; the gateway's `app_mentions:read` and `chat:write.customize`;
   `files:read`, which lets bots open screenshots and other files people share; `links:read` and
   `links:write`, which preview the task links people paste),
   paste the current manifest over the old one on **App Manifest**, then reinstall - Slack shows
   a yellow "reinstall your app" banner until you do, and the missing calls fail with
   `missing_scope` until the token is replaced with the new one.
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`). There is no signing secret to store,
   because nothing calls us. For the gateway, also mint an **App-Level Token** with
   `connections:write` under **Basic Information** (starts with `xapp-`); it opens the Socket
   Mode connection.
5. Paste both tokens in the server's **Tools > Slack**, or put them in the secret named by
   `TICO_SLACK_SECRET_ARN` (`docs/slack-gateway.md`, Running it), and start the gateway. Bots need no
   token of their own. (A computer still on v0.6.0-rc.1 runs the old connector, which needs the bot token
   granted as `SLACK_BOT_TOKEN` in **Tools > Credentials**: older computers only.)
6. Never paste the token into an Issue, a commit, a Slack message, or a bot prompt.
7. Invite the bot to every channel the bots read: in each channel a person types `/invite @Tico`.
8. List the channels the bots use: Settings > Tools > Slack (an owner or admin), or
   `tico slack channel add '#name' --reader <bot>`. A channel listed by name gets its id from Slack.
   A bot's `connectors/slack.py doctor` says whether the gateway is connected.

## Using it (bots)

`$HUB` is `~/tico-work/tico`. Every call goes to Tico as your run (`HUB_API_URL`, `HUB_TOKEN`).

    # is Tico's Slack gateway up?
    $HUB/connectors/slack.py doctor

    # yesterday in one channel, with every thread, as markdown for reading
    $HUB/connectors/slack.py history --channel '#release_notes' --since yesterday --threads --format md

    # four channels at once, one section each
    $HUB/connectors/slack.py history --channel '#release_notes,#sales,#support,#engineering' \
        --since yesterday --tz America/Los_Angeles --threads --format md

    # a fixed window, machine-readable
    $HUB/connectors/slack.py history --channel <channel-id> --since 2026-09-01T09:00 --until 2026-09-01T17:00 --format json

    # the Slack channel list
    $HUB/connectors/slack.py channels

    # post (only on a Slack tools entry with no `can` or with `post` in it, to a listed channel with posting on)
    $HUB/connectors/slack.py post --channel '#agents' --text 'Shipped 3 doc updates.'
    printf '%s\n' "$BODY" | $HUB/connectors/slack.py post --channel '#marketing' --text -

Exit codes: `0` ok, `1` failure (Tico or Slack unreachable, the gateway off), `2` Tico refused the
action. Add `--json` (or `--format json`) to get errors as `{"ok": false, "error", "hint"}`.

A refusal is an answer, not an obstacle. If Tico says the channel is not on the list, the
employee lacks `post` access, or the channel is externally shared, do not work around it: put the
draft on the task and, if it really needs to change, ask the owner.

A Slack entry with no `can` reads, posts and DMs; write `can: [read]` to keep an agent read-only. Posting also
needs the channel on the list with posting on.

Every post is a message in the bot's room in Tico, and Tico records what was sent.

## DMs

**What a human can do.** DM **@Tico** in Slack, or `@Tico` in a channel it is in. The gateway routes the message
to the bot it is for, which wakes and answers in the same thread (`docs/slack-gateway.md`).

**What a bot may do.** DM one person on the company's roster who may sign in to Tico and has a Slack account Tico
knows. The DM is the bot's message to that person in Tico, which the gateway copies into their Slack DM unless they
keep bot messages in Tico. It needs the bot's Slack tools entry to allow posting (no `can`, or `post` in it) and is at most 3,800 characters.

```bash
$HUB/connectors/slack.py dm --to cara@acme.example --text 'Pricing doc is live.'
printf '%s\n' "$BODY" | $HUB/connectors/slack.py dm --to ana@acme.example --text -

# open a screenshot someone shared: the `[file F...]` line in history or a routed message names it
$HUB/connectors/slack.py file --id F0123ABCD
```

## Tests

    python3 -m unittest discover -s connectors -v

Covers time parsing, markdown rendering, saving files and each command against Tico's answers, with Tico mocked.
No network, no token needed. The rules themselves are tested on the server (`backend/tests/test_slack_bots.py`).
