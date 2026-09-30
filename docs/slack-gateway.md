# Tico in Slack: the gateway

A DM to Tico, or an `@Tico` in a channel Tico has been invited to, wakes the bot the
message is for. The reply comes back in the same thread or DM under that bot's name, with
`(sent from <bot>)` as its last line. Everything else said in a channel Tico is in is
stored, and the channel's readers get what they have not seen within the hour, the way a
bot subscribed to the channel catches up on it. One Slack app, one process on the server,
and the decision model deciding who a DM or a mention is for. The code is
`backend/slack_gateway.py`.

## What wakes a bot

| In Slack | What happens |
|---|---|
| A DM to Tico | Routed; the reply lands at the bottom of the DM, or in the reply thread the human wrote in. A DM is one Tico conversation per bot for its life, however the human types: a follow-up at the bottom of the DM continues it. |
| `@Tico` in a channel Tico is in | Routed; the reply goes under the message. Mentioning Tico again in that thread continues the same conversation. |
| A reply in a thread a bot already talks in, no mention | Routed at once, like a mention: the decision model sees the thread's routing and the reply usually continues it. |
| Any other message in a channel Tico is in, bots' posts included | Stored (Channels, below). The channel's readers get it on the hourly pass; nobody is woken now. A bot still reads a channel through `connectors/slack.py history` during its own run. |
| An edit or a deletion in a channel | Applied to the stored message; a reader that had already seen it is told. |
| A join, a reaction, a file with no text | Ignored. |
| A Slack Connect or externally shared channel | Refused; nothing is written. |
| A guest, a deactivated account, a sender whose verified email is not on `registry/people.yaml` or not admitted by `registry/hub-access.yaml` | Refused silently: no reply, the reason on the event. |

A DM is a front door to whichever bot the message is for, never a human's private Tico
room (`docs/conversation.md`); those rooms are untouched. The human's profile in Tico
(`#/person/<id>/slack`) shows those DMs, including their replies. A bot that messages a
human in Tico (Legal saying something to Thomaz) is posted through the same Tico Slack
app into their DM, so it shows up there too.

## How a message is routed

Every accepted message is one decision, through the same primitive every bot has
(`clients/judge.py`, `skills/decisions/SKILL.md`); the three fixed questions are the shared set
`questions/slack-route.json` and the call is labelled `slack-route@1`. The state the decision model reads:

- the message, the sender (roster human, group, `primary_for`) and the channel or DM with its
  purpose from `registry/slack-channels.yaml`;
- the last twelve exchanges in this Slack thread, each with the bot it went to;
- the thread's previous routing: bots, confidences, reason, and whether each bot is still waiting
  on a reply (the last line in its conversation is the bot's own `ask`, or ends with a question;
  once the human has answered, it is not waiting);
- the active roster: slug, display name, group, description, `reports_to`. Paused, planned and
  quarantined bots are not offered.

The questions, asked in as many calls as needed (a call holds at most 40, so 56 active bots make two;
the answers are merged by question id): one `noul` "should this go to <bot>?" per active bot, plus
"is this a reply to the bot that last asked here?", "does it ask for anything at all?" and "does
it name a bot explicitly?". Only the per-bot scores and "asks for anything" decide the
recipients; "reply to the bot that last asked" and "names a bot" are stored for tuning and
audit (the plan, section 4) and never override a score, so continuity in a thread comes from
The decision model reading the previous routing and the open ask in its state, not from a rule.

| the decision model says | The gateway does |
|---|---|
| asks-for-anything below `TICO_SLACK_ASK_THRESHOLD` (0.5) | records the message on the thread's conversations, wakes nobody |
| one or more bots at or above `TICO_SLACK_ROUTE_THRESHOLD` (0.6) | routes to each, best `TICO_SLACK_MAX_RECIPIENTS` (3) by confidence, ties by slug |
| no bot at threshold | routes to the assistant (`TICO_ASSISTANT_BOT`) with the top three candidates on the message, so it can ask the one clarifying question the write layer allows. A team that chose no assistant (it is optional) gets BotOps instead; with neither active, the message is recorded and nobody is woken |
| a chosen bot the write layer refuses (paused or quarantined since the roster was read) | drops it with the reason; the assistant (or BotOps) if that empties the set |

Without a decisions key every message goes to the assistant (or BotOps) and the message says so
(`routed_by: default`). When the decision model is unreachable, the message stays pending and retries after a minute, for up to
ten minutes. A refused call, or one still failing after that, ends the message as `failed` with the
reason on it, and Tico tells the human in the thread; nothing retries a failed message.

Every score and answer is stored: on the event (`slack_events.routing_json`) and on the message
(`refs.routing`), where the conversation view shows it as a chip (`routed by decisions to Legal 92%`).
A misroute is fixed by a better description in the bot's registry entry, not by a rule.

Stored answer for one routed message, as the message's `refs.routing` carries it:

```json
{"routed_by": "judge", "model": "judge-latest",
 "recipients": [{"bot": "legal", "confidence": 0.92}],
 "candidates": [{"bot": "legal", "confidence": 0.92}, {"bot": "coo", "confidence": 0.3}, {"bot": "cmo", "confidence": 0.0}],
 "scores": {"cmo": 0.0, "content-social": 0.0, "coo": 0.3, "cto": 0.0, "legal": 0.92, "seo": 0.0},
 "asks": 0.9, "reply_to_last": 0.1, "names_bot": 0.1,
 "thresholds": {"route": 0.6, "ask": 0.5, "max": 3},
 "fallback": false, "dropped": [], "reason": "",
 "delivered": [{"bot": "legal", "conversation_id": "…", "message_id": "…", "opened": true}]}
```

## Identity and what the bot sees

The Tico message is written through `hubdb.say` as the verified human (`human:<id>`), never as
Tico, into a `direct` conversation between that human and the bot, mapped to the Slack thread
in `slack_threads`. The guardrails in `docs/how-it-works.md` apply unchanged: an active bot only,
the cap on bot-to-bot traffic, the unsolicited cap, the lint, the escape rule (a message that
names a `secrets/` path is refused and recorded as such).

A follow-up in a thread is linked to the bot's last line (`in_reply_to`); when that line is an
unanswered `ask`, the follow-up is written as its `answer`, which is what `hub question ask --wait`
polls for, so the ask closes.

The bot's run gets the text, and in the message's `refs.slack` the channel or DM name, the
thread permalink, the sender's email, the last twelve exchanges of the thread and a note that
its reply is posted back to Slack for it and that it must not post to Slack itself for that
conversation. It gets no other channel and no private room history. A
message routed to several bots opens or continues one conversation per bot, all mapped to the
same thread; each reply lands in the thread under its own name.

## Channels, read like a bot reads them

A bot in a channel is meant to have the channel's full context, the way a human subscribed to
it does, and not in realtime: `@Tico` for a faster response, otherwise within the hour, if
there are changes. The store is the record of what is read:

- **Stored.** Every message in a channel Tico is in (`message.channels`, `message.groups`) is
  written to `slack_events` as it arrives, before the envelope is acknowledged, unique on
  `(channel, ts)`: humans' messages, bots' and apps' posts (an alert feed is all bots), Tico's
  own mirrored replies, with the author's kind and name. An edit (`message_changed`) replaces
  the stored text and marks the row edited; a deletion marks it deleted. Nothing is ever
  pruned; the history builds up in the database.
- **Read.** `slack_reads` holds one cursor per channel and reader: the newest `ts` that
  reader has been handed, and when. It moves only in the transaction that writes the digest.
  A message the fast lane already routed to a bot counts as read by it. A reader named for a
  channel for the first time starts at now: the channel's past is context, not unread.
- **Unread.** For a reader, the stored rows in the channel past its cursor, not deleted, plus
  rows it has seen that were edited or removed since its last pass. That is the whole query;
  Slack is never asked what is new.
- **Gaps.** Socket Mode replays nothing, so each pass first pulls `conversations.history` per
  readable channel from the newest stored message and `conversations.replies` for the threads
  the gateway talks in or whose root is on that page, into the same table. What history finds
  is stored, never routed: an old mention found this way wakes nobody late. A channel Tico is
  not a member of is reported once and skipped until the next start. This also means the
  readers work before `message.channels` is subscribed, an hour behind.

**The hourly pass** (`TICO_SLACK_DIGEST_MINUTES`, default 60; `0` pauses delivery while storage
goes on), for each reader that is an active bot, in one transaction:

1. Per channel it reads, the unread rows, grouped by thread in order. A thread whose root was
   seen before is shown with its root and the last three earlier replies marked `(earlier)`,
   then the new ones; a message edited after delivery comes again marked `(edited)`, one
   removed after delivery as `(message removed)`. Names are the roster names Slack reports,
   permalinks link the threads, the channel's registry `purpose` heads the section.
2. A channel with more than `TICO_SLACK_DIGEST_CAP` (200) unread is skimmed: the oldest 200,
   a line saying how many more, and the cursor still moves to the end, so a storm is not
   replayed next hour.
3. One message into the reader's `Slack channels` conversation (a `direct` conversation between
   the owner and the bot, opened once), written by the owner because nobody said it; it
   carries the channels, the count and the event ids in `refs.slack.digest`, and queues one
   run for the bot. A reader with several channels gets one message covering all of them, so
   an engineering bot's twenty alert channels are one run an hour, not twenty.
4. Nothing unread in any of its channels, nothing written, no run.
5. A channel with `digest_hours: N` reaches its readers at most once in N hours; until then its
   messages wait unread and the cursor stays. A high-volume channel is best read daily: otherwise every message would cost a short run.

Readers are named per channel in `registry/slack-channels.yaml` (`readers: [cto]`); naming one
is the owner's call, like `post: false`. Reading grants no posting right: a reader that wants to say
something in the channel is bound by `post:` and the solicited-reply rule exactly as before,
and its digest says so.

## Replies back to Slack

When a routed bot's reply (`say`, `ask` or `answer` to the human) lands in a mapped
conversation, the gateway posts it once in the thread with `chat.postMessage`: `username` is
the bot's display name, `icon_emoji`/`icon_url` come from `slack_icon`/`slack_icon_url` in the
bot's registry entry when set, and the last line is `(sent from <display name>)`. Slack still
shows the APP badge. Without the `chat:write.customize` scope the post goes out as Tico with the
footer alone, and the log says so once.

The reply text is escaped as Slack requires (`&`, `<`, `>`), so a bot writing `<!channel>` shows
those characters and pages nobody, and "x < y" survives the parser.

One try per post. A confirmed rate limit waits Slack's `Retry-After` and tries again, ten
tries at most, then the post is `failed`. Any other Slack refusal (`channel_not_found`,
`missing_scope`) is `failed`. A network fault, an unreadable answer or a crash between send and
record is `uncertain` and waits for a human.
Thread replies are solicited: they are not stopped by `post: false` in `registry/slack-channels.yaml` and
grant no bot any posting right there; `connectors/slack.py` and its gates are unchanged.

## A human's mentions

A human can connect their own Slack through a second app, the mentions app, so that every
channel thread that names them is kept for them alone and their approved reply is posted as
them. The gateway holds that app's socket and each connected human's user token, read from the
credential vault; it never reads their DMs. See [mentions.md](mentions.md).

## Tables

| Table | One row per | States |
|---|---|---|
| `slack_events` | Slack message, unique on `event_id` and on `(channel, ts)`; `author`, `author_name`, `edited`, `deleted` | `received`, `denied`, `recorded`, `routed`, `failed`; `stored` for a channel message the readers get |
| `slack_threads` | Slack thread and bot: the Tico conversation it continues | |
| `slack_posts` | bot reply to mirror | `ready`, `sending`, `sent`, `rate_limited`, `failed`, `uncertain` |
| `slack_reads` | channel and reader: the cursor (`last_ts`, `last_run`, `digests`) | |
| `slack_digests` | digest written: the reader, its conversation and message, the channels, the event ids it covered | |

All five are readable with `hub sql` (`docs/hub-sql.md`).

## Running it

In the Docker install, see [slack.md](slack.md): the `slack` compose profile runs `backend.slack_gateway` from the server image, and the tokens are pasted in Settings and stored encrypted. The settings below are environment variables of that container, in `.env`.

Settings: `TICO_SLACK_GATEWAY_ENABLED` (the kill switch), `SLACK_TEAM_ID`
(the one workspace whose events are accepted), `SLACK_APP_ID` (optional; the app `bots.info`
must name), `TICO_SLACK_SECRET_ARN` (the JSON credential `{"bot_token","app_token","team_id","app_id"}`;
`SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN` in the environment or `/etc/tico/slack` also work), and the
three thresholds above, `TICO_SLACK_DIGEST_MINUTES` and `TICO_SLACK_DIGEST_CAP` (Channels,
above). Only the gateway process reads it; the API never holds a Slack token. Change them in `.env` and run `docker compose up -d`, which restarts only the gateway.

Start-up verifies `auth.test` (the workspace) and `bots.info` (the app) and refuses to run on a
mismatch; it lists any missing scope in the log. `python -m backend.slack_gateway --check` runs
only that verification and prints it. A post that was mid-flight at the last stop is marked
`uncertain` on start, never resent.

Kill switch: `TICO_SLACK_GATEWAY_ENABLED=0` (through `configure`) stops ingress, egress and
the readers' pass; the tables stay for audit. `TICO_SLACK_DIGEST_MINUTES=0` pauses only the
readers' pass. Rollback beyond that is removing the `app_mention`, `message.im`,
`message.channels` and `message.groups` subscriptions from the app manifest; nothing in
Tico needs undoing.

## Installing or reinstalling the app

`connectors/slack-app-manifest.yaml` is the app: the scopes `connectors/slack.py` uses, plus
`app_mentions:read` and `chat:write.customize`, Socket Mode on, and the `app_mention`,
`message.im`, `message.channels` and `message.groups` events. When the installed app lacks any of these (the gateway log says which):

1. On <https://api.slack.com/apps>, open the Tico app, **App Manifest**, paste the manifest over
   the old one, save.
2. **Install App**, reinstall to the Acme workspace (Slack shows the reinstall banner).
3. If the Bot User OAuth Token changed, update `bot_token` in the `tico/slack` credential (and
   `SLACK_BOT_TOKEN` in the Mac computer's `secrets/_shared.env`). The app-level token
   (`xapp-`, `connections:write`, **Basic Information → App-Level Tokens**) is unchanged by a
   reinstall.
4. Restart the gateway unit (`configure` with the same values does it) and check the log for
   `Verified workspace` with no missing scopes.

## Recovery

Read the queue with `hub sql` or SQLite against the live database, read-only:

```sql
SELECT event_id, channel, thread_ts, state, reason, processed
FROM slack_events WHERE state NOT IN ('routed', 'recorded', 'denied') ORDER BY received;

SELECT message_id, bot, channel, thread_ts, state, attempts, next_attempt, slack_ts, error
FROM slack_posts WHERE state NOT IN ('sent') ORDER BY created;

-- what each reader has seen of each channel, and the last digest it got
SELECT r.channel, r.reader, r.last_ts, r.last_run, r.digests,
       (SELECT COUNT(*) FROM slack_events e WHERE e.channel=r.channel AND e.ts>r.last_ts AND e.deleted IS NULL) AS unread
FROM slack_reads r ORDER BY r.reader, r.channel;
```

A reader that should start further back than now: set its `last_ts` to the `ts` to start
after (the next pass delivers everything past it, capped). A reader removed from the registry
keeps its cursor rows; nothing is delivered to it.

For an `uncertain` post, open the exact thread in Slack. If the reply is there, record its
timestamp and close the row:

```sql
UPDATE slack_posts SET state='sent', slack_ts='<ts from Slack>', error=NULL WHERE message_id='<id>';
```

Only when its absence is confirmed may the one row go back for a single new try:

```sql
UPDATE slack_posts SET state='ready', error=NULL WHERE message_id='<id>';
```

Never delete an event or a post: the row is the dedupe record. A `failed` post stays failed;
fix the cause (a scope, a channel the app was removed from) and set that one row `ready` if the
reply should still go out. Parameterize values; never interpolate Slack text.

## What the tests cover

`backend/tests/test_slack_gateway.py`, with a fake Slack and a fake decision model: start-up verification,
every deny path, dedupe, the Legal reply case (Legal's open ask in the thread, the answer
reaches Legal in the same conversation), an explicit address, a message for two bots, a
four-way tie capped at three, the low-confidence fallback, a no-ask message that wakes nobody,
a refused recipient, at-most-once posting with the `uncertain`, `rate_limited` and `failed`
states, restart recovery, the customize fallback, a DM, and the kill switch. For the channels:
a stored message reaching every reader once and the cursors moving with it, the fast lane and
the pass never handing a reader the same message twice, a thread delivered with its earlier
lines as context, edits and deletions before and after delivery, the cap and the cursor moving
past what it skipped, a reply in a bot's thread routed at once, the channel copy of a mention
routed once, history filling a gap without routing and an unreadable channel skipped, a refused
delivery leaving the cursors where they were, and the pause. Nothing live is called. `backend/tests/test_mentions.py` covers a
human's mentions: one mention per thread with its context, the reply posted as them once, a DM
never read, a token used only for the human whose Slack it is, and only that human reading
their mentions.
