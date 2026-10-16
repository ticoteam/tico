---
service: slack
title: Slack
kind: api
summary: Read channel history and the bot's DMs, post to registered internal channels through the one shared connector; a DM to Tico or an @Tico wakes the employee it is for.
access: "`$HUB_DIR/connectors/slack.py` — never the Slack API, an SDK or curl"
credentials:
  - SLACK_BOT_TOKEN — the Tico Slack app's Bot User OAuth Token, stored in Tools > Credentials and granted to this bot; only granted runs receive it
declared_as: |
  - service: slack
    identity: the company workspace
    can: [read]                     # add `post` to write to channels and DM people
    channels: [marketing]           # optional read allow-list; omit only for broad roles
    dms: false                      # optional: refuse the shared DM inbox
    env: SLACK_BOT_TOKEN
writes: allowed
owner: owner
---

## What it is

The company's Slack workspace, reached through one app ("Tico") and one connector,
`connectors/slack.py`. The policy checks, the audit log and the retries live in the connector;
a direct API call skips all three and is a policy violation. Install and scopes:
[connectors/README.md](../connectors/README.md).

## What data it has

The channels the app has been invited to, listed under Tools > Slack channels
(`hub slack channel list`): for example a
marketing work log, sales, product release notes, support operations, a mention feed, an agents
channel, and the engineering alert channels an engineering bot watches (cloud, CI, error
tracking, deploys). DMs and
group DMs people send the bot. A DM to Tico, or an `@Tico` in a channel it is in, wakes the
employee the decision model routes it to and the reply comes back in that thread under the employee's name
([Tico in Slack: the gateway](../docs/slack-gateway.md)). A plain channel message wakes nobody
at once: it is stored, and the channel's `readers` in the registry get what they have not seen,
grouped by thread, on the gateway's hourly pass, the way an employee subscribed to the channel
catches up on it. A bot that is not a reader still reads a channel with `history` during a turn.

## How a bot uses it

```bash
$HUB_DIR/connectors/slack.py doctor                                   # token, bot user, channels it is in
$HUB_DIR/connectors/slack.py channels                                 # what the bot can see
$HUB_DIR/connectors/slack.py history --as <slug> --channel '#release_notes' --since yesterday --threads --format md
$HUB_DIR/connectors/slack.py history --as <slug> --channel '#marketing,#sales' --since 7d --tz America/Los_Angeles --format json
$HUB_DIR/connectors/slack.py post --as <slug> --channel '#marketing' --text 'Shipped 3 doc updates.'
printf '%s\n' "$BODY" | $HUB_DIR/connectors/slack.py post --as <slug> --channel '#agents' --text -
$HUB_DIR/connectors/slack.py post --as <slug> --channel '#agents' --text 'Bot health' --blocks health.json   # Block Kit; prints the ts
$HUB_DIR/connectors/slack.py update --as <slug> --channel '#agents' --ts 1756742400.000100 --text 'Bot health' --blocks health.json
$HUB_DIR/connectors/slack.py upload --as <slug> --file chart.png                # private to the app; prints F... for a slack_file image block
$HUB_DIR/connectors/slack.py upload --as <slug> --channel '#agents' --file chart.png --comment 'Waiting tasks by person'
$HUB_DIR/connectors/slack.py inbox --since 24h --format md --mark    # DMs people sent the bot; --mark advances the watermark
$HUB_DIR/connectors/slack.py dm --as <slug> --to person@example.com --text 'Pricing doc is live.'
$HUB_DIR/connectors/slack.py reply --as <slug> --to person@example.com --thread 1756742400.000100 --text 'On it.'
$HUB_DIR/connectors/slack.py file --as <slug> --id F0123ABCD   # a shared screenshot or log, from its [file F...] line
```

Inside a hosted turn `HUB_BOT` stands in for `--as`. Exit codes: `0` ok, `1` failure
(token, network, Slack), `2` a hub policy refused it. `--json` gives errors as
`{"ok": false, "error", "hint"}`.

## Rules

- Reading needs `service: slack` with `read` in `tools:`; a `channels:` list on that entry
  limits `history` to those channels (plus any channel whose list entry names the bot as a reader) before any call
  reaches Slack; `dms: false` refuses `inbox`.
- A file someone shared shows as a `[file F...] name` line in `history`, `inbox` and a message routed from
  Slack. `file --id` downloads it under the same read rules: a channel the bot may read, or a DM unless
  `dms: false`. It needs the app's `files:read` scope; without it Slack sends its sign-in page, and `file` says
  so instead of saving it. By default the file goes to a private folder of the bot's own user under the temp
  folder, never into the repository, and a long or odd name is shortened and cleaned.
- Posting needs `post` in `can`, and the channel must be on the Slack channel list
  (Tools > Slack channels) with posting on, as it is unless an owner or admin turned it off. Externally shared (Slack Connect) channels are refused: posting there is an
  outbound send. Text over 4,000 characters is refused.
- `--blocks` takes a Block Kit array (a file, or `-` for stdin) of at most 50 blocks; `--text` stays required as the
  notification text. Blocks may not carry `image_url`, because Slack would fetch that address and any data in it
  leaves the workspace: upload the image and use an image block with `"slack_file": {"id": "F..."}`.
- `update` edits a message only when this computer's audit log shows the same bot posted it in that channel (every bot
  shares the app's token, and Slack alone would let one edit another's posts). A dashboard is one `post` whose ts the
  bot keeps, then an `update` each run. Without `--blocks` the message goes back to plain text.
- `upload` takes a png, jpg or gif up to 10 MB and needs the app's `files:write` scope. With `--channel` the channel
  rules above apply; without it the image is shared nowhere until an image block shows it.
- A DM goes only to a person in `registry/hub-access.yaml`; bots and deactivated accounts are
  refused. Several recipients make one group DM.
- Internal posts and DMs are not outbound sends, so `outbound_send` does not gate them; the
  read-only period still applies to anything public.
- Every accepted post and DM is appended to `<projects>/runtime/slack-audit.jsonl`.
- Nothing from a mailbox goes into Slack. A refusal is an answer: put the draft on the task and,
  if the channel or verb really should change, file a task for `the owner`.
- A reply the gateway posts for you in a Slack thread is a solicited reply, shown under your
  name with `(sent from <you>)`. It needs no `post: true` and grants none: posting anything else
  there still goes through `post` and the registry.

## Recipes

- Yesterday in four channels, one section each, ready to read:
  `history --channel '#release-notes,#sales,#support,#engineering' --since yesterday --threads --format md`
- A fixed window, machine-readable: `history --channel <channel-id> --since 2026-09-01T09:00 --until 2026-09-01T17:00 --format json`
- An engineering bot's daily watch reads the alert channels with `--since 24h`; each channel's
  purpose is in the registry file.
- Use a channel id (`C...`) when a name is ambiguous; a channel that does not exist is refused,
  so check the registry file for the real name.

## Gotchas

- `search.messages` is rejected for this token type; read with `history` per channel instead.
- Private channels need a human to `/invite @Tico`; public ones the bot joins itself
  (`join --channel <id>`).
- `inbox` without `--mark` returns the same DMs next time; `--since` only floors a conversation
  with no watermark yet.
- The user directory is cached for a day in `runtime/slack-users.json`; a new hire may not
  resolve until it refreshes.
- A message that reached you from Slack carries `refs.slack` (channel, permalink, the last
  twelve exchanges) and `refs.routing` (why the decision model chose you). Answer in the conversation; the
  gateway posts it to that thread for you, under your name. Do not post to Slack yourself for
  that conversation: no `post`, no `dm`, no `reply`.

## Learnings

What bots and people learn about this integration is added with `hub tool learn slack "…"` and
shown under this page; a person folds it into the page over time. The page is the rule.
