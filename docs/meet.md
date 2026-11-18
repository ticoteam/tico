# Calls: Google Meet and Tico Meet

A call happens in a thread. A call is a Google Meet (the default) or a Tico Meet call (audio and screen share on the
install's own LiveKit). A live transcript is kept while it runs, and bots that were attached listen and reply in the
thread. When the call ends, the transcript and the action items drafted from it are posted to the thread.

## Google Meet

**How it works.** The call button makes a Google Meet space as the caller (the Workspace key impersonating their
mailbox), opens it, and posts a `Call` card in the thread (`refs.call.card`; the card wakes no bot). The space is
`TRUSTED`: people in the Workspace and invited guests join without knocking. Each viewer's link carries
`?authuser=<their Google address>` (their Workspace mailbox, else their Connect Google account) so Meet opens on the
right account. A call is still a live meeting governed by its thread: who may see it, join it and read its transcript
follows the thread, as for Tico Meet.

**Ringing.** A thread of up to 8 people rings everyone in it but the caller who can read it (`ring` on the call view,
`GET /api/v2/calls/ringing`, and the hooks in `backend/call_ring.py`, which phone push fills). A bigger thread rings
nobody; its readers see the call on the thread's row (`live_call`) and its card. Bots are never rung. A person stops
being rung once they open the call from Tico or Google sees them in it.

**Status.** The calls sweep (every heartbeat, 10 s) reads each live Meet as its connector: the space, then the people
still in its active conference (`latest_end_time IS NULL`). The reads run in parallel on a small pool, and the sweep
waits 5 s for them at most, so a slow Google never holds up the heartbeat; a read still out is made again on a later
pass. A call whose read failed skips 1, 2, 4... passes (at most 16) before its next. Google is the source of truth for `live_count`, each
participant's `in_call`, and `seen_at`. Rules, from live tests:

- Opening the Meet's pre-join page opens a conference with nobody in it. A conference alone is nobody in the call: only
  a listed participant is.
- `seen_at` is the first poll that lists anyone. A call can span several conferences: when the last person leaves Google
  ends the conference at once, and a rejoin makes a new one on the same space (`meet_conference` is the latest).
- Only a successful read changes the status. A read Google does not answer (a timeout, its own error, 429) keeps the
  count and who is in the call as the last good read left them.
- `meet_ended`: someone was seen, and successful reads have shown nobody for 60 s (a grace for dropping and
  rejoining). Failed reads never count toward that minute.
- `no_answer`: nobody was seen within 120 s of the start.
- `stale`: Google gave no status (scope not authorized, mailbox not covered, Google refusing, or no answer for 10
  minutes) and the call is 8 hours old. The connector can always End, which also asks Google to end the active
  conference and then locks the space (`RESTRICTED`) so its link is spent (both best effort). A call the sweep ends has
  its space locked too.
- `duration_s`: from the first time anyone was seen (`seen_at`) to the last (`meet_active_at`); null when nobody was
  ever seen. An ended call keeps its `status_source`: null duration with a source is a missed call, with none a call
  nothing watched.

People are matched to Google's participants by display name (one person with that name; email when Google gives one),
and only people who can read the thread; anyone else is counted and never named. A change to the count, who is in it,
the status source or `seen_at` writes one `call.updated` change (no audit row).

**Recording.** Google records nothing for Tico, and Tico does not record a Google Meet call. For a transcript of a
call, use a meeting tool such as Granola; see [Meetings](meetings.md#meeting-importers). Tico Meet calls are transcribed
by Tico Meet's own server-side recorder, below.

**Setup.** Google Meet needs the Workspace key (Tools > Settings > Email) authorized for
`https://www.googleapis.com/auth/meetings.space.created` and the Meet API turned on in the key's project. The setup
script turns the API on, and the delegation link asks for the scope with Gmail's and Calendar's. A key made before
that: an admin adds the scope to the client in Google Admin (Security > API controls > Domain-wide delegation), then
presses Check. The check proves the scope with a token for it and a read of a space that cannot exist (404 is proof),
and makes nothing. A key without it keeps working for mail and calendar, and What Tico may do is unchanged.

**Availability.** `google_meet` is available to a person when the Workspace key is active, covers their mailbox and
its last check proved the Meet scope. `auto` picks Google Meet when it is available, else Tico Meet when LiveKit is
set up, else `503 meet_unavailable`. If Google will not make the space, `auto` falls back to Tico Meet when it can.

**Live check.** `scripts/meet-live-check.py --key <key.json> --mailbox <address>` makes a space, prints its link, polls
who is in it every 10 s and ends it. It needs no running Tico.

## Tico Meet

Status on this branch: the call API, the CLI, the MCP tools, the OpenAPI operations, the server's own recorder, the
bots' voice and the browser signal path are built. Most tests run against fakes for LiveKit and the Gateway. The live
tests (`backend/tests/test_call_live.py`) run against a real `livekit-server` in dev mode when `TICO_LIVE_LIVEKIT` names
one; CI does not start one (no CI job runs a `livekit-server`). In the first, a synthetic speaker publishes audio through the LiveKit SDK, the recorder
hears it, the Gateway's speech-to-text is the only fake, and the transcript posts into the thread and the LiveKit
session is closed. In the second, the call's Tico voice speaks into the room and a person subscribed to it hears it.
No install has run calls yet.

## How a call works

1. **Start.** Someone who can write to the thread starts a call. They are its connector. Tico creates the call and its
   live meeting, then the server's recorder joins the call's media session as the participant **Tico voice**
   (identity `tico-recorder-<call id>`). It hears the call and publishes one microphone track (48 kHz mono) that
   carries the bots' spoken replies. Clients never show it as a person, and no count of who is in the call includes
   it. If the recorder cannot join, the call ends at once with `meet_unavailable`. The start may name bots (`bots`) to
   attach at once.
2. **Join.** People who can read the thread join and get a token good for 5 minutes. It grants microphone and screen
   share, never a camera and no data messages. A join sent again with the same idempotency key gets its token back only
   while the person can still read the thread. A joined client refreshes its token with
   `POST /api/v2/calls/{call_id}/token` before it expires. Once End is pressed (`ending`) joins and token refreshes are
   refused (`409 call_ended`), and the thread rail no longer shows the call as live.
3. **Transcript.** The recorder subscribes to each person's microphone track (never Tico voice's, and not a shared
   screen's sound). Each speaker's audio is cut where they pause: a clip ends after 700 ms of quiet, keeps 200 ms before
   the first loud frame and after the last, and is cut at 60 seconds when nobody pauses. A click shorter than 200 ms is
   not speech. A speaker whose audio stops coming (muted, left) has their clip cut 700 ms after their last frame, by a
   timer on the recorder's own loop. Each clip goes to the Gateway's speech-to-text (`tico-transcribe-1.1`; when the
   Gateway answers 400 that it does not know that model, the call falls back once to `elevenlabs/scribe-v2` and logs
   it). Clips are transcribed on workers, so the recorder never waits on the Gateway, and are stored in the order they
   start, across people: a clip waits for the clips that start before it and for an utterance someone else began before
   it and is still saying. The text becomes the call's live meeting chunks. Audio is held in memory until its clip is
   stored and is never stored itself. At most 4 clips of a call wait for their text at once: past that the oldest is
   dropped. If the Gateway does not answer, that clip is lost and the call goes on; in a 1:1 call with a bot a lost or
   dropped clip shows `Please repeat that`, and no bot is asked.
4. **Bots.** A person who can write to the thread and has joined the call attaches bots (up to 10 in the call, counting
   the ones already in it; more is `422 bot_limit`, through the call's route and the live meeting's alike). A bot must
   be one
   that person can write to. Attached bots get turns on the call's speech and reply in the thread, as they do in a
   live meeting. A reply is a run of the bot's own runner job, so the bot needs a runner that takes private meeting
   turns (`private_lanes_v1`), and it is posted in the thread only through that run. A bot stays attached only while the
   person who attached it can still read the thread and write to the bot. When that ends, the bot stops hearing the call
   and its replies are refused at once. The sweep then detaches it and cancels its open turns.
5. **Access.** When someone loses read access to the thread, the server removes them from the media session through
   LiveKit's server API and refuses their token refresh. Leaving removes a person the same way, at once. A removal that
   LiveKit does not confirm stays pending, and the background sweep retries it on each pass until it is confirmed.
6. **End.** The connector ends the call. The End is checked and stored first: a refused End (no idempotency key, not
   the connector) leaves the call fully live. Then listening and the bots' voice stop at once: no audio heard after End
   is used, and what is playing stops. The open questions are closed. Speech already heard is still transcribed by one
   thread per call, however many Ends arrive: when it is done within 2 seconds End answers with the call `ended`;
   otherwise it answers `ending` (a second End answers the same), and the call becomes `ended` once the clips are
   stored (up to 45 seconds). A Tico that restarts while a call is `ending` ends it on the sweep. Then the recorder leaves and the media session closes. If LiveKit does not confirm the
   close, the sweep retries it, and the transcript and action items wait until it does. Then two messages are posted to
   the thread from the connector, each once. Each names the call's meeting, so the meeting's own rules decide who reads
   it, and each is marked `restricted`:
   - `Call transcript: <title>` with one `speaker: text` line per piece of speech. Long transcripts are cut short, and
     the message says where the full one is.
   - `Action items: <title>` with one `- ` line per item, or `No action items were found.`. The items are drafted
     from the transcript on the install's meeting model through the Gateway. The drafting follows a
     prompt and rule (an item stands only when its quote is in the transcript word for word; at most 5),
     in `backend/call_items.py`. If the Gateway does not answer, the items stay owed. The sweep tries again, each wait
     longer than the last (two minutes, doubling, up to an hour). A day after the first failure the thread gets one
     line, `Action items for <title> could not be made`, with the retry route, and the call gives up on them. The
     connector can ask again (`POST /api/v2/calls/{call_id}/action-items/retry`), and the sweep makes them on its next
     pass.
   A thread that refuses one of these posts is final: nothing retries it. The call records the refusal in its audit log
   and in its `holds` list (`kind`, `reason`, `created_at`), which only its connector sees.
   Tasks are still made by hand from the action items.
7. **Presence.** The sweep reads who is in the room from LiveKit (Tico voice left out): it sets each person's
   `in_call`, the call's `live_count` and `status_source: recorder`. A room nobody is in ends the call
   (`end_reason: empty`) after 60 seconds, so someone who drops can rejoin; a call nobody ever joined ends `no_answer`
   after two minutes. A read LiveKit does not answer changes nothing.
8. **Restarts.** A Tico restart brings the recorder back for each live call from the database. A live call whose media
   session is gone when the sweep looks ends with `end_reason: media_lost`, and the transcript so far is kept. A call
   whose recorder never started ends the same way two minutes after it began.
9. **One owner.** One server process holds each call's media: the process whose recorder is in the room. Only it cuts
   clips, asks bots and plays their voice. A reply that reaches a process that does not hold the call is posted as
   text and not spoken. With one server process per install (today's deployment), that process holds every call.

## Voice

A Tico Meet call is a voice call: `speak_replies` is on by default for Tico Meet (a call may turn it off). A call with
bots or voice is always Tico Meet: asking for Google Meet with either is `422 voice_needs_tico_meet`, and `auto` picks
Tico Meet.

**Calling a bot.** The call button in a DM with one bot, and Call on a bot's card in the org chart, start a Tico Meet
call in the viewer's DM with that bot, with the bot in it, and join it.

**Who answers.** In a 1:1 call with a bot (one bot attached, and the thread's people are exactly one person and that
bot) the bot answers every utterance. In any other voice call, a group DM with two people and a bot among them, only a
bot whose name was said answers; the router's other windows and its decisions on chat do not run in a voice call. A
call has one open question: a bot asked while another answers waits its turn (the latest question for it), and a
question is dropped with a note (`<bot> missed a question`) 30 seconds after it was heard. Lines sent to the meeting's
own transcript and chat routes ask through the same queue, so they never open a second question. A question heard 30
seconds or more before it was transcribed (a slow Gateway) is not asked; a 1:1 call shows `Please repeat that`.

**The answer.** The bot answers on its own model (a voice call's meeting model is the bot's own), with an instruction to
speak: at most about 80 words, no markdown, lists, links, code or emoji. The no-actions rule of a live meeting holds. A
turn is dead 30 seconds after its question was heard: a bot that has not answered by then is cut off (`skip_reason:
voice_deadline`, note `<bot> couldn't answer in time`), and its late answer is neither posted nor heard. A reply stored
after that time, or whose first sentence would start playing after it, is posted as text and not heard.

**Speaking it.** The reply is posted in the thread as text first; the voice is made after the reply's transaction
commits, never inside it. It goes to the Gateway's text-to-speech (`POST /audio/speech`, model `tico-voice-1.0`, raw
16-bit PCM; the voice is picked from `GET /audio` by the bot's slug, so a bot keeps its voice) one sentence at a time,
the next made while one plays, and plays on Tico voice's track. The Gateway's voices are read when the call starts and
kept for an hour, so no reply waits on them. Markdown is taken out and the spoken part is cut to about 600 characters.
Only a reply to the open question's own turn is spoken. Before any speech is made, and before each sentence plays, the
call must still be live and not ending, and the bot still attached by someone who may still use it. A reply is spoken
once. Bots speak at most 30 minutes in a call: a sentence is made only when its length (estimated from its characters)
fits in what is left. A voice the Gateway does not make leaves the text as it is (the server logs
`call_voice_speech_failed` with the Gateway's status). Detach (through the call's route or the live meeting's), lost
access and End stop a bot's voice at once, even a sentence cleared to play a moment before; taking out a bot that is
only waiting leaves the bot speaking alone.

**What the call shows.** A call's view has `voice`: each attached bot's `state` (`listening`, `waiting`, `thinking`,
`making_voice` or `speaking`) and `caption` (the sentence being spoken), and `note`, the latest short note for 10
seconds. The call bar shows the bot answering, dots while it thinks, and its sentence while it speaks.

## Who may do what

| Action | Who |
| --- | --- |
| See a call, list calls, join, refresh a token | People who can read the thread |
| Start a call, attach or detach bots | People who can write to the thread (attach also needs the person to have joined) |
| Leave | The person who joined, even after losing access to the thread |
| End | The connector |
| Anything | Bots are refused. BotOps acts as the person it works for |

Errors: `meet_unavailable` (503) when no call provider is available to the caller, Google will not make the space,
Tico Meet is not set up, the LiveKit package is missing, or the recorder cannot join. `not_tico_meet` (409) for a token
on a Google Meet call, `call_in_progress` (409) for a second live call in a thread, `call_ended` (409), `not_joined` (409) when a token is
asked for without joining, and `call_access_removed` (403) after removal. A call the caller cannot read is 404, the
same as a call that does not exist. `call_meeting` (409) when the live meeting's own controls
(`/api/v2/live-meetings/{id}/control`) are used on a call's meeting: a call is paused, resumed or ended through the call.

## API

| Method and path | What it does |
| --- | --- |
| `GET /api/v2/threads/{thread_id}/calls` | The thread's calls, newest first. `limit` (1-50) and `before` page through them. A page also stays under 256 KB; `next_before` is the cursor for the next page, `null` at the end. `providers` is what the viewer could start, in order; `available` says whether that is anything |
| `POST /api/v2/threads/{thread_id}/calls` | Start a call. Body: `title` (optional), `speak_replies` (optional; on for Tico Meet, off for Google Meet), `provider` (`auto`, `google_meet` or `tico`; default `auto`; bots or voice make it Tico Meet), `bots` (optional, up to 10 slugs attached at the start) |
| `GET /api/v2/calls/ringing` | The live calls ringing the caller now |
| `GET /api/v2/calls/{call_id}` | One call: state (`live`, `ending` or `ended`), connector, people (each with `in_call`), bots, `speak_replies`, how it ended, `holds` (the connector only: posts the thread refused at the end), and `provider`, `meet_uri`, `join_url`, `live_count`, `status_source`, `seen_at`, `duration_s`, `ring`, `card_message_id`, `transcript_message_id`, and `voice` (see Voice; `null` without voice) |
| `POST /api/v2/calls/{call_id}/join` | Join. Tico Meet returns `url`, `token` and `expires_in` (300); Google Meet records the open and returns `url` (the viewer's `join_url`) |
| `POST /api/v2/calls/{call_id}/token` | A fresh token while joined (Tico Meet only) |
| `POST /api/v2/calls/{call_id}/leave` | Leave |
| `POST /api/v2/calls/{call_id}/end` | End (the connector). Answers the call `ended`, or `ending` while speech already heard is transcribed |
| `POST /api/v2/calls/{call_id}/action-items/retry` | Ask again for action items the Gateway could not make (the connector, after the call ended) |
| `POST /api/v2/calls/{call_id}/bots` | Attach bots. Body: `bots`, 1 to 10 slugs; at most 10 in the call (`422 bot_limit`) |
| `DELETE /api/v2/calls/{call_id}/bots/{bot}` | Detach a bot |

The same operations are `tico call list|start|show|join|token|leave|end|retry` and `tico call bot add|remove` in the
`tico` CLI, and `tico_call_list`, `tico_call_show`, `tico_call_start`, `tico_call_join`, `tico_call_token`, `tico_call_leave`,
`tico_call_end`, `tico_call_retry`, `tico_call_bot_add` and `tico_call_bot_remove` over MCP. The OpenAPI operations are
`listCalls`, `startCall`, `getCall`, `joinCall`, `refreshCallToken`, `leaveCall`, `endCall`, `retryCallActionItems`,
`attachCallBots` and `detachCallBot`.

Changes are written to the change log on the `threads` topic: `call.started`, `call.joined`, `call.left`,
`call.ended`, `call.bot_attached`, `call.bot_detached`, `call.participant_removed`, `call.post_refused`,
`call.action_items_given_up`, `call.action_items_retry` and `call.updated`. Each but `call.updated` has an audit row. A refusal's event tells the thread's
readers that a post was refused, not what it said. The live
event stream sends them with `GET /api/v2/events?topics=threads`, and only to people who can read the call's thread.
A call's view lists only the people and bots the viewer may see (the teammate See level), and a person always sees
their own place in it.

## Self-hosting

The `meet` compose profile runs LiveKit (Apache-2.0) on the cell host. It adds no paid cloud resource. Set these in
`.env` (see `.env.example`):

- `TICO_MEET_URL`: the install's own address as `wss://`, the same host as `TICO_DOMAIN`. Browsers connect to LiveKit's
  signal at `/rtc` on it.
- `TICO_MEET_DOMAIN`: the TURN hostname. Its DNS points at this host, and TURN over TLS uses it.
- `TICO_MEET_API_KEY` and `TICO_MEET_API_SECRET`: LiveKit's key pair. They go to the LiveKit service and the server only.
- `TICO_MEET_CERT_DIR`: a folder holding `fullchain.pem` and `privkey.pem` for `TICO_MEET_DOMAIN`. TURN over TLS needs
  them, and the service does not start without them.
- `TICO_MEET_IMAGE`: optional. The default is `livekit/livekit-server:v1.13.11@sha256:25cd902d...1d3e20`, the tag and
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

### On Tico Cloud (a cell)

A cell runs one LiveKit for every company on the host, turned on with `python3 -m setup cell init --meet-domain
<TURN host>`. `setup cell` then writes the `TICO_MEET_*` keys into each company's `.env`, and its router sends `/rtc`
on every company host to LiveKit. The design, the production steps in order and the rollback are in
[cell.md, Tico Meet](cell.md#tico-meet).

## Not built

- **Voice beyond one process.** Each call's media and voice live in the one process that holds it (How a call
  works, One owner). Several server processes per install would need the reply handed to the owning process.
- **Interrupting a bot.** Speaking over a bot does not stop it; End, detach or a lost access does.
- **Media through a tunnel.** The tunnel carries only the signal (see Signal path). A tunnel install carries calls only
  when its TURN and media ports are open to browsers on the host.
- **Live LiveKit beyond one dev server.** The live tests cover one speaker and Tico voice on one `--dev` server, run by
  hand (`TICO_LIVE_LIVEKIT`). Not run yet: browsers
  through TURN and TLS, several calls at once, a LiveKit restart in the middle of a call, and the real Gateway.
- **Bot replies need a runner.** A reply is a run of the bot's runner job. A bot whose runner does not take private
  meeting turns waits, and the meeting says so, until that runner is updated.
