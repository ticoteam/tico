# Google Meet calls in threads: build contract

Since Nov 18, new calls default to Tico Meet and open the meeting window ([meet.md](../meet.md)); Google Meet is a per-call option. The rest is the original contract.

The shared contract for the tracks building Google Meet calls, the New message pane and phone push. Each track builds against this file; a change to it goes through the integration branch `chris/meet-calls`.

## What Chris approved (Nov 9-10)

- A thread's header has one call button (phone icon). One tap makes a Google Meet, opens it in a new tab and posts a
  call card in the thread. Google Meet is the default; Tico Meet (LiveKit, `backend/calls.py`) stays for later.
- Join links carry `?authuser=<the viewer's Google address>` so Meet opens on the right Google account, as Granola does.
- People in a DM or a thread of up to 8 people are rung: a popup on any Tico page (desktop), a bottom sheet (phone), a
  notification when Tico is hidden or closed. Everyone else in the thread only sees a green phone mark on the thread's
  rail row and the live card.
- Live status: who is in the call, a timer, the length once it ends. Source of truth in this order: Google's Meet API
  (polled), then nothing (card shows Join and the connector can End).
- The rail's pencil becomes **+** and opens a Slack-style **New message** pane: one To box over people, bots, groups
  and threads; threads with exactly the chosen people come first so Enter re-opens one; one person opens the DM; no
  match asks for a name (group chats need a name, unchanged).
- UI copy rules: rows are the title only; state is a mark, not words; buttons are a verb; no helper text or captions.

## Data (migration `backend/migrations/20261110060000_meet_calls.py`, server track)

`calls` gains:

| Column | Meaning |
| --- | --- |
| `provider TEXT NOT NULL DEFAULT 'tico'` | `tico` (LiveKit) or `google_meet` |
| `meet_space TEXT` | Google's space name, `spaces/<id>` |
| `meet_uri TEXT` | `https://meet.google.com/<code>` as Google returned it |
| `meet_code TEXT` | the meeting code |
| `meet_conference TEXT` | the last active `conferenceRecords/<id>` seen |
| `meet_seen_at TEXT` | first time a status source saw anyone in the Meet; the call's length counts from here |
| `meet_active_at TEXT` | last time a status source saw anyone in the Meet; the call's length counts to here |
| `meet_empty_at TEXT` | first successful read that showed nobody since someone was last seen (migration `20261110060000_meet_calls.py`); NULL while anyone is seen |
| `meet_polled_at TEXT` | last successful status read |
| `live_count INTEGER NOT NULL DEFAULT 0` | people in the Meet now, as the status source last said (unmatched guests included) |
| `status_source TEXT` | `google` or NULL (no live status) |
| `ring TEXT` | JSON list of the actors rung at start |
| `card_message_id TEXT` | the thread message that is the call's card |
| `transcript_message_id TEXT` | the transcript post, once made |

`call_participants` gains `in_call INTEGER NOT NULL DEFAULT 0`: 1 while the status source reports that person in the
Meet. For `google_meet` calls a row's `joined_at` is the first time they opened it from Tico or were first seen in it.

## API (server track)

All existing routes keep working for `tico` calls. Changes:

- `POST /api/v2/threads/{thread_id}/calls` body gains `provider`: `auto` (default), `google_meet`, `tico`. `auto` is
  Google Meet when it is available to the caller, else Tico Meet when LiveKit is set up, else `503 meet_unavailable`.
  A Google Meet start makes the space as the caller (Workspace service-account key impersonating the caller's
  mailbox, scope `https://www.googleapis.com/auth/meetings.space.created`), stores the call, posts the card message and
  rings. A retried request (same idempotency key) must not make a second space.
- `GET /api/v2/threads/{thread_id}/calls`: `available` stays; adds `providers` (what the viewer could start, in order).
  Calling a bot needs Tico Meet, so the UI shows a bot call (a one-bot DM's phone, the org chart's Call) only when
  `providers` has `tico`, or, with no thread to ask, when `GET /api/v2/config` says `features.tico_meet`.
- Call view (`GET /api/v2/calls/{id}`, the start/join answers, list items) adds:
  `provider`, `meet_uri`, `join_url` (meet_uri + `?authuser=<viewer's Google address>` when known, else meet_uri; null for
  `tico`), `live_count`, `status_source`, `seen_at` (= meet_seen_at), `duration_s` (ended calls only. Google Meet: meet_active_at
  minus meet_seen_at, null when nobody was ever seen; Tico Meet: ended_at minus started_at), `ring` (bool: the viewer was rung, has not joined, and the call is live), `card_message_id`,
  `transcript_message_id`, and on each participant `in_call`.
- `POST /api/v2/calls/{id}/join` on a `google_meet` call records the open and answers `{"call": view, "url": join_url}`
  (no token). `POST /api/v2/calls/{id}/token` on one answers `409 not_tico_meet`.
- `POST /api/v2/calls/{id}/end` (connector) on a `google_meet` call asks Google to end the active conference
  (`spaces/<id>:endActiveConference`, best effort), then locks the space (`PATCH spaces/<id>?updateMask=config.accessType`
  with `{"config":{"accessType":"RESTRICTED"}}`, best effort, a failure is logged) so the link can't be reused, and ends
  the call. A call the sweep ends (`meet_ended`, `no_answer`, `stale`) has its space locked the same way, on the poll
  pool.
- `GET /api/v2/calls/ringing`: live calls ringing the viewer now (page load and reconnect): `{"calls": [view, ...]}`.
- Thread rows (rail, browse, a thread's own row) gain `live_call`: `{"id", "live_count"}` or null.
- Change kinds on the `threads` topic: existing `call.*`, plus `call.updated` whenever `live_count`, `in_call`,
  `status_source` or `meet_seen_at` changes. The browser re-reads the call on any `call.*`.

### Status polling (the calls sweep, every heartbeat, 10 s by default)

Live `google_meet` calls are read in parallel on a pool of 4, and the sweep waits 5 s for the pass at most; a read
still out then is read again on a later pass (never two at once for one call). A call whose read failed skips 1, 2,
4... passes (at most 16) before its next. All Google requests happen outside DB write transactions; the roster is read
once per pass, each call's participants once per pass.

For each read: mint a Meet token for the connector's mailbox, `GET spaces/<id>`. With an `activeConference`, list its
participants that are still in it, match `signedinUser.displayName` (then email when present) to company people, set
`in_call`, `live_count`, `meet_seen_at`, `meet_active_at`, `meet_polled_at`, `status_source='google'` (also when the
read found nobody). A call someone was seen in ends `meet_ended` once successful reads have shown nobody for 60 s
(from `meet_empty_at`); a call nobody joined within 120 s ends `no_answer`. Only a successful read changes the status:
a read Google did not answer (timeout, 5xx, 429, status 0) keeps the count, `in_call` and source as they were and never
counts toward the empty minute. Google refusing (401/403/404: scope not authorized, mailbox not covered) leaves
`status_source` NULL. A call ends `stale` 8 hours after it started when it has no status from Google
(refused, or no successful read for 10 minutes). The connector can always End.

An ended call keeps `status_source` (the end does not clear it), so the UI can tell a
missed call (`duration_s` null, `status_source` set: something watched and saw nobody) from one nothing watched
(`duration_s` null, `status_source` null).

When a `google_meet` call ends: no LiveKit work; final windows run; the transcript is posted only when the call's live
meeting has chunks; action items as today. No "(no speech was transcribed)" post for a Meet call.

### Ringing

`ring` = the thread's human members (thread_members, people only, who can read the thread) other than the caller,
when the thread has at most 8 human members; otherwise empty. Bots are never rung. At start the server calls
`backend/call_ring.py` `ring(app, call_id, actors, payload)`, a hook list (`HOOKS`) that the push track fills; with no
hook it does nothing. `payload` = `{"title": "<caller name>", "body": "# <thread title>", "url": "<thread path>?call=<id>",
"tag": "call-<id>"}`.

### Scheduled calls (migration `backend/migrations/20261118133337_scheduled_calls.py`)

- `calls` gains `scheduled_start`, `scheduled_end` and `prepped_at` (all nullable); `calls.state` gains `scheduled`, and
  `calendar_event_links.link_type` gains `call`.
- `POST /api/v2/threads/{thread_id}/calls` with `starts_at` and `ends_at` stores a Tico Meet call `scheduled`, its live
  meeting `paused`, its card and its bots, with no participant and an empty `ring`. Before the write it writes one event
  as the caller (backend/calendar_api.py `plan_call_event`, the one exception to calendar decision 13): their Google
  calendar when connected (event id = the call id without dashes, `extendedProperties.private.tico_call`), else their
  Tico calendar, in the write. The link row is in the same write. A calendar refusal is `calendar_write_failed` and no
  call; a write that fails after the Google event was made deletes the event.
- A scheduled call is in no live list: not `live_call`, not ringing, not `GET /api/v2/live-meetings`, not the calls
  sweep. `GET /api/v2/calls/upcoming` lists the viewer's.
- The first `join` turns it `live` (started_at = now, the meeting `live`, `ring` the thread's other people), then starts
  the recorder and rings, once. A second live call in the thread is `409 call_in_progress`.
- The heartbeat's `call_prep` step (backend/call_prep.py, every minute): for calls due within 30 minutes and not
  prepped, or an hour past their end, re-read the event (`calendar_api.read_call_event`); moved moves the times,
  cancelled or gone ends the call `calendar_cancelled`; unreadable waits, three passes at most. Then one task per bot,
  "Prepare for <title>" (skills/meeting-prep/SKILL.md), and `prepped_at`, in one write guarded by `prepped_at IS NULL`.
  Past its end by an hour: `missed`.
- Change kinds: `call.scheduled`, `call.moved`, `call.prepped`.

## Google availability

`google_meet` is available to a person when the Workspace connection is active, covers their mailbox
(`google_workspace.covers`) and its key was proven for the Meet scope. The Meet scope is added to what the setup link
asks the admin to authorize and to the key check's probes, without changing `narrowable` or What Tico may do for keys
that lack it. The viewer's Google address for `authuser` is their mailbox when the Workspace covers it, else their
connected Google account's address when they have one, else none.

## Phone push (push track)

Web Push for the Tico web app and the installed app (`ui/manifest.webmanifest`, `ui/sw.ts`): VAPID keys made once per
install and kept sealed, a `push_subscriptions` table per person and browser, routes to read the public key, subscribe
and unsubscribe, the service worker's `push` and `notificationclick` handlers (focus or open `url`), and a switch in the
person's own notification settings. It registers a `call_ring.HOOKS` entry that sends the ring payload to the rung
people's subscriptions. iPhone push works once Tico is added to the home screen.

## File ownership

| Track | Branch | Owns |
| --- | --- | --- |
| Server | `chris/meet-calls-server` | backend/calls.py, backend/google_meet.py (new), backend/call_ring.py (new), backend/google_workspace.py (Meet scope only), backend/live_meetings.py (`_connector` only), thread row `live_call` (backend/thread_rail.py, backend/threads.py row builder), backend/openapi_v2.py call entries, the migration, docs/meet.md, scripts/meet-live-check.py (new), backend tests |
| Calls UI | `chris/meet-calls-ui` | ui/app/thread-call.ts, the call card in ui/app/thread-view.ts, the rail mark in ui/app/thread-rail.ts (row render only), ring popup/sheet (new ui/app/call-ring.ts), styles, ui tests |
| New message | `chris/meet-calls-newmsg` | rail + button and New message pane (ui/app/thread-rail.ts header, ui/app/threads-page.ts, new ui/app/new-message.ts), `GET /api/v2/threads/match` (backend/thread_api.py), ui + api tests |
| Push | `chris/meet-calls-push` | backend/web_push.py (new), its migration, ui/sw.ts, the switch in ui/app/person-page.ts, docs/push.md (new), tests |
