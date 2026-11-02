# Live Meetings API and Recorder event contract

> **Beta**: the Live Meetings API and Recorder work today and may still change. See [Beta features](support.md#beta-features).

This is Recorder wire contract revision 8 for the Tico Live Meetings API v2 server path in PR #92.
It handles transcript text only. Raw audio is never sent to or stored by Tico. Request and response
shapes below are part of the contract; change them only with a reviewed contract update.

## Identity and visibility

- A signed-in human explicitly connects a meeting. A meeting is not team-visible before that action.
- Connect chooses `visibility`: `team` (the default) lets signed-in company humans read the meeting's
  live state, transcript, chat, and router trace. `private` limits that to the connector, people the
  connector adds, and attached bots; to everyone else the meeting answers 404 and is left out of the
  list. The connector can add or remove people, or switch visibility, while the meeting is live or paused. Only the connector can write transcript chunks, pause/resume, disconnect, end, or
  finalize; another human explicitly joins before chatting. Joining a team meeting does not grant
  access if it becomes private: only the connector and explicitly added people retain human access.
- In a team meeting, a joined human may attach active org-chart bots they are allowed to see and have
  Write access to. In a private meeting, only the connector can attach bots. Switching to private
  removes bot attachments made by other people; the connector can attach those bots again explicitly.
  Attachment creates a meeting-scoped read grant; it does not change the bot's standing access or
  grant access to other meetings. Runner delivery is checked against the attaching human's current
  Write access and contact policy, so keeper transport does not create bot-to-bot contact. Any joined
  human can remove an attached bot from this meeting; removal revokes that meeting-only grant and
  settles outstanding turns without changing ordinary bot permissions.
- Meeting-derived conversations, messages and run artifacts retain the meeting's current access
  restriction, including on retries and after a bot is removed. Company-owner inspection does not
  override private meeting membership. An already downloaded copy cannot be retracted.
- Private meeting executions can publish their scoped reply and retain files tied to that run.
  File writes are limited to uploads/imports in the turn's own conversation, with no task/bot scope
  override, link registration or metadata edits. They cannot publish ordinary messages, tasks,
  comments or company status from that context.
  This server-side restriction does not change ordinary team-meeting executions.
- Authorized meeting members can read a generated bot room's meeting-scoped prompt and reply
  without broader bot Read access. This grants no posting or goal-setting authority in that room:
  people use live meeting Chat, and bots name their claimed turn when replying there.
- A bot acting for a person must also be explicitly attached before accessing that person's private
  meeting. Both memberships remain required on retries and event streams. Connect a private meeting
  directly before attaching a bot; delegated team connections keep the ordinary team behavior.
- An attached bot can read this meeting's transcript and chat, and can post a reply attributed to
  itself. It cannot control the meeting or change its membership.

## HTTP routes

All routes are under `/api/v2/live-meetings` and require the same Tico session or token used by the
signed-in company client. For an authenticated SSE fetch in Recorder's main process, send the human
credential in `Authorization: Bearer <credential>` just as for the other API calls. Do not put the
credential in the URL. The Tico instance is the tenant boundary: a Recorder connected to one
instance uses that user's credential for this instance only. The connector identity is stored as
`owner_actor` and owns transcript input, lifecycle control and finalization. Joined humans can attach
only bots they can see and contact under the existing org-chart Write permission, and only the
connector can do so in a private meeting.

Every write, including `DELETE` bot removal, uses the normal Tico `Idempotency-Key` header (1–200 characters). Repeating the same
operation, key, and body returns the saved response; reusing a key for different content returns
HTTP 409 with `error.code = "idempotency_conflict"`. Errors use
`{"error":{"code":"...","detail":"...","retryable":false,...}}`; endpoint-specific cursor
fields are listed below.

| Method and path | Access | Purpose |
| --- | --- | --- |
| `POST /api/v2/live-meetings` | Human | Explicitly connect; body `{title,client_id?,client?,fallback_window_seconds?,threshold?,reply_cap?,cooldown_seconds?,visibility?}`. `visibility` is `team` (default) or `private`. `reply_cap` is 1–20 (default 5); `cooldown_seconds` is 0–3600 (default 60); `meeting_model` is `""` for the bot's own model or a model the company can run (default: the company's meeting model). Returns the meeting view described below. Fallback window is 30–45 seconds; repeating a `client_id` for that person returns the same meeting. |
| `GET /api/v2/live-meetings` | Human | List live and paused meetings this person can see, newest first; ended meetings are excluded. |
| `GET /api/v2/live-meetings/bot-candidates` | Human | Return `{"bots":[{"slug":"ops","name":"Operations","description":"...","team":"..."}]}`. `team` may be null. Only active bots this human can see and contact with Write access are included; `bots` may be empty. |
| `GET /api/v2/live-meetings/{id}` | Company human, or attached bot | Read state, transcript chunks, chat, attached bots, transcript-window traces, chat-route traces, named-bypass traces, and turn outcomes. |
| `POST /api/v2/live-meetings/{id}/chunks` | Connector | Append `{chunks:[{seq,speaker,start_ms,end_ms,text}]}` (1–500 chunks). Seq starts at 1 and is contiguous. HTTP 200 body is exactly `{"id":"<live-id>","state":"live|paused|ended","seq":<highest-seq>,"accepted":[<seq>,...],"event_id":<meeting-event-id>}`. Identical duplicate seq is acknowledged without a new event; a different payload for an existing seq returns `409` with `error.code = "sequence_conflict"`; a gap returns `409` with `error.code = "sequence_gap"` and `error.expected_seq`. New chunks are accepted only in `live`; an already-stored identical retry may still be acknowledged while paused or ended. |
| `POST /api/v2/live-meetings/{id}/chunks/{seq}/corrections` | Connector | Append `{revision,speaker,start_ms,end_ms,text}`. Revision 2 follows initial revision 1. HTTP 200 body is exactly `{"id":"<live-id>","seq":<seq>,"revision":<revision>,"replayed":<bool>,"event_id":<meeting-event-id>}`. Repeating the same revision and content returns `replayed:true` without a new event; changed content at an existing revision returns `409` with `error.code = "revision_conflict"`; a gap returns `409` with `error.code = "revision_gap"` and `error.expected_revision`. Corrections are accepted while live or paused. Empty `text` clears a chunk the final transcript has no words for; cleared chunks leave the transcript, bot context and finalization. The latest revision becomes the transcript used for display and finalization; older versions remain replayable. |
| `POST /api/v2/live-meetings/{id}/join` | Human who can see it | Join the connected meeting. Joined people can chat. |
| `POST /api/v2/live-meetings/{id}/access` | Connector | `{visibility?,add?:[person],remove?:[person]}` with person ids or `human:` actors. Adding lets a person see and join a private meeting; removing also takes them out of the joined list. Returns the meeting view, whose `visibility` and `access:[{actor,name}]` show the result. |
| `POST /api/v2/live-meetings/{id}/chat` | Joined human or attached bot | Append `{text,at_ms?,transcript_seq?,turn_id?}`. The response is `{"message":<saved chat object> or null,"event_id":<latest meeting-event-id>}` and includes `"outcome":"pass"` for a bot PASS. A live unnamed human message receives one multi-bot decision using the existing decisions service; a named-bot mention bypasses that decision and directly targets named attachments. Chat sent while paused is saved but not dispatched. A bot using this API must claim its turn first and name it on reply. For bot text exactly `PASS`, optionally followed by `.` or `!`, HTTP 200 is exactly `{"message":null,"event_id":<meeting-event-id>,"outcome":"pass"}`; it settles the claimed turn without chat text. Assigned bots using the normal Tico runner receive a private, meeting-scoped job; the runner lease claims the turn and its completed response settles it. A transcript reference links chat/replies to the captured words. |
| `POST /api/v2/live-meetings/{id}/bots` | Joined human with Write access to each bot; connector only when private | Attach `{bots:[org_chart_slug,...]}`. Each bot receives meeting-only read/reply access. |
| `DELETE /api/v2/live-meetings/{id}/bots/{bot}` | Joined human | Send `{}` with an `Idempotency-Key` to remove one attached bot. HTTP 200 is `{"id":"<live-id>","bot":"<slug>","removed":<bool>,"cancelled_turns":[<turn-id>,...],"event_id":<meeting-event-id>}`. Repeating the same key/body returns the saved result. Unclaimed/claimed turns are marked skipped as `bot_detached`; queued jobs are cancelled; late replies cannot post. Removal is meeting-local and idempotent when the bot is already absent. |
| `POST /api/v2/live-meetings/{id}/control` | Connector | `{action:"pause"|"disconnect"|"resume"|"end"}`. Pause and disconnect are server-side live-sharing controls: both set Tico state to `paused`, preserve text/chat, suppress new routing, and reject new chunk writes. They do not stop Recorder's local device capture, local recording, or review. Recorder must stop uploading chunks while paused/disconnected; local capture/review behavior remains under Recorder and the person. Reconnect with the same `client_id`; only an explicit `resume` re-enables live chunk writes. End is terminal. |
| `POST /api/v2/live-meetings/{id}/finalize` | Connector | Finalize an ended meeting with transcript text through the existing meeting import path, using stable source `tico-live` and external id equal to the live meeting id. The transcript enters the connector's `review=pending` person queue and is not automatically shared. HTTP 200 body is exactly `{"id":"<live-id>","meeting_id":"<imported-meeting-id>","existing":<bool>,"changed":<bool>,"event_id":<meeting-event-id>}`. `meeting_id` is the imported Tico meeting id to use when linking reviewed meeting items. Finalization uses latest corrected text. A later finalize call with a fresh idempotency key returns the same `meeting_id` with `existing:true` and `changed:false`; retrying with the same key/body replays the original saved response. |
| `GET /api/v2/live-meetings/{id}/events?after={event_id}` | Company human, or attached bot | Authenticated SSE replay of persisted meeting events after a monotonically increasing meeting-local id. Recorder's main process must attach its existing Bearer credential. Use `after` or the `Last-Event-ID` header; when a positive `after` is supplied it takes precedence, otherwise `Last-Event-ID` is used. Resume from the last fully processed event id. The server sends keepalives and closes the stream after at most 55 one-second polling cycles; reconnect from the cursor. |
| `GET /api/v2/live-meetings/{id}/turns` | Attached bot | Pull pending turns and their meeting-scoped transcript/chat context for clients that execute meeting turns directly. |
| `POST /api/v2/live-meetings/{id}/turns/{turn_id}/claim` | Turn's bot | Claim a pending turn before composing a reply through the meeting API. HTTP 200 is `{"id":"<turn-id>","status":"claimed"}`; current meeting access is checked before a fresh claim or cached receipt (403 for an unattached team-meeting bot, 404 for an unreadable private meeting). If the still-readable turn ended or lost its attaching human's Write authority before claim, it is durably skipped and returns `{"id":"<turn-id>","status":"skipped","reason":"<reason>"}`. |

Initial Recorder setup calls connect once with a stable client id, then sends initial transcript
and later transcript segments through the same ordered chunk route. A retry reuses the same client
id, chunk sequence, and idempotency key/body pair. Corrections use a new revision number; they never
replace the history of a previously acknowledged chunk. Event IDs are monotonic per meeting and each
event is committed with the state change it describes. Reconnect from the last fully processed id to
replay without gaps or duplicate effects.

The connect and detail routes return a meeting view with these top-level fields: `id`, `title`,
`state`, `owner_actor`, `client`, `seq`, `event_id`, `window_ms`, `cooldown_ms`, `reply_cap`, `threshold`,
`meeting_model` (the meeting's own choice as stored: `""` for the bot's own model, null to follow the company's),
`meeting_model_effective` (what replies are set to run on now: the meeting's own choice, else the company's meeting
model; `""` is the bot's own; not specific to a computer, see `meeting.model` for a fallback), `created_at`,
`started_at`, `ended_at`, `imported_meeting_id`, `humans`, `bots`, `chunks`, `chat`, `notices`, and `router`.
`notices` lists the meeting's `meeting.bot_turn_failed`, `_waiting`, `_deferred` and `_retried` events, oldest
first, each as `{event_id,kind:"failed"|"waiting"|"deferred"|"retried",created_at,...payload}` (payloads below), so a
client can say in the chat why a bot has not answered yet without replaying the event stream.
The `router` object contains `windows`, `chat_routes`, `turns`, and `bypasses`. Each `chat_routes` item
has `chat_id`, `status`, `outcome`, `started_at`, `finished_at`, and `trace`. Use the route-specific acknowledgments
above for writes; the view is a snapshot, not an acknowledgment receipt.

Chunk, correction, resume and human-chat writes are acknowledged once stored; routing (window and chat
decisions, which call a model) runs after the response, one meeting at a time, so a slow decision never
delays an upload and a window is never decided twice at once. A window left undecided by a restart is
picked up by the scheduler within about ten seconds for meetings live in the last two hours. Read the
outcome from the event stream or the `router` view.

A window also closes by the wall clock: when no chunk has arrived for the window's length plus 2 seconds since
the last one, the server routes the open window without waiting for the next words, so a question followed by
silence is answered. A late chunk, one whose `start_ms` falls inside a window already closed this way, is routed by
the next window that is open (opened if need be), once: its window's `chunk_range` names it, and no earlier window
takes it again. Corrections (`/chunks/{seq}/corrections`) never route anything.

`client` is optional on connect: omit it (or send null) for an unnamed connector, or use
`"tico-recorder"`. Any other value is a validation error. The meeting view always includes
`client` as `"tico-recorder"` or null. It is fixed at creation: reconnecting with an existing
`client_id` returns that meeting unchanged. Finalize carries it into the filed meeting's metadata.
Live now and the filed meeting show "From Tico Recorder" for that client. Existing meetings
have null provenance. Visibility still defaults to `team`; event schemas and all other routes
are unchanged.

The stable UI link for Open in Tico is `<instance-base>/#/meetings?live=<id>` (URL-encode the
meeting id). It selects the meeting directly, including an ended meeting. The link grants no
access: normal sign-in and meeting visibility checks still apply. The filed meeting link remains
`<instance-base>/#/meetings?meeting=<imported_meeting_id>`.

## Events

SSE frames use `id: <event_id>`, `event: <type>`, and JSON `data:`. These payloads are the v1 event
schemas; nullable values are present as JSON `null` where noted:

| Event | Exact `data` object |
| --- | --- |
| `meeting.state` on connect | `{state:"live",connected_by:<actor>,visibility:"team"|"private",window_ms:<integer>,cooldown_ms:<integer>,reply_cap:<integer>}` |
| `meeting.state` on control | `{state:"paused|live|ended",by:<actor>,action:"pause|disconnect|resume|end"}` |
| `meeting.chunk` | `{seq:<integer>,speaker:<string>,start_ms:<integer>,end_ms:<integer>,text:<string>,revision:1}` |
| `meeting.chunk_corrected` | `{seq:<integer>,revision:<integer>,speaker:<string>,start_ms:<integer>,end_ms:<integer>,text:<string>,corrected_by:<actor>}` |
| `meeting.joined` | `{actor:<actor>}` |
| `meeting.bot_joined` | `{bots:[<slug>,...],joined_by:<actor>,rights:"meeting_read_and_reply"}` |
| `meeting.bot_left` | `{bot:<slug>,removed_by:<actor>,cancelled_turns:[<turn-id>,...],reason:"meeting_only_access_revoked"}` |
| `meeting.chat` | `{id:<chat-id>,actor:<actor>,role:"human",text:<string>,at_ms:<integer|null>,transcript_seq:<integer|null>}` |
| `meeting.bot_turn` | `{turns:[{id:<turn-id>,bot:<slug>,window_index:<integer|null>,transcript_seq:<integer|null>},...],source_key:<string>}` |
| `meeting.bot_turn_claimed` | `{turn_id:<turn-id>,bot:<slug>}` |
| `meeting.bot_turn_failed` | `{turn_id:<turn-id>,bot:<slug>,reason:"private_checkout",detail:<string>}`: once per private meeting and bot, when the bot's computer could make no private copy of its repository; that turn and later ones that fail alike end without an answer (also `meeting.bot_turn_skipped`, reason `bot_execution_private_checkout`) instead of being retried. |
| `meeting.bot_turn_skipped` | `{turn_id:<turn-id>,bot:<slug>,reason:<string>}`; `steered_into_turn` means the bot took the turn in as a follow-up to the reply it was giving, which answers it. |
| `meeting.bot_turn_waiting` | `{turn_id:<turn-id>,bot:<slug>,reason:"runner_update",detail:<string>}`: once per private meeting and bot, when no computer running the bot can take a private meeting's turn yet; the turns wait for its update (see Router timing, private meetings). |
| `meeting.bot_turn_deferred` | `{turn_id:<turn-id>,bot:<slug>,reason:"replying_in_meeting"|"busy",behind:<turn-id|null>,source_key:<string>}`: a bot named while busy keeps its turn; it runs after the current work (see Router timing). |
| `meeting.bot_turn_retried` | `{turn_id:<turn-id>,bot:<slug>,reason:"empty_bot_reply",retry:1|2,model:<meeting model|null>}`: an empty reply is asked again in the same conversation as a fresh run: once on the model that gave it (`model`), and, when that was a meeting model, once more on the bot's own (`model:null`). After that an empty reply is a hidden PASS (`empty_bot_reply`). |
| `meeting.model` | `{model:<first choice>,bot:<slug>,turn_id:<turn-id|null>,fallback:"openrouter"|"bot_model",ran:<model>,reason:<first reason>,steps:[{model,reason},...]}`: the meeting model could not run on the bot's computer and the reply ran on `ran`; `steps` lists each model tried that could not. Said once per turn: a claim of the same turn that falls back the same way adds none. |
| `meeting.router` named bypass | `{bypass:"named",source_key:<string>,targets:[<slug>,...],skipped:[{bot:<slug>,reason:<string>},...],trace:{decision:"bypassed",reason:"named mention"}}` |
| `meeting.router` chat route | `{source_kind:"chat",source_key:"chat:<chat-id>",chat_id:<chat-id>,outcome:"route|pass",decision_ms:<integer|null>,trace:<router trace object>}` |
| `meeting.router` window result | `{window_index:<integer>,start_ms:<integer>,end_ms:<integer>,outcome:"route|pass",decision_ms:<integer|null>,trace:<router trace object>}`. Empty elapsed windows record `trace.reason:"no_transcript_text"` with empty `selected`, `skipped`, and `chunk_range`; an ended partial final window records `trace.reason:"meeting_ended_final_window"` and never dispatches a new turn. |
| `meeting.router` bot PASS/skip | `{turn_id:<turn-id>,bot:<slug>,window_index:<integer|null>,outcome:"pass",trace:{reason:<reason>,source_key:<string>}}`; `reason` is `bot_pass`, `empty_bot_reply`, a terminal meeting reason, or a non-requeued runner outcome. |
| `meeting.router` bot reply | `{turn_id:<turn-id>,bot:<slug>,window_index:<integer|null>,outcome:"reply",trace:{reason:"bot_replied",source_key:<string>}}` |
| `meeting.bot_reply` | `{id:<chat-id>,actor:<actor>,text:<string>,at_ms:<integer>,transcript_seq:<integer|null>,turn_id:<turn-id>,window_index:<integer|null>}` |
| `meeting.finalized` | `{id:<live-id>,meeting_id:<imported-meeting-id>,source:"tico-live",external_id:<live-id>}` |

The API error event is `event: expired` with `{}` when the credential is no longer valid. Clients
should reconnect with the same identity after refreshing credentials. For a completed decision,
`meeting.router.trace` contains `selected` (bot slugs), `skipped` (`{bot,reason}` objects), and
`chunk_range` (`[first_seq,last_seq]` for transcript windows); a provider-backed decision also records `model`, `ms`,
`answers` (bot slug to score), `decision_answers` (the checked answer objects), `decision_input`
(the exact bounded context passed to the decision service), and `threshold`. Chat decision inputs
include recent chat, prior transcript context, attached bot descriptions and recent bot messages,
including recent question/answer pairs. These traces are persisted for replay and do not trigger
another decision on a retry. Fail-closed traces carry a `reason` such as
`rehearsal_mode`, `decisions service unconfigured`, or `decision_error`
(the latter also includes the exception class in `error`, a `code` such as `unavailable`, `timeout` or
`unreadable`, `retryable`, and the provider HTTP `status` when known; never the provider's error text). A bot response of exactly `PASS`
(optionally followed by `.` or `!`) marks its existing turn skipped with reason `bot_pass`, emits the
router trace, and creates no Chat message. Ending a meeting marks open turns skipped and suppresses
their queued jobs. An interrupted in-flight decision emits
`{window_index,outcome:"pass",trace:{reason:"interrupted decision; not retried",chunk_range:[first_seq,last_seq]}}`
without window timing fields; its chunks are passed, not routed again by a later window. A router PASS creates no bot turn; a bot PASS settles its existing turn without
creating visible chat.

For transcript-window and chat decisions, `decision_input` has this exact structure (arrays may be
empty; text is clipped to 800 characters per entry):

```json
{
  "meeting_id": "<live-id>",
  "meeting_title": "<title>",
  "source": {"kind": "transcript|chat", "key": "window:<index>|chat:<chat-id>"},
  "window_index": 0,
  "transcript_before": [{"seq": 1, "speaker": "<speaker>", "start_ms": 0, "end_ms": 1000, "text": "<text>"}],
  "transcript": [{"seq": 2, "speaker": "<speaker>", "start_ms": 1000, "end_ms": 2000, "text": "<text>"}],
  "recent_chat": [{"id": "<chat-id>", "actor": "human:ana", "role": "human", "text": "<text>", "at_ms": null, "transcript_seq": null}],
  "answered_questions": [{"question": "<human question>", "question_id": "<chat-id>", "answer": "<bot reply>", "answered_by": "bot:ops"}],
  "bots": [{"slug": "ops", "name": "Operations", "description": "<description>", "last_messages": [{"id": "<chat-id>", "text": "<reply>", "created_at": "<timestamp>"}]}]
}
```

For a chat decision `window_index` is JSON `null`, `source.kind` is `chat`, and `source.key` is
`chat:<chat-id>`. A chat route trace records `source_key`, `selected`, `skipped`, `decision_input`,
and the provider result fields when applicable; unlike a transcript-window trace it has no
`chunk_range`. `router.chat_routes` records `{chat_id,status,outcome,trace,started,finished}` for replay. A paused
chat route records `outcome:"pass"`, `trace.reason:"meeting_paused_before_chat_routing"`, and
`decision_input:null`; it never calls a decision provider or creates turns.

## Router timing

The server records the routing window start/end, model time, outcome, selected/busy bots and result
trace. Every elapsed window without transcript text gets a hidden PASS trace with reason
`no_transcript_text`; it does not call the decisions service. Use the 15-second window when Jev is attached; without Jev use a configurable value between
30 and 45 seconds. At most one meeting-route decision is made per window. Each meeting stores its
reply cap (1–20, default 5) and cooldown (0–3600 seconds, default 60). Live meetings do not count against
the daily decision budget; the monthly decision spend cap still stops them; bots with any pending/claimed
live-meeting turn in that meeting or an active ordinary runner job are skipped as busy, and a bot that
replied within the cooldown is skipped as `cooldown`. A speaker addressing a bot by name bypasses the
decision provider, the busy check and the cooldown: the reply cap still applies, and the mention is
never dropped. If the bot is replying in this meeting, the mention goes into that reply's conversation:
a runtime that takes follow-ups mid-turn (`/attempts/{id}/inputs`) answers it in the same reply and the
turn is skipped as `steered_into_turn`; otherwise it runs right after. If the bot is busy with other
work it runs after that. Either way `meeting.bot_turn_deferred` records it. An ordinary human chat message gets one
separate decision using the same service and bounded recent context; paused chat is never dispatched.
PASS is persisted as a hidden router outcome.

Meeting model: a live-meeting reply runs as a normal bot turn on the meeting's model at low effort, with the
bot's instructions, memory and hub tools. The model is the meeting's own `meeting_model` when the connect
body sets one, else the company's meeting model (Settings > Computers > Meeting model). That default is
`tico-meeting-1.0` while the Tico AI Gateway is on and the bot's own model otherwise; `""` means the bot's own
model. Other bot work is unaffected. If the meeting model cannot run on the bot's computer (a Mac computer
cannot reach the Gateway's meeting models), a Gateway meeting model falls back to `mercury-3` (OpenRouter
`inception/mercury-3`, low effort, on pi) when the company has OpenRouter on; if that cannot run either,
the reply uses the bot's own model. `meeting.model` records each step.

Each bot has one conversation per meeting, kept for the meeting's turns while the bot stays attached, and
every turn runs on that conversation's own thread whatever the bot's session setting. So a bot's turns in
one meeting continue each other, and nothing said in a meeting (private or team) reaches the bot's other
meetings or its ordinary chats through the harness thread. Removing and re-adding a bot starts a new one.

A private meeting's bot turns change nothing shared; they read as any turn does (memory, instructions,
knowledge, docs, hub reads). The meeting's owner shares its notes and action items after the meeting, by
review. Enforced, not left to the bot, on any harness:

- **Hub writes.** Every write a private meeting's turn makes through the hub API or `/api/v2/mcp` is refused
  with HTTP 403 `privacy`, detail "This is a private meeting: nothing from it is saved or shared until its
  owner reviews it." (docs, pages, tasks, messages to other rooms, mail, status, approvals, settings). What it
  may still write is one list, `task_privacy.PRIVATE_MEETING_WRITES`: reads over POST (`sql`,
  `tasks/dry-run`), the MCP door, run files (they keep the run's provenance, so only the meeting's readers see
  them), a GitHub token (read-only for it), and its own meeting's `chat` and `turns/{turn_id}/claim`. The
  runner's own lease, event and result calls are not affected.
- **The bot's repository.** Its turns run in their own lane, `private:<conversation id>`, which the runner
  gives a worktree of the bot's repository kept for that meeting alone (`sessions/bot-<slug>/private-<id>`,
  branch `tico-session/private-<id>` with no upstream). The bot keeps what it changed there for the rest of
  the meeting; nothing in it is committed for the bot, pushed, published or reported, and the bot's checkout
  is never used. Its GitHub tokens are asked for with the turn's own token, so they are read-only. The
  runner deletes the worktree and its branch, unsaved work and all, within ten minutes of the meeting
  ending (`POST /api/v2/runners/me/session-tasks` answers `done` for `private-<id>`). A checkout with no
  upstream (a repository made on that computer, no remote yet) is fine: the copy starts from its `HEAD`. If
  the worktree cannot be made (the bot has no checkout, git refuses), the turn never runs in the bot's
  checkout: the runner tags the failure `private_checkout`, the server ends the turn rather than hand it
  back or resume it, and the meeting gets one `meeting.bot_turn_failed` notice.
- **Older runners.** Only a runner that advertises the `private_lanes_v1` capability in its heartbeat
  (runner-rs `lanes::CAPABILITIES`) is handed a private meeting's turn; any other would run it in the bot's
  checkout with write tokens. The turn stays queued for the bot's next updated runner, Health says
  "<computer> needs a Tico update before it can join private meetings" (reason `private_update`), and when
  no computer running the bot has the capability, the meeting gets one `meeting.bot_turn_waiting` event per
  bot. Team meetings' turns go to any runner.
- **The prompt** says so: the bot cannot save or share anything from the meeting, and when asked to, it says
  the owner can share the meeting's notes and action items after the meeting. Team meetings get no such line.

Team meetings are unchanged.

Each selected bot gets a durable normal Tico runner job containing only that meeting's title,
transcript (up to 20 latest chunks through the routed sequence), and recent chat (up to 12 messages);
source texts are clipped to 800 characters per entry. The runner lease claims the meeting turn and
its completed response settles that same turn. Automated turns may run concurrently, but each reply
is attached to the originating meeting and transcript position. Router execution never creates
external posts or action items. People review any future action-item proposal separately. In Tico
rehearsal mode, routing records a hidden PASS without calling a decision provider.

## Replay and lifecycle

Replay uses synthetic transcript chunks with the same sequence and router timestamps as production;
no audio is involved. The Recorder can resume SSE by event id and can safely retry a chunk after a
network loss. Pause/disconnect stop *server live sharing* while preserving received text; they do not
stop or delete local device capture, local recording, or local review. Recorder must not stream new
chunks while Tico is paused. Resume is an explicit user/server control that permits uploads to
continue from the last acknowledged sequence. End prevents new chunks, marks pending or claimed bot
turns as skipped, cancels queued runner jobs, and records the unprocessed final window as a hidden
PASS instead of creating terminal work. A decision already in progress when End arrives is discarded
as a hidden PASS and cannot dispatch new turns. It then allows one stable import finalization. The existing
`recording_source_refs(source, resource_type, external_id)` uniqueness
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
