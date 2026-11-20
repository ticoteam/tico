---
service: slack
title: Slack
kind: api
summary: Read channel history, post to listed internal channels and DM people through Tico's Slack gateway; a DM to Tico or an @Tico wakes the employee it is for.
access: "`$HUB_DIR/connectors/slack.py`, which asks Tico — never the Slack API, an SDK or curl"
credentials:
  - none in a bot's environment — Tico's Slack gateway holds the company's token (Tools > Slack)
declared_as: |
  - service: slack
    identity: the company workspace
    can: [read]                     # add `post` to write to channels and DM people
    channels: [marketing]           # optional: read these too, on top of the channels that list this bot as a reader
    dms: false                      # optional: no files from DMs
writes: allowed
owner: owner
---

## What it is

The company's Slack workspace, reached through one app ("Tico"). Tico's Slack gateway holds the app's one token;
a bot holds none. `connectors/slack.py` asks Tico (`/api/v2/slack/...`), which checks every rule and has the gateway
read or post. Slack for bots needs the gateway running: with it off, every command says what to start. Install and
scopes: [connectors/README.md](../connectors/README.md).

## What data it has

The channels the app has been invited to, listed under Tools > Slack channels
(`tico slack channel list`): for example a
marketing work log, sales, product release notes, support operations, a mention feed, an agents
channel, and the engineering alert channels an engineering bot watches (cloud, CI, error
tracking, deploys). DMs and
group DMs people send the bot. A DM to Tico, or an `@Tico` in a channel it is in, wakes the
employee the decision model routes it to and the reply comes back in that thread under the employee's name
([Tico in Slack: the gateway](../docs/slack-gateway.md)). A plain channel message wakes nobody
at once: it is stored, and in a channel bots read (Settings > Tools > Slack) it goes into the channel's
read-only thread, which each reader follows and catches up on about once an hour, the way an employee
subscribed to the channel does. A bot whose `channels:` list names a channel reads it with `history` during a turn.
A channel can also be linked to a Tico thread, both ways ([Slack](../docs/slack.md#link-a-channel-to-a-thread)).

## How a bot uses it

```bash
$HUB_DIR/connectors/slack.py doctor                                   # is Tico's Slack gateway up
$HUB_DIR/connectors/slack.py channels                                 # the Slack channel list
$HUB_DIR/connectors/slack.py history --channel '#release_notes' --since yesterday --threads --format md
$HUB_DIR/connectors/slack.py history --channel '#marketing,#sales' --since 7d --tz America/Los_Angeles --format json
$HUB_DIR/connectors/slack.py post --channel '#marketing' --text 'Shipped 3 doc updates.'
printf '%s\n' "$BODY" | $HUB_DIR/connectors/slack.py post --channel '#agents' --text -
$HUB_DIR/connectors/slack.py post --channel '#marketing' --thread 1756742400.000100 --text 'On it.'
$HUB_DIR/connectors/slack.py dm --to person@example.com --text 'Pricing doc is live.'
$HUB_DIR/connectors/slack.py file --id F0123ABCD   # a shared screenshot or log, from its [file F...] line
```

The bot is the one the run's `HUB_TOKEN` belongs to; `--as` is accepted and ignored. Exit codes: `0` ok, `1` failure
(Tico or Slack unreachable, the gateway off), `2` Tico refused it. `--json` gives errors as
`{"ok": false, "error", "hint"}`.

## Rules

Tico checks each of these, not the connector:

- Reading needs `service: slack` with `read` in `tools:`. `history` reads the channels on that entry's `channels:`
  list and any channel whose list entry names the bot as a reader. Screening runs on what comes back: a warning
  line heads it, and a likely prompt injection is held.
- A file someone shared shows as a `[file F...] name` line in `history` and a message routed from Slack.
  `file --id` downloads it under the same read rules: a channel the bot may read, or a DM unless `dms: false`. It
  needs the app's `files:read` scope; without it Slack sends its sign-in page, and `file` says so. By default the
  file goes to a private folder of the bot's own user under the temp folder, never into the repository.
- Posting needs `post` in `can`, a bot past onboarding, and the channel on the Slack channel list
  (Settings > Tools > Slack) with posting on, as it is unless an owner or admin turned it off. Slack Connect
  channels are refused: posting there is an outbound send. Text over 3,800 characters is refused (what Slack gets whole, with the bot's footer). The org's rules
  check the text; a held post is sent only when a person chooses Send anyway.
- A post shows in the bot's room in Tico too, and in Slack with `(sent from <bot>)`.
- A DM goes to one person on the company's roster who may sign in to Tico and has a Slack account Tico knows. It
  is the bot's message to them in Tico, which the gateway copies into their Slack DM, unless they keep bot messages
  in Tico; the same reach rules as any bot message apply.
- DMs people send the bot reach it as messages through the gateway; there is no inbox to poll.
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
  note is in Settings > Tools > Slack.
- Use a channel id (`C...`) when a name is ambiguous; a channel Slack does not know is refused,
  so check Settings > Tools > Slack for the real name.

## Gotchas

- `search.messages` is rejected for this token type; read with `history` per channel instead.
- A channel needs a person to `/invite @Tico` before the gateway can read or post there.
- `post` and `dm` wait up to 20 seconds for Slack to have the message and print its link; after that the message is
  still queued, and the bot's room shows when it is sent.
- A message that reached you from Slack carries `refs.slack` (channel, permalink, the last
  twelve exchanges) and `refs.routing` (why the decision model chose you). Answer in the conversation; the
  gateway posts it to that thread for you, under your name. Do not post to Slack yourself for
  that conversation: no `post`, no `dm`.

## Learnings

What bots and people learn about this integration is added with `tico tool learn slack "…"` and
shown under this page; a person folds it into the page over time. The page is the rule.
