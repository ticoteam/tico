# Live Meetings API and Recorder event contract

This is the proposed v1 contract for the Tico Live Meetings server path and the Recorder client.
It handles transcript text only. Raw audio is never sent to or stored by Tico.

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
signed-in company client. The Tico instance is the tenant boundary: a Recorder connected to one
instance uses that user's credential for this instance only. The connector identity is stored as
`owner_actor` and owns transcript input, lifecycle control and finalization. Joined humans can attach
only bots visible to them under the existing org-chart permissions.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /api/v2/live-meetings` | Human | Explicitly connect; body `{title,client_id?,fallback_window_seconds?,threshold?}`. Returns `{id,state,seq,event_id,router}`. Fallback window is 30–45 seconds; repeating a `client_id` for that person returns the same meeting. |
| `GET /api/v2/live-meetings` | Human | List live and paused meetings newest first; ended meetings are excluded. |
| `GET /api/v2/live-meetings/bot-candidates` | Human | Return active org-chart bots this person may see, for the attached-bot picker. |
| `GET /api/v2/live-meetings/{id}` | Company human, or attached bot | Read state, transcript chunks, chat, attached bots, window traces, named-bypass traces, and pending/replied turns. |
| `POST /api/v2/live-meetings/{id}/chunks` | Connector | Append `{chunks:[{seq,speaker,start_ms,end_ms,text}]}`. Seq starts at 1 and is contiguous. Identical duplicate seq is acknowledged; a different payload for an existing seq returns `409`; a gap returns `409` with `expected_seq`. |
| `POST /api/v2/live-meetings/{id}/chunks/{seq}/corrections` | Connector | Append `{revision,speaker,start_ms,end_ms,text}`. Revision 2 follows initial revision 1. A repeated identical revision is acknowledged; changed content at an existing revision returns `409`; a gap returns `409` with `expected_revision`. The latest revision becomes the transcript used for display and finalization; older versions remain replayable. |
| `POST /api/v2/live-meetings/{id}/join` | Human | Join the connected meeting. Joined people can chat; all company humans can read after connect. |
| `POST /api/v2/live-meetings/{id}/chat` | Joined human or attached bot | Append `{text,at_ms?,transcript_seq?,turn_id?}`. An attached bot must claim its turn first and name it on reply. A transcript reference links chat/replies to the captured words. Named routing is suppressed during pause. |
| `POST /api/v2/live-meetings/{id}/bots` | Joined human | Attach `{bots:[org_chart_slug,...]}`. Each bot receives meeting-only read/reply access. |
| `POST /api/v2/live-meetings/{id}/control` | Connector | `{action:"pause"|"disconnect"|"resume"|"end"}`. Pause/disconnect preserve text and stop new capture/routing. A Recorder reconnects with the same `client_id` and the connector explicitly resumes. End is terminal and idempotent. |
| `POST /api/v2/live-meetings/{id}/finalize` | Connector | Materialize the final transcript through the existing meeting import path using stable source `tico-live` and external id equal to the live meeting id. Repeated finalization returns the same imported meeting. |
| `GET /api/v2/live-meetings/{id}/events?after={event_id}` | Company human, or attached bot | SSE replay of persisted meeting events after a monotonically increasing meeting-local id. `Last-Event-ID` is equivalent to `after`. |
| `GET /api/v2/live-meetings/{id}/turns` | Attached bot | Pull pending concurrent turns and their meeting-scoped transcript/chat context. |
| `POST /api/v2/live-meetings/{id}/turns/{turn_id}/claim` | Attached bot | Claim its pending turn before composing a reply. |

Initial Recorder setup calls connect once with a stable client id, then sends initial transcript
and later transcript segments through the same ordered chunk route. A retry reuses the same client
id and chunk sequence. Corrections use a new revision number; they never replace the history of a
previously acknowledged chunk. Mutations honor Tico's `Idempotency-Key`. Event IDs are monotonic per meeting and each event is
committed with the state change it describes. Reconnect from the last received id to replay without
gaps or duplicate effects.

## Events

SSE frames use `id: <event_id>`, `event: <type>`, and JSON `data:`. The durable event types are:

- `meeting.state`: `live`, `paused`, or `ended`
- `meeting.chunk`: one acknowledged transcript segment and its sequence
- `meeting.chunk_corrected`: new transcript revision for a sequence; prior versions remain stored
- `meeting.joined`: a human explicitly joined and may now chat
- `meeting.chat`: a human or bot message, optionally linked to a transcript sequence/time
- `meeting.bot_joined`: one or more attached bot slugs
- `meeting.bot_turn`: persisted concurrent pending turns for attached bots
- `meeting.bot_turn_claimed`: an attached bot claimed its turn before replying
- `meeting.router`: window start/end, decision timing and outcome; PASS is trace-only, never chat
- `meeting.bot_reply`: bot text with the triggering transcript sequence/window reference
- `meeting.finalized`: stable imported meeting id

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
network loss. Pausing stops routing while preserving received text; resume continues from the last
sequence; end prevents further chunks and allows one stable import finalization. The existing
`recording_source_refs(source, resource_type, external_id)` uniqueness remains the finalization
idempotency boundary.

This contract is a draft for Recorder integration. Any implementation-specific shape change must be
reviewed with Engineering Lead before Recorder ships against it.
