# Live Meetings API and Recorder event contract

This is Recorder wire contract revision 3 for the Tico Live Meetings API v2 server path in PR #92.
It handles transcript text only. Raw audio is never sent to or stored by Tico. Request and response
shapes below are part of the contract; change them only with a reviewed contract update.

## Identity and visibility

- A signed-in human explicitly connects a meeting. A meeting is not team-visible before that action.
- After connect, signed-in company humans can read the meeting's live state, transcript, chat, and
  router trace. Only the connector can write transcript chunks, pause/resume, disconnect, end, or
  finalize; another human explicitly joins before chatting or attaching a bot.
- A joined human may attach active org-chart bots they are allowed to see. Attachment creates a
  meeting-scoped read grant; it does not change the bot's standing access or grant access to other
  meetings.
- An attached bot can read this meeting's transcript and chat, and can post a reply attributed to
  itself. It cannot control the meeting or change its membership.

## HTTP routes

All routes are under `/api/v2/live-meetings` and require the same Tico session or token used by the
signed-in company client. For an authenticated SSE fetch in Recorder's main process, send the human
credential in `Authorization: Bearer <credential>` just as for the other API calls. Do not put the
credential in the URL. The Tico instance is the tenant boundary: a Recorder connected to one
instance uses that user's credential for this instance only. The connector identity is stored as
`owner_actor` and owns transcript input, lifecycle control and finalization. Joined humans can attach
only bots visible to them under the existing org-chart permissions.

Every `POST` uses the normal Tico `Idempotency-Key` header (1–200 characters). Repeating the same
operation, key, and body returns the saved response; reusing a key for different content returns
HTTP 409 with `error.code = "idempotency_conflict"`. Errors use
`{"error":{"code":"...","detail":"...","retryable":false,...}}`; endpoint-specific cursor
fields are listed below.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /api/v2/live-meetings` | Human | Explicitly connect; body `{title,client_id?,fallback_window_seconds?,threshold?}`. Returns the meeting view described below. Fallback window is 30–45 seconds; repeating a `client_id` for that person returns the same meeting. |
| `GET /api/v2/live-meetings` | Human | List live and paused meetings newest first; ended meetings are excluded. |
| `GET /api/v2/live-meetings/bot-candidates` | Human | Return `{"bots":[{"slug":"ops","name":"Operations","description":"...","team":"..."}]}`. `team` may be null. Only active bots visible to this human are included; `bots` may be empty. |
| `GET /api/v2/live-meetings/{id}` | Company human, or attached bot | Read state, transcript chunks, chat, attached bots, window traces, named-bypass traces, and pending/replied turns. |
| `POST /api/v2/live-meetings/{id}/chunks` | Connector | Append `{chunks:[{seq,speaker,start_ms,end_ms,text}]}` (1–500 chunks). Seq starts at 1 and is contiguous. HTTP 200 body is exactly `{"id":"<live-id>","state":"live|paused|ended","seq":<highest-seq>,"accepted":[<seq>,...],"event_id":<meeting-event-id>}`. Identical duplicate seq is acknowledged without a new event; a different payload for an existing seq returns `409` with `error.code = "sequence_conflict"`; a gap returns `409` with `error.code = "sequence_gap"` and `error.expected_seq`. New chunks are accepted only in `live`; an already-stored identical retry may still be acknowledged while paused or ended. |
| `POST /api/v2/live-meetings/{id}/chunks/{seq}/corrections` | Connector | Append `{revision,speaker,start_ms,end_ms,text}`. Revision 2 follows initial revision 1. HTTP 200 body is exactly `{"id":"<live-id>","seq":<seq>,"revision":<revision>,"replayed":<bool>,"event_id":<meeting-event-id>}`. Repeating the same revision and content returns `replayed:true` without a new event; changed content at an existing revision returns `409` with `error.code = "revision_conflict"`; a gap returns `409` with `error.code = "revision_gap"` and `error.expected_revision`. Corrections are accepted while live or paused. The latest revision becomes the transcript used for display and finalization; older versions remain replayable. |
| `POST /api/v2/live-meetings/{id}/join` | Human | Join the connected meeting. Joined people can chat; all company humans can read after connect. |
| `POST /api/v2/live-meetings/{id}/chat` | Joined human or attached bot | Append `{text,at_ms?,transcript_seq?,turn_id?}`. An attached bot must claim its turn first and name it on reply. A transcript reference links chat/replies to the captured words. Named routing is suppressed during pause. |
| `POST /api/v2/live-meetings/{id}/bots` | Joined human | Attach `{bots:[org_chart_slug,...]}`. Each bot receives meeting-only read/reply access. |
| `POST /api/v2/live-meetings/{id}/control` | Connector | `{action:"pause"|"disconnect"|"resume"|"end"}`. Pause and disconnect are server-side live-sharing controls: both set Tico state to `paused`, preserve text/chat, suppress new routing, and reject new chunk writes. They do not stop Recorder's local device capture, local recording, or review. Recorder must stop uploading chunks while paused/disconnected; local capture/review behavior remains under Recorder and the person. Reconnect with the same `client_id`; only an explicit `resume` re-enables live chunk writes. End is terminal. |
| `POST /api/v2/live-meetings/{id}/finalize` | Connector | Finalize an ended meeting with transcript text through the existing meeting import path, using stable source `tico-live` and external id equal to the live meeting id. HTTP 200 body is exactly `{"id":"<live-id>","meeting_id":"<imported-meeting-id>","existing":<bool>,"changed":<bool>,"event_id":<meeting-event-id>}`. `meeting_id` is the imported Tico meeting id to use when linking reviewed meeting items. Finalization uses latest corrected text. A later finalize call with a fresh idempotency key returns the same `meeting_id` with `existing:true` and `changed:false`; retrying with the same key/body replays the original saved response. |
| `GET /api/v2/live-meetings/{id}/events?after={event_id}` | Company human, or attached bot | Authenticated SSE replay of persisted meeting events after a monotonically increasing meeting-local id. Recorder's main process must attach its existing Bearer credential. Use `after` or the `Last-Event-ID` header; when a positive `after` is supplied it takes precedence, otherwise `Last-Event-ID` is used. Resume from the last fully processed event id. The server sends keepalives and closes the stream after at most 55 one-second polling cycles; reconnect from the cursor. |
| `GET /api/v2/live-meetings/{id}/turns` | Attached bot | Pull pending concurrent turns and their meeting-scoped transcript/chat context. |
| `POST /api/v2/live-meetings/{id}/turns/{turn_id}/claim` | Attached bot | Claim its pending turn before composing a reply. |

Initial Recorder setup calls connect once with a stable client id, then sends initial transcript
and later transcript segments through the same ordered chunk route. A retry reuses the same client
id, chunk sequence, and idempotency key/body pair. Corrections use a new revision number; they never
replace the history of a previously acknowledged chunk. Event IDs are monotonic per meeting and each
event is committed with the state change it describes. Reconnect from the last fully processed id to
replay without gaps or duplicate effects.

The connect and detail routes return a meeting view with these top-level fields: `id`, `title`,
`state`, `owner_actor`, `seq`, `event_id`, `window_ms`, `cooldown_ms`, `threshold`, `created`,
`started_at`, `ended_at`, `imported_meeting_id`, `humans`, `bots`, `chunks`, `chat`, and `router`.
The `router` object contains `windows`, `turns`, and `bypasses`. Use the route-specific acknowledgments
above for writes; the view is a snapshot, not an acknowledgment receipt.

## Events

SSE frames use `id: <event_id>`, `event: <type>`, and JSON `data:`. These payloads are the v1 event
schemas; nullable values are present as JSON `null` where noted:

| Event | Exact `data` object |
| --- | --- |
| `meeting.state` on connect | `{state:"live",connected_by:<actor>,visibility:"team",window_ms:<integer>}` |
| `meeting.state` on control | `{state:"paused|live|ended",by:<actor>,action:"pause|disconnect|resume|end"}` |
| `meeting.chunk` | `{seq:<integer>,speaker:<string>,start_ms:<integer>,end_ms:<integer>,text:<string>,revision:1}` |
| `meeting.chunk_corrected` | `{seq:<integer>,revision:<integer>,speaker:<string>,start_ms:<integer>,end_ms:<integer>,text:<string>,corrected_by:<actor>}` |
| `meeting.joined` | `{actor:<actor>}` |
| `meeting.bot_joined` | `{bots:[<slug>,...],joined_by:<actor>,rights:"meeting_read_and_reply"}` |
| `meeting.chat` | `{id:<chat-id>,actor:<actor>,role:"human",text:<string>,at_ms:<integer|null>,transcript_seq:<integer|null>}` |
| `meeting.bot_turn` | `{turns:[{id:<turn-id>,bot:<slug>,window_index:<integer|null>,transcript_seq:<integer|null>},...],source_key:<string>}` |
| `meeting.bot_turn_claimed` | `{turn_id:<turn-id>,bot:<slug>}` |
| `meeting.router` named bypass | `{bypass:"named",source_key:<string>,targets:[<slug>,...],skipped:[{bot:<slug>,reason:<string>},...],trace:{decision:"bypassed",reason:"named mention"}}` |
| `meeting.router` window result | `{window_index:<integer>,start_ms:<integer>,end_ms:<integer>,outcome:"route|pass",decision_ms:<integer|null>,trace:<router trace object>}` |
| `meeting.bot_reply` | `{id:<chat-id>,actor:<actor>,text:<string>,at_ms:<integer>,transcript_seq:<integer|null>,turn_id:<turn-id>,window_index:<integer|null>}` |
| `meeting.finalized` | `{id:<live-id>,meeting_id:<imported-meeting-id>,source:"tico-live",external_id:<live-id>}` |

The API error event is `event: expired` with `{}` when the credential is no longer valid. Clients
should reconnect with the same identity after refreshing credentials. For a completed decision,
`meeting.router.trace` contains `selected` (bot slugs), `skipped` (`{bot,reason}` objects), and
`chunk_range` (`[first_seq,last_seq]`); a provider-backed decision also records `model`, `ms`,
`answers` (bot slug to score), and `threshold`. Fail-closed traces carry a `reason` such as
`rehearsal_mode`, `daily_decision_budget`, `decisions service unconfigured`, or `decision_error`
(the latter also includes the exception class in `error`). An interrupted in-flight decision emits
`{window_index,outcome:"pass",trace:{reason:"interrupted decision; not retried"}}` without window
timing fields. PASS never creates a chat message or bot turn.

## Router timing

The server records the routing window start/end, model time, outcome, selected/busy bots and result
trace. Use the 15-second window when Jev is attached; without Jev use a configurable value between
30 and 45 seconds. At most one meeting-route decision is made per window. A 60-second cooldown and
three automated replies per window apply; busy bots are skipped. A speaker addressing a bot by name
and a direct chat message bypass window collection. PASS is persisted as a hidden router outcome.

Automated turns may run concurrently, but each reply is attached to the originating meeting and
transcript position. Router execution never creates external posts or action items. People review
any future action-item proposal separately. In Tico rehearsal mode, routing records a hidden PASS
without calling a decision provider.

## Replay and lifecycle

Replay uses synthetic transcript chunks with the same sequence and router timestamps as production;
no audio is involved. The Recorder can resume SSE by event id and can safely retry a chunk after a
network loss. Pause/disconnect stop *server live sharing* while preserving received text; they do not
stop or delete local device capture, local recording, or local review. Recorder must not stream new
chunks while Tico is paused. Resume is an explicit user/server control that permits uploads to
continue from the last acknowledged sequence. End prevents new chunks and allows one stable import
finalization. The existing `recording_source_refs(source, resource_type, external_id)` uniqueness
remains the finalization idempotency boundary.

Recorder integration requirements remain client-side and are not claimed as implemented by PR #92:
after a connected capture ends, final Send must use only the `meeting_id` returned by finalize, include
the latest corrected transcript, and preserve reviewed-action receipts/idempotency when linking
reviewed meeting items. A capture that never connected to Tico must keep its existing multi-instance
Send flow unchanged. These rules do not add automatic meeting-item creation, external posts, or
unreviewed action items to the server API.

This contract is the PR #92 Recorder handoff for review; it does not claim that Recorder has
integrated these behaviors or that PR #92 has shipped. Any implementation-specific shape change must
be reviewed with Engineering Lead before Recorder ships against it.
