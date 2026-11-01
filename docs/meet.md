# Tico Meet

A call happens in a thread. People talk and share their screen, a live transcript is kept while it runs, and bots that
were attached listen and reply in the thread. When the call ends, the transcript and the action items drafted from it
are posted to the thread.

Status on this branch: the call API, the CLI, the MCP tools, the OpenAPI operations, the server's recorder and the
browser signal path are built. Most tests run against fakes for LiveKit and the Gateway. One live test
(`backend/tests/test_call_live.py`) runs against a real `livekit-server` in dev mode, the digest-pinned image: a
synthetic speaker publishes audio through the LiveKit SDK, the recorder hears it, the Gateway's speech-to-text is the
only fake, and the transcript posts into the thread and the room is deleted. No install has run calls yet. Text to
speech is not built (see Not built).

## How a call works

1. **Start.** Someone who can write to the thread starts a call. They are its connector. Tico creates the call and its
   live meeting, then the server's recorder joins the call's media session as a hidden participant. It hears the call
   and never publishes. If the recorder cannot join, the call ends at once with `meet_unavailable`.
2. **Join.** People who can read the thread join and get a token good for 5 minutes. It grants microphone and screen
   share, never a camera and no data messages. A joined client refreshes its token with
   `POST /api/v2/calls/{call_id}/token` before it expires.
3. **Transcript.** The recorder subscribes to each microphone and screen-share audio track. Each speaker's audio goes to
   the Gateway's speech-to-text (the Recorder's model, `elevenlabs/scribe-v2`) in clips of up to 8 seconds, or sooner
   once the speaker is quiet for 3 seconds. Clips are transcribed on workers, so the recorder never waits on the
   Gateway. The text becomes the call's live meeting chunks. Audio is held in memory until it is sent and is never
   stored. If the Gateway does not answer, that clip is lost and the call goes on.
4. **Bots.** A person who can write to the thread and has joined the call attaches bots (up to 10). A bot must be one
   that person can write to. Attached bots get turns on the call's speech and reply in the thread, as they do in a
   live meeting. Their replies are messages in the thread.
5. **Access.** When someone loses read access to the thread, the server removes them from the media session through
   LiveKit's server API and refuses their token refresh. Bots that lose write access are detached the same way. A
   background sweep does this on each pass.
6. **End.** The connector ends the call. Speech still waiting is sent, and the end waits up to 150 seconds for clips
   already being transcribed. Then the recorder leaves and the media session closes. Then two messages are posted to
   the thread, each once and each marked `restricted`, so only people who can read the thread see them:
   - `Call transcript: <title>` with one `speaker: text` line per piece of speech. Long transcripts are cut short, and
     the message says where the full one is.
   - `Action items: <title>` with one `- ` line per item, or `No action items were found.`. The items are drafted
     from the transcript on the install's meeting model through the Gateway. The drafting is a server port of the
     Recorder's prompt and rule (an item stands only when its quote is in the transcript word for word; at most 5),
     in `backend/call_items.py`. If the Gateway does not answer, no action items message is posted and the transcript
     stays the call's record.
   Tasks are still made by hand from the action items.
7. **Restarts.** A Tico restart brings the recorder back for each live call from the database. If the media server
   stops, the call ends with `end_reason: media_lost`, and the transcript so far is kept.

## Spoken replies

`speak_replies` is off by default. The connector can set it when starting a call (`--speak` in the CLI, `speak_replies`
in REST and MCP), and the call shows it. Spoken replies are not sent yet: bots reply in text either way, and the flag
is kept for when they do.

## Who may do what

| Action | Who |
| --- | --- |
| See a call, list calls, join, refresh a token | People who can read the thread |
| Start a call, attach or detach bots | People who can write to the thread (attach also needs the person to have joined) |
| Leave | The person who joined |
| End | The connector |
| Anything | Bots are refused. BotOps acts as the person it works for |

Errors: `meet_unavailable` (503) when Tico Meet is not set up, the LiveKit package is missing, or the recorder cannot
join. `call_in_progress` (409) for a second live call in a thread, `call_ended` (409), `not_joined` (409) when a token is
asked for without joining, and `call_access_removed` (403) after removal. A call the caller cannot read is 404.

## API

| Method and path | What it does |
| --- | --- |
| `GET /api/v2/threads/{thread_id}/calls` | The thread's calls, newest first. `limit` (1-50) and `before` page through them. A page also stays under 256 KB; `next_before` is the cursor for the next page, `null` at the end |
| `POST /api/v2/threads/{thread_id}/calls` | Start a call. Body: `title` (optional), `speak_replies` (optional, default false) |
| `GET /api/v2/calls/{call_id}` | One call: state, connector, people, bots, `speak_replies`, how it ended |
| `POST /api/v2/calls/{call_id}/join` | Join. Returns `url`, `token` and `expires_in` (300) |
| `POST /api/v2/calls/{call_id}/token` | A fresh token while joined |
| `POST /api/v2/calls/{call_id}/leave` | Leave |
| `POST /api/v2/calls/{call_id}/end` | End (the connector) |
| `POST /api/v2/calls/{call_id}/bots` | Attach bots. Body: `bots`, 1 to 10 slugs |
| `DELETE /api/v2/calls/{call_id}/bots/{bot}` | Detach a bot |

The same operations are `hub call list|start|show|join|token|leave|end` and `hub call bot add|remove` in the `hub`
CLI, and `hub_call_list`, `hub_call_show`, `hub_call_start`, `hub_call_join`, `hub_call_token`, `hub_call_leave`,
`hub_call_end`, `hub_call_bot_add` and `hub_call_bot_remove` over MCP. The OpenAPI operations are `listCalls`,
`startCall`, `getCall`, `joinCall`, `refreshCallToken`, `leaveCall`, `endCall`, `attachCallBots` and `detachCallBot`.

Changes are written to the change log on the `threads` topic: `call.started`, `call.joined`, `call.left`,
`call.ended`, `call.bot_attached`, `call.bot_detached` and `call.participant_removed`. Each has an audit row. The live
event stream sends them with `GET /api/v2/events?topics=threads`, and only to people who can read the call's thread.

## Self-hosting

The `meet` compose profile runs LiveKit (Apache-2.0) on the cell host. It adds no paid cloud resource. Set these in
`.env` (see `.env.example`):

- `TICO_MEET_URL`: the install's own address as `wss://`, the same host as `TICO_DOMAIN`. Browsers connect to LiveKit's
  signal at `/rtc` on it.
- `TICO_MEET_DOMAIN`: the TURN hostname. Its DNS points at this host, and TURN over TLS uses it.
- `TICO_MEET_API_KEY` and `TICO_MEET_API_SECRET`: LiveKit's key pair. They go to the LiveKit service and the server only.
- `TICO_MEET_CERT_DIR`: a folder holding `fullchain.pem` and `privkey.pem` for `TICO_MEET_DOMAIN`. TURN over TLS needs
  them, and the service does not start without them.
- `TICO_MEET_IMAGE`: optional. The default is `livekit/livekit-server:v1.8.4@sha256:621fd87b...c532af`, the tag and
  its digest, both checked on Docker Hub. A new LiveKit version means a new digest in `compose.yaml`.

`TICO_MEET_INTERNAL_URL` defaults to `http://livekit:7880`, the server's own address for LiveKit's server API and for
the recorder.

**Signal path.** Caddy (the `caddy` profile) proxies `/rtc` on the app host to LiveKit's port 7880 on the compose network,
on 443 like the rest of the app (`backend/caddy_assets.py`). A tunnel install (the `cloudflared` profile) gets the same
route in cloudflared's ingress (`tunnel_config` in `docker/entrypoint.sh`), ahead of the app's own rule: `/rtc` goes
straight to LiveKit, which checks the token itself. The route is written only when `TICO_MEET_URL` is on `TICO_DOMAIN`
and the key and secret are set. Otherwise the server logs `warning: calls cannot connect over this tunnel: set
TICO_MEET_URL, key and secret for <domain>`, and browsers cannot join. Nothing publishes 7880. The tunnel carries only
the signal, so media still needs the ports below open on the host.

**Edge network.** LiveKit joins the `edge` network at `TICO_LIVEKIT_ADDRESS` (172.30.254.4 by default), so Caddy and
the tunnel reach it by name. The address is outside the dynamic range, and the server does not trust it as a front
door.

**Ports.**

- `3478/udp` and `5349/tcp`: TURN, and TURN over TLS on `TICO_MEET_DOMAIN`. Caddy keeps 443, so TURN never competes with it.
- `7881/tcp` and `50000-50100/udp`: media.
- No signal port.

**Dependency.** The server installs the `livekit` real-time package (pinned at 1.1.21 in `backend/requirements.txt`, and
imported only when a call starts). With numpy and protobuf, its wheels are 28.9 MB compressed; the installed packages
gzip to about 29 MB, the size of the layer it adds to the server image. The recorder stays in-process, because that is
under the 40 MB at which a separate recorder image would be worth it. These sizes come from the pinned wheels, not from
a built image.

## Not built

- **Text to speech.** `speak_replies` is stored, but no speech is produced. Bots reply in text.
- **Media through a tunnel.** The tunnel carries only the signal (see Signal path). A tunnel install carries calls only
  when its TURN and media ports are open to browsers on the host.
- **Live LiveKit beyond one dev server.** The live test covers one speaker on one `--dev` server. Not run yet: browsers
  through TURN and TLS, several calls at once, a LiveKit restart in the middle of a call, and the real Gateway.
- **Bot replies after access ends.** A bot whose membership of the thread has ended cannot post its reply, and the
  reply is not posted at all.
- **Connector access.** The transcript and action items are posted as the connector. If the connector can no longer
  post in the thread when the call ends, neither is posted, and the error report says so.
