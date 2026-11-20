# Tico in Slack: the gateway

> **Beta**: the Slack gateway works today and may still change. See [Beta features](support.md#beta-features).

A DM to Tico, or an `@Tico` in a channel Tico has been invited to, wakes the bot the
message is for. The reply comes back in the same thread or DM under that bot's name, with
`(sent from <bot>)` as its last line. Everything else said in a channel Tico is in is
stored, and a channel bots read comes into its Tico thread, where its readers catch up
within the hour. One Slack app, one process on the server,
and the decision model deciding who a DM or a mention is for. The code is
`backend/slack_gateway.py`.

## What wakes a bot

| In Slack | What happens |
|---|---|
| A DM to Tico | Routed; the reply lands at the bottom of the DM, or in the reply thread the human wrote in. A DM is one Tico conversation per bot for its life, however the human types: a follow-up at the bottom of the DM continues it. |
| `@Tico` in a channel Tico is in | Routed; the reply goes under the message. Mentioning Tico again in that thread continues the same conversation. |
| A reply in a thread a bot already talks in, no mention | Routed at once, like a mention: the decision model sees the thread's routing and the reply usually continues it. |
| Any other message in a channel Tico is in, bots' posts included | Stored (Channels, below). In a channel bots read it also goes into the channel's thread, and its readers hear of it in the hourly bot digest; nobody is woken now. A bot still reads a channel with `connectors/slack.py history` during its own run, which the gateway answers (Bots on computers, below). |
| An edit or a deletion in a channel | Applied to the stored message, and to the thread's copy. |
| A join, a reaction | Ignored. |
| A file shared with a DM, a mention or a channel message | A `[file F...] name` line per file is added to the message; the bot opens it with `connectors/slack.py file --id F...`, which the gateway downloads (the app needs `files:read`). |
| A Slack Connect or externally shared channel | Refused; nothing is written. |
| A guest, a deactivated account, a sender whose verified email is not on `registry/people.yaml` or not admitted by `registry/hub-access.yaml` | Refused silently: no reply, the reason on the event. |

A DM is a front door to whichever bot the message is for, never a human's private Tico
thread (`docs/threads.md`); those threads are untouched. The human's profile in Tico
(`#/person/<id>/slack`) shows those DMs, including their replies. A bot that messages a
human in Tico (Legal saying something to Thomaz) is posted through the same Tico Slack
app into their DM, so it shows up there too.

## How a message is routed

Every accepted message is one decision, through the same primitive every bot has
(`clients/judge.py`, `skills/decisions/SKILL.md`); the three fixed questions are the shared set
`questions/slack-route.json` and the call is labelled `slack-route@1`. The state the decision model reads:

- the message, the sender (roster human, group, `primary_for`) and the channel or DM with its
  note from the Slack channel list (Tools > Slack);
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
| asks-for-anything below `TICO_SLACK_ASK_THRESHOLD` (0.5) | routes to the assistant (or BotOps): the decision model picks who gets a person's message, never whether anyone does. With neither active, the message is recorded on the thread's conversations and nobody is woken |
| one or more bots at or above `TICO_SLACK_ROUTE_THRESHOLD` (0.6) | routes to each, best `TICO_SLACK_MAX_RECIPIENTS` (3) by confidence, ties by slug |
| no bot at threshold | routes to the assistant (`TICO_ASSISTANT_BOT`) with the top three candidates on the message, so it can ask the one clarifying question the write layer allows. A team that chose no assistant (it is optional) gets BotOps instead; with neither active, the message is recorded and nobody is woken |
| a chosen bot paused or quarantined since the roster was read | keeps the message for it (it runs when the bot is back) and tells the person in the thread that it waits |
| a chosen bot the write layer refuses (archived, planned or gone since the roster was read) | drops it with the reason; the assistant (or BotOps) if that empties the set |

Without a decision model (no gateway key and no TypeSafe key of your own) every message goes to the assistant (or BotOps) and the message says so
(`routed_by: default`). When the decision model is unreachable, the message stays pending and retries after a minute, for up to
ten minutes. A refused call, or one still failing after that, ends the message as `failed` with the
reason on it, and Tico tells the human in the thread; nothing retries a failed message. Each try asks the providers in
order, one short HTTP attempt each with no client-side retries on top of the gateway's own: Jev on the Tico AI Gateway
(`tico-decide-low`), then Jev on your own TypeSafe key if you set one; no other model is asked. A failure moves on to
the next, so an outage costs at most eleven attempts per provider per question batch for a message.

Routing a message is a decision call on the server's key, so it counts against the sender's daily decision cap
(`TICO_DECISIONS_DAILY_CALLS_OWNER` / `_HUMAN`, the same cap as `POST /api/v2/decisions`) and is audited as one
`judge.call` event by the sender (`via: slack`, the label, the question count, the provider and model that answered, usage and latency; never the
text, and a failed call keeps only its error code, never the provider's error text). A message counts once, when it is routed or finally fails, however many tries it took. Over the cap the decision
model is not asked: the message goes to the assistant (or BotOps) with `routed_by: budget` and the reason on it, and when
neither is running it is recorded and Tico tells the human in the thread.

When the team's decision spend for the month has reached its cap (`TICO_DECISIONS_MONTHLY_USD`, backend/judge.py), the
decision model is not asked either: the message goes to the assistant (or BotOps) with `routed_by: spend_cap`, as it would
with no decisions key. A big fleet is asked in several calls, and the cap is checked before each: one whose most (its
input and the longest answer, at the price of the model that answers) would not fit under the cap stops the message
there and it goes to the assistant the same way. Each call's cost is written down as it returns
(a `judge.spend` event, which the monthly cap sums and the sender's daily calls do not count), so a later call failing,
its retries and the final failure lose none and add none; each `judge.call` the gateway audits repeats the message's
`cost_usd` for reading.

Every score and answer is stored: on the event (`slack_events.routing`) and on the message
(`refs.routing`), where the conversation view shows it as a chip (`routed by decisions to Legal 92%`).
A misroute is fixed by a better description in the bot's registry entry, not by a rule.

Stored answer for one routed message, as the message's `refs.routing` carries it:

```json
{"routed_by": "judge", "model": "judge-latest",
 "recipients": [{"bot": "legal", "confidence": 0.92}],
 "candidates": [{"bot": "legal", "confidence": 0.92}, {"bot": "assistant", "confidence": 0.3}, {"bot": "cmo", "confidence": 0.0}],
 "scores": {"cmo": 0.0, "content-social": 0.0, "assistant": 0.3, "cto": 0.0, "legal": 0.92, "seo": 0.0},
 "asks": 0.9, "reply_to_last": 0.1, "names_bot": 0.1,
 "thresholds": {"route": 0.6, "ask": 0.5, "max": 3},
 "fallback": false, "dropped": [], "reason": "",
 "delivered": [{"bot": "legal", "conversation_id": "…", "message_id": "…", "opened": true}]}
```

## Identity and what the bot sees

The Tico message is written through `hubdb.say` as the verified human (`human:<id>`), never as
Tico, into a DM between that human and the bot, mapped to the Slack thread in `slack_threads`.
Linking a channel to a Tico thread both ways is separate ([Slack](slack.md#link-a-channel-to-a-thread)).
The guardrails in `docs/how-it-works.md` apply unchanged: an active bot only,
except that a person's message, a new thread or a reply in one, to a paused or quarantined bot is kept and runs when the
bot is back (the gateway tells the person in the thread that it waits and when the bot is back); the cap on bot-to-bot traffic, the unsolicited cap, the lint, the escape rule (a message that
names a `secrets/` path is refused and recorded as such).

A follow-up in a thread is linked to the bot's last line (`in_reply_to`).

The bot's run gets the text, and in the message's `refs.slack` the channel or DM name, the
thread permalink, the sender's email, the last twelve exchanges of the thread and a note that
its reply is posted back to Slack for it and that it must not post to Slack itself for that
conversation. It gets no other channel and no other thread's history. A
message routed to several bots opens or continues one conversation per bot, all mapped to the
same thread; each reply lands in the thread under its own name.

## Channels, read like a bot reads them

A bot that reads a channel has the channel's full context, the way a person subscribed to it does, and not in
realtime: `@Tico` for a faster response, otherwise within the hour, if there are changes.

- **Stored.** Every message in a channel Tico is in (`message.channels`, `message.groups`) is
  written to `slack_events` as it arrives, before the envelope is acknowledged, unique on
  `(channel, ts)`: humans' messages, bots' and apps' posts, Tico's own mirrored replies, with
  the author's kind and name. An edit (`message_changed`) replaces the stored text and marks the
  row edited; a deletion marks it deleted. Message automations on the channel hear these rows,
  and bots' `history` reads are answered from Slack.
- **Read-only thread.** A channel bots read is linked to a Tico thread that only takes Slack in
  (`slack_thread_links.read_only`, see [threads.md](threads.md)): a members thread named for the
  channel, owned by the person who listed it, with the readers as members. Each message goes in as
  its roster author through screening and the org rule check, like a two-way link's, and nothing
  goes back to Slack: no copies of the thread, and no private note when a message is held. The
  channel's members are not mapped, so someone who is not on the roster suspends nothing; their
  messages are skipped. Only a definite answer from `users.info` (`user_not_found`, `user_not_visible`,
  `account_inactive`) counts as not on the roster: a rate limit or no answer keeps the message queued and tries
  again, up to five times with a growing wait, each logged as `slack.inbound.sender` with the link and event.
  Another app's post (an alert, a deploy) is carried in by Tico under the app's name, a plain line that reaches
  the readers; Tico's own posts never are. A two-way link still carries no app's post. Slack Connect is still
  refused.
- **Catch-up.** A read-only link remembers the newest Slack message it has read (`read_ts`). Once an hour, and
  right after the gateway reconnects, it reads the channel's history (replies included) from there and carries
  what the socket missed through the same queue; a message the thread already has is not carried twice. A
  failed read is logged (`slack.catch_up`) and starts from the same place next time.
- **Replies.** A Slack thread reply goes into a sub-thread of the message it answers; the readers follow the
  sub-thread at `digest` before the reply is written, so it is in their digest too.
- **Readers.** Each reader follows the thread at `digest`. The hourly bot digest
  (`thread_bot_digest`) wakes an active reader once with every thread it follows that has news,
  so an engineering bot's twenty alert channels are one run an hour, not twenty. A paused bot
  waits until it is back.

Readers are named per channel in the Slack channel list, which lives in the database and is managed
in the app (Tools > Slack channels, `tico slack channel add '#name' --reader cto`, or BotOps
as the person who asked). Only an owner or an admin changes it, like turning posting off. Adding a reader to a
channel with no link makes the read-only link; a channel already linked both ways keeps its link, and the reader
follows that thread. Taking the last reader off, or the channel off the list, ends a read-only link. A channel may be
listed by name alone: the gateway asks Slack for its id. Reading grants no posting right: a reader that wants to say
something in the channel is bound by `post:` and the solicited-reply rule exactly as before.

The gateway used to read these channels itself, once an hour, into a digest message per reader. The upgrade
moves each channel's readers to a read-only link and follows (`20261117104315_slack_readers_to_links`). The link
starts reading at the oldest reader's last read, so what no reader had read yet is carried in and counted in their
digests; a channel no reader had read starts at its activation. The old digests stay readable on the bot's Messaging page link (`GET /api/v2/messaging/bots/{bot}/slack-digests/{id}`).

## Replies back to Slack

When a routed bot's reply (`say`, `ask` or `answer` to the human) lands in a mapped
conversation, the gateway posts it once in the thread with `chat.postMessage`: `username` is
the bot's display name, `icon_emoji`/`icon_url` come from `slack_icon`/`slack_icon_url` in the
bot's registry entry when set, and the last line is `(sent from <display name>)`, or
`(sent from <display name> on #<number> <task title>)` linked to the task when the reply is about one. Slack still
shows the APP badge. Without the `chat:write.customize` scope the post goes out as Tico with the
footer alone, and the log says so once.

A reply with visuals (pictures, SVG, visual pages, video, audio) keeps its text and adds one line,
**1 visual - view in Tico** (or **N visuals**), linking to the message in Tico; no visual is posted to Slack. A
task-done post adds the same line for the owner's result comment, before **Open task**. See
[Visuals in replies](visuals.md#outside-tico).

The reply text is escaped as Slack requires (`&`, `<`, `>`), so a bot writing `<!channel>` shows
those characters and pages nobody, and "x < y" survives the parser.

One try per post. A confirmed rate limit waits Slack's `Retry-After` and tries again, ten
tries at most, then the post is `failed`. Any other Slack refusal (`channel_not_found`,
`missing_scope`) is `failed`. A network fault, an unreadable answer or a crash between send and
record is `uncertain` and waits for a human.
Thread replies are solicited: turning bot posting off for a channel does not stop them, and they
grant no bot any posting right there; a bot's own posts go through `connectors/slack.py` and its gates (below).

## Bots on computers

A bot never holds the Slack token. `connectors/slack.py` on its computer asks Tico (`backend/slack_bots.py`), which
checks the bot's `service: slack` entry, onboarding, the channel list, the 3,800-character limit (what the gateway sends whole), the recipient, the
org's rules and screening, and then has the gateway act:

- `GET /api/v2/slack/history` and `GET /api/v2/slack/files/{id}` write a row to `slack_requests`. The gateway's
  request thread (beside its tick, so a slow decision model never holds a read) claims it and hands it to one of four
  workers, which asks Slack and writes the answer back; the API reads it and deletes the row. The API waits without
  holding a thread, at most 16 waits per server (503 `slack_busy` past that). Before each Slack call a worker checks
  that the API still waits (the row is there and its deadline, 60 s for history and 150 s for a file, has not passed),
  so a read the API gave up on stops at once. A ref like `<#C...|name>` is authorized on its id alone. A file's bytes go to `slack-files/` beside the database (the
  shared `tico-data` volume), never into it, and are deleted once streamed.
- `POST /api/v2/slack/posts` writes the post as a message in the bot's room plus a `slack_posts` row, which `deliver`
  sends with the bot's name and `(sent from <bot>)`. `deliver` asks Slack about the channel on every post
  (never from a cache) and cancels it in a Slack Connect channel; a rate-limited check leaves it for the next pass.
- `POST /api/v2/slack/dms` is the bot's message to the person in Tico, which `originate` copies into their Slack DM.

With the gateway not connected, or not claiming a request within 8 seconds, each route answers 503 and says what to
start. A computer on v0.6.0-rc.1 still runs its own connector with a granted `SLACK_BOT_TOKEN` (older computers only).

## Link previews

A link to a task pasted in Slack gets a preview under the message, the way Slack's own links do:
the task's number and title linked to the pasted URL, the start of its first message, its column (or
status), owner and due date, and an open button. The links that get one, on the server's own host
and any in `TICO_SLACK_UNFURL_HOSTS`:

- `/task/<id>` and `/ticket/<number>`, each optionally with a readable slug after it
  (`/ticket/42/fix-the-login-page`) and optionally under `/open/`; the slug is never read, so a
  renamed task keeps its old links;
- `/#/task/<id>`, the web app's task page.

The host must be in the app's `unfurl_domains` (the manifest Settings > Slack hands out lists them).
Slack sends `link_shared` over the same socket; the gateway answers with `chat.unfurl` at once and
stores nothing. A preview is shown only when the person who pasted the link passes the same sender
checks as a message (workspace member, not a guest, admitted, on the roster) and may read the task,
and only for a task that is not private: nothing of a private task goes to Slack. A channel shared
outside the workspace gets none, and so does a channel whose info Slack will not give Tico (a private
channel it is not in) and a link still in the composer, whose channel is not known yet. A DM gets one
either way: the sender is verified there. Everyone in the conversation then sees the preview; each one is
recorded as a `slack.unfurl` event with the task and the person.

The button opens `TICO_SLACK_OPEN_URL` (`{id}` is the task id; default the task in the web app) and
reads `TICO_SLACK_OPEN_LABEL` (default "Open in Tico"), so a team with its own desktop client can
open the task there. Slack does not take every URL scheme on a button; when it refuses the blocks,
the preview is sent again with the same link as text.

## Tables

| Table | One row per | States |
|---|---|---|
| `slack_events` | Slack message, unique on `event_id` and on `(channel, ts)`; `author`, `author_name`, `edited`, `deleted` | `received`, `denied`, `recorded`, `routed`, `failed`; `stored` for a channel message nobody was woken for |
| `slack_threads` | Slack thread and bot: the Tico conversation it continues | |
| `slack_posts` | bot reply to mirror | `ready`, `sending`, `sent`, `rate_limited`, `failed`, `uncertain` |
| `slack_requests` | bot's history read or file download waiting on the gateway; deleted once read, or after 10 minutes | `pending`, `running`, `done`, `failed` |
| `slack_digests` | digest the old hourly pass wrote (read-only; nothing writes it) | |

Only `slack_posts` is in `tico sql`'s safe-column list (`backend/sql.py`, `docs/hub-sql.md`); the other Slack tables are not readable there.

## Running it

In the Docker install, see [slack.md](slack.md): the `slack` compose profile runs `backend.slack_gateway` from the server image, and the tokens are pasted in Settings and stored encrypted. The settings below are environment variables of that container, in `.env`.

Settings: `TICO_SLACK_GATEWAY_ENABLED` (the kill switch), `SLACK_TEAM_ID`
(the one workspace whose events are accepted), `SLACK_APP_ID` (optional; the app `bots.info`
must name), `TICO_SLACK_SECRET_ARN` (the JSON credential `{"bot_token","app_token","team_id","app_id"}`;
unset, the tokens come from Tools > Slack), and the
three thresholds above, and `TICO_SLACK_UNFURL_HOSTS`, `TICO_SLACK_OPEN_LABEL` and `TICO_SLACK_OPEN_URL` (Link previews,
above). Only the gateway process reads it; the API never holds a Slack token. Change them in `.env` and run `docker compose up -d`, which restarts only the gateway.

Start-up verifies `auth.test` (the workspace) and `bots.info` (the app) and refuses to run on a
mismatch; it lists any missing scope in the log. `python -m backend.slack_gateway --check` runs
only that verification and prints it. A post that was mid-flight at the last stop is marked
`uncertain` on start, never resent.

Kill switch: `TICO_SLACK_GATEWAY_ENABLED=0` (through `configure`) stops ingress and egress,
read channels included; the tables stay for audit. Rollback beyond that is removing the `app_mention`, `message.im`,
`message.channels`, `message.groups` and `link_shared` subscriptions from the app manifest; nothing in
Tico needs undoing.

## Installing or reinstalling the app

`connectors/slack-app-manifest.yaml` is the app: the scopes bots' reads and posts use, plus
`app_mentions:read`, `chat:write.customize`, `links:read` and `links:write`, Socket Mode and
interactivity on, the `app_mention`, `message.im`, `message.channels`, `message.groups` and
`link_shared` events, and the link-preview hosts in `unfurl_domains`. When the installed app lacks any of these (the gateway log says which):

1. On <https://api.slack.com/apps>, open the Tico app, **App Manifest**, paste the manifest over
   the old one, save.
2. **Install App**, reinstall to the Acme workspace (Slack shows the reinstall banner).
3. If the Bot User OAuth Token changed, paste it in Tools > Slack (or, with `TICO_SLACK_SECRET_ARN` set,
   update `bot_token` in that secret), and in the `SLACK_BOT_TOKEN` credential granted to bots if any computer
   is still on v0.6.0-rc.1 (older computers only). The app-level token
   (`xapp-`, `connections:write`, **Basic Information → App-Level Tokens**) is unchanged by a
   reinstall.
4. Restart the gateway unit (`configure` with the same values does it) and check the log for
   `Verified workspace` with no missing scopes.

## Recovery

Read the queue with `tico sql` or SQLite against the live database, read-only:

```sql
SELECT event_id, channel, thread_ts, state, reason, processed_at
FROM slack_events WHERE state NOT IN ('routed', 'recorded', 'denied') ORDER BY received_at;

SELECT message_id, bot, channel, thread_ts, state, attempts, next_attempt_at, slack_ts, error
FROM slack_posts WHERE state NOT IN ('sent') ORDER BY created_at;

-- each read channel's link and readers
SELECT l.channel_ref, l.channel_id, l.state, l.reason, f.actor
FROM slack_thread_links l LEFT JOIN thread_followers f
  ON f.thread_id=l.thread_id AND f.following=1 AND f.notify='digest' AND f.actor LIKE 'bot:%'
WHERE l.read_only=1 AND l.state<>'unlinked' ORDER BY l.channel_ref, f.actor;
```

A read-only link that stays `requested` or is `refused` names why in `reason`; invite Tico to the channel, then
add a reader again (or Resume) to ask once more.

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
four-way tie capped at three, the low-confidence fallback, a message with no question that wakes nobody,
a refused recipient, at-most-once posting with the `uncertain`, `rate_limited` and `failed`
states, restart recovery, the customize fallback, a DM, and the kill switch. For the channels:
a read channel's message stored and carried into its thread, its active readers woken once by
the hourly bot digest (a paused one waits), nothing posted back, a reply in a bot's thread routed
at once, the channel copy of a mention routed once, and message automations hearing a read
channel but not a two-way linked one. `backend/tests/test_slack_threads.py` covers the read-only
link itself (a reply in each reader's digest, apps' posts, a sender Slack cannot look up yet, catch-up, and
the unread backlog across the upgrade) and `backend/tests/test_slack_channels.py` the list, its readers and the upgrade.
Nothing live is called.
`backend/tests/test_slack_bots.py` covers the bots' routes: every gate, the 503s, history and files through the
request thread, a post in the outbox and its Slack Connect cancel, a DM, and Send anyway on a held post.
