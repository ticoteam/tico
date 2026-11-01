# Tico Meet

A call happens in a thread. People talk and share their screen, a live transcript is kept while it runs, and bots
that were attached listen and reply in the thread. When the call ends, the transcript is posted to the thread.

Status on this branch: the call API, the CLI, the MCP tools and the OpenAPI operations are built, with the media
side behind a port so it can be tested with fakes. A call cannot start on a real install yet (see Not built).

## How a call works

1. **Start.** Someone who can write to the thread starts a call. They are its connector. Tico creates the call and
   its live meeting, then has the recorder join the media session as a hidden server participant that hears the
   call and never publishes.
2. **Join.** People who can read the thread join and get a token good for 5 minutes. It grants microphone and
   screen share, never a camera and no data messages. A joined client refreshes its token with
   `POST /api/v2/calls/{call_id}/token` before it expires.
3. **Transcript.** The recorder sends each speaker's audio to the Gateway's speech-to-text (the Recorder's model,
   `elevenlabs/scribe-v2`) in clips of up to 8 seconds, or sooner once the speaker is quiet for 3 seconds. The text
   becomes the call's live meeting chunks. Audio is held in memory until it is sent and is never stored.
4. **Bots.** A person who can write to the thread and has joined the call attaches bots (up to 10). A bot must be one
   that person can write to. Attached bots get turns on the call's speech and reply in the thread, as they do in a
   live meeting. Their replies are messages in the thread.
5. **Access.** When someone loses read access to the thread, the server removes them from the media session through
   LiveKit's server API and refuses their token refresh. Bots that lose write access are detached the same way. A
   background sweep does this on each pass.
6. **End.** The connector ends the call. Speech still buffered is sent, the media session is closed, and the
   transcript is posted to the thread once as a `Call transcript` message marked `restricted`, so only people who can
   read the thread see it. Action items are not drafted yet. Tasks are made by hand.
7. **Restarts.** A Tico restart brings the recorder back for each live call from the database. If the media server
   stops, the call ends with `end_reason: media_lost`, and the transcript so far is kept.

## Who may do what

| Action | Who |
| --- | --- |
| See a call, list calls, join, refresh a token | People who can read the thread |
| Start a call, attach or detach bots | People who can write to the thread (attach also needs the person to have joined) |
| Leave | The person who joined |
| End | The connector |
| Anything | Bots are refused. BotOps acts as the person it works for |

Errors: `meet_unavailable` (503) when Tico Meet is not set up or the recorder cannot start, `call_in_progress` (409)
for a second live call in a thread, `call_ended` (409), `not_joined` (409) when a token is asked for without joining,
and `call_access_removed` (403) after removal. A call the caller cannot read is 404.

## API

| Method and path | What it does |
| --- | --- |
| `GET /api/v2/threads/{thread_id}/calls` | The thread's calls, newest first. `limit` (1-50) and `before` page through them |
| `POST /api/v2/threads/{thread_id}/calls` | Start a call. Body: `title` (optional) |
| `GET /api/v2/calls/{call_id}` | One call: state, connector, people, bots, how it ended |
| `POST /api/v2/calls/{call_id}/join` | Join. Returns `url`, `token` and `expires_in` (300) |
| `POST /api/v2/calls/{call_id}/token` | A fresh token while joined |
| `POST /api/v2/calls/{call_id}/leave` | Leave |
| `POST /api/v2/calls/{call_id}/end` | End (the connector) |
| `POST /api/v2/calls/{call_id}/bots` | Attach bots. Body: `bots`, 1 to 10 slugs |
| `DELETE /api/v2/calls/{call_id}/bots/{bot}` | Detach a bot |

The same operations are `hub call list|start|show|join|token|leave|end` and `hub call bot add|remove` in the `hub`
CLI, and `hub_call_list`, `hub_call_show`, `hub_call_start`, `hub_call_join`, `hub_call_token`, `hub_call_leave`,
`hub_call_end`, `hub_call_bot_add` and `hub_call_bot_remove` over MCP. The OpenAPI operations are `listCalls`,
`startCall`, `getCall`, `joinCall`, `refreshCallToken`, `leaveCall`, `endCall`, `attachCallBots` and
`detachCallBot`.

Changes are written to the change log on the `threads` topic: `call.started`, `call.joined`, `call.left`,
`call.ended`, `call.bot_attached`, `call.bot_detached` and `call.participant_removed`. Each has an audit row. The live
event stream does not send the `threads` topic yet, so these reach clients once the threads event view lands.

## Self-hosting

The `meet` compose profile runs LiveKit (Apache-2.0) on the cell host. It adds no paid cloud resource. Set these in
`.env` (see `.env.example`):

- `TICO_MEET_URL`: the address clients connect to, `wss://` on the meet hostname.
- `TICO_MEET_DOMAIN`: the meet hostname. Its DNS points at this host.
- `TICO_MEET_API_KEY` and `TICO_MEET_API_SECRET`: LiveKit's key pair. They go to the LiveKit service and the server only.
- `TICO_MEET_CERT_DIR`: a folder holding `fullchain.pem` and `privkey.pem` for `TICO_MEET_DOMAIN`. TURN over TLS needs
  them, and the service does not start without them.
- `TICO_MEET_IMAGE`: optional. The default tag is not yet checked against the registry, so pin a digest before you
  enable the profile.

`TICO_MEET_INTERNAL_URL` defaults to `http://livekit:7880`, the server's own address for LiveKit's server API.

Ports:

- `3478/udp` and `5349/tcp` for TURN, and TURN over TLS on its own hostname and port. Caddy keeps 443, so TURN never
  competes with it.
- `7881/tcp` and `50000-50100/udp` for media.
- `7880` for the signal path, bound to loopback only.

## Not built

- **Recorder.** `LiveKitMedia.start_recorder` raises "not wired on this server yet", and the `livekit` real-time
  package is not in the server's requirements. Until both land, every start is refused with `meet_unavailable`. A
  call is never started without a transcript.
- **Signal path for browsers.** Browsers reach the signal port through a TLS front on the meet hostname. The front is
  not in compose yet, so `wss://` does not reach LiveKit on a real install.
- **Action items.** The after-call output is the transcript only. Drafting action items is not built.
- **Text to speech.** Bots reply in text only. No speech output exists.
- **Shared threads contract.** Thread visibility uses the current conversation check. Transcript posts use the current
  message path. Bot access uses the current bot access check. These swap to the threads core's `readable`,
  `threads.post` and teammate access once they land.
- **Events.** The `threads` topic is not in the live event stream yet (see Events above).
