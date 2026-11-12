# Google Meet calls in threads: build contract

The shared contract for the tracks building Google Meet calls, the New message pane, the Recorder on calls and phone
push. Each track builds against this file; a change to it goes through the integration branch `chris/meet-calls`.

## What Chris approved (Nov 9-10)

- A thread's header has one call button (phone icon). One tap makes a Google Meet, opens it in a new tab and posts a
  call card in the thread. Google Meet is the default; Tico Meet (LiveKit, `backend/calls.py`) stays for later.
- Join links carry `?authuser=<the viewer's Google address>` so Meet opens on the right Google account, as Granola does.
- People in a DM or a thread of up to 8 people are rung: a popup on any Tico page (desktop), a bottom sheet (phone), a
  notification when Tico is hidden or closed. Everyone else in the thread only sees a green phone mark on the thread's
  rail row and the live card.
- Live status: who is in the call, a timer, the length once it ends. Source of truth in this order: Google's Meet API
  (polled), then the Tico Recorder (when one is on the call), then nothing (card shows Join and the connector can End).
- Starting or joining a call from Tico on a Mac opens the Tico Recorder, which records the call into the call's live
  meeting. One Recorder per call is the source; the transcript and action items land in the thread when the call ends
  (the existing call wrap-up). Calls skip the Recorder's Finish sheet.
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
| `status_source TEXT` | `google`, `recorder` or NULL (no live status) |
| `ring TEXT` | JSON list of the actors rung at start |
| `card_message_id TEXT` | the thread message that is the call's card |
| `recorder_actor TEXT`, `recorder_claimed_at TEXT` | the person whose Recorder is the call's recording source |
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
- Call view (`GET /api/v2/calls/{id}`, the start/join answers, list items) adds:
  `provider`, `meet_uri`, `join_url` (meet_uri + `?authuser=<viewer's Google address>` when known, else meet_uri; null for
  `tico`), `live_count`, `status_source`, `seen_at` (= meet_seen_at), `duration_s` (ended calls only. Google Meet: meet_active_at
  minus meet_seen_at, null when nobody was ever seen; Tico Meet: ended_at minus started_at), `ring` (bool: the viewer was rung, has not joined, and the call is live), `card_message_id`,
  `transcript_message_id`, `recorder: {"source": <actor or null>, "installed": <viewer has a Recorder seen in the last
  30 days, recorder_installs.mine(...)["recent"]>}`, and on each participant `in_call`.
- `POST /api/v2/calls/{id}/join` on a `google_meet` call records the open and answers `{"call": view, "url": join_url}`
  (no token). `POST /api/v2/calls/{id}/token` on one answers `409 not_tico_meet`.
- `POST /api/v2/calls/{id}/end` (connector) on a `google_meet` call asks Google to end the active conference
  (`spaces/<id>:endActiveConference`, best effort), then locks the space (`PATCH spaces/<id>?updateMask=config.accessType`
  with `{"config":{"accessType":"RESTRICTED"}}`, best effort, a failure is logged) so the link can't be reused, and ends
  the call. A call the sweep ends (`meet_ended`, `no_answer`, `stale`) has its space locked the same way, on the poll
  pool.
- `GET /api/v2/calls/ringing`: live calls ringing the viewer now (page load and reconnect): `{"calls": [view, ...]}`.
- `POST /api/v2/calls/{id}/recorder` (body `{}`): a person who can read the thread and is in the call claims the
  recording source for a live call. In the call means: the connector, a person with a `call_participants` row that was
  not removed (they opened it from Tico, or a status source saw them in it), or one of the thread's own people
  (`thread_members`, a DM's or group chat's people). Anyone else who can read the thread (a company thread's readers)
  gets `403 not_in_call`. First claim wins and is kept by its holder; answers `{"source": bool, "meeting_id", "title",
  "source_actor"}`. `DELETE /api/v2/calls/{id}/recorder` releases it (holder only). While held, the holder may write
  chunks and corrections to the call's live meeting (`backend/live_meetings.py` `_connector` allows the call's
  `recorder_actor` for those two routes only), and for 90 s after the call ended (`RECORDER_TAIL_SECONDS`, the
  Recorder's upload tail); after that they are refused as before. A call with a recording source posts its transcript
  only once the tail is over.
- `POST /api/v2/calls/{id}/presence` body `{"names": [str, ...]}` (max 100): the recording source's Recorder reports
  who it sees in the Meet window. Used only while Google gives no status (`status_source` is not `google`); names are
  matched to company people by display name; `live_count` = len(names).
- Thread rows (rail, browse, a thread's own row) gain `live_call`: `{"id", "live_count"}` or null.
- Change kinds on the `threads` topic: existing `call.*`, plus `call.updated` whenever `live_count`, `in_call`,
  `status_source`, `recorder_actor` or `meet_seen_at` changes. The browser re-reads the call on any `call.*`.

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
`status_source` to the Recorder or NULL. A call ends `stale` 8 hours after it started when it has no status from Google
(refused, or no successful read for 10 minutes). The connector can always End.

An ended call keeps `status_source` (the end and a later Recorder release do not clear it), so the UI can tell a
missed call (`duration_s` null, `status_source` set: something watched and saw nobody) from one nothing watched
(`duration_s` null, `status_source` null).

When a `google_meet` call ends: no LiveKit work; final windows run; the transcript is posted only when the call's live
meeting has chunks (from the Recorder); action items as today. No "(no speech was transcribed)" post for a Meet call.

### Ringing

`ring` = the thread's human members (thread_members, people only, who can read the thread) other than the caller,
when the thread has at most 8 human members; otherwise empty. Bots are never rung. At start the server calls
`backend/call_ring.py` `ring(app, call_id, actors, payload)`, a hook list (`HOOKS`) that the push track fills; with no
hook it does nothing. `payload` = `{"title": "<caller name>", "body": "# <thread title>", "url": "<thread path>?call=<id>",
"tag": "call-<id>"}`.

## Google availability

`google_meet` is available to a person when the Workspace connection is active, covers their mailbox
(`google_workspace.covers`) and its key was proven for the Meet scope. The Meet scope is added to what the setup link
asks the admin to authorize and to the key check's probes, without changing `narrowable` or What Tico may do for keys
that lack it. The viewer's Google address for `authuser` is their mailbox when the Workspace covers it, else their
connected Google account's address when they have one, else none.

## Recorder (Recorder track)

- Link: `tico-recorder://record?server=<org address>&call=<call id>`. The Recorder accepts it only for a server it is
  signed in to (else it signs in to that org first, then continues), claims the source (`POST /calls/{id}/recorder`)
  and, when it is the source, starts recording into the call's live meeting (`meeting_id` from the claim: no new live
  meeting is created). It comes to the front either way. Not the source: it records nothing.
- When the call's live meeting ends (the Recorder sees state `ended`), recording stops by itself, no Finish sheet; the
  recording is kept as sent to that org (the server posts the transcript; the Recorder must not import it again).
- Stopping by hand before the call ends releases the source (`DELETE /calls/{id}/recorder`).
- Signed in to the org already, it records at once; when it had to sign in first, it asks "Record <title>?" once
  and records only on Record (Cancel releases the source). Join on a calendar event (`event=`, calendar contract)
  behaves the same.
- Stretch: while the source and Google gives no status, post Meet window names to `/calls/{id}/presence` every 10 s.
- The Tico web page opens the link on Macs when `recorder.installed`; the Tico desktop app opens it without a browser
  prompt.

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
| Recorder | `chris/meet-calls-recorder` | recorder/**, app/src/recorder.rs (desktop app opens the record link) |
| Push | `chris/meet-calls-push` | backend/web_push.py (new), its migration, ui/sw.ts, the switch in ui/app/person-page.ts, docs/push.md (new), tests |
