# Calls: Google Meet and Tico Meet

A call happens in a thread. A call is a Tico Meet call (the default, for every call, people-only calls too: audio and
screen share on the install's own LiveKit) or a Google Meet, picked per call with **Use Google Meet instead** next to the
call button. A Google Meet has no bots. A live transcript is kept while it runs, and bots that were attached listen and reply in the
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
its last check proved the Meet scope. `auto` picks Tico Meet when LiveKit is set up, else Google Meet when it
is available, else `503 meet_unavailable`. A Google Meet is started by naming it (`provider: google_meet`); the call
button's menu offers it as **Use Google Meet instead** when it is available. If Google will not make the space, `auto`
falls back to Tico Meet when it can.

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

## The meeting window

Opening a Tico Meet call (starting it, the thread's call bar, the call card or the ring) opens a view that shows only
the meeting, at `#/call/<call id>`: a small window (about 400 by 640) in the desktop app, a new tab on the web, and the
whole screen on a phone. There is one window per call: Start, Join, the call card and the ring all open the same one
(the desktop app's `call-<org>-<call id>`, a browser's tab named `tico-call-<call id>`), so a second open brings it to
the front. If the window cannot open (a blocked popup), the call is joined in the thread with its call bar instead.
The window joins the call only when it was opened to (`#/call/<call id>?join=1`, which those entries add); a plain
visit to its address, such as a copied link, shows Join.

- **Header.** The title, `Live` with the time since the call started, and one face per person and bot in the call:
  the people the call view lists as in it and its bots. The media room only says who of them is speaking or muted; it
  never adds a face. Each face shows one state: a green ring on the one whose words the caption shows while they speak,
  a small crossed-out mic on someone muted (a muted mic shows no ring), or a breathing ring on a bot that is thinking
  or making its voice. Past two rows of faces (three on a phone) the rest are
  `+N`, which opens People. The dashed `+` face opens People to add someone. Tapping a bot's face adds it to the call,
  or takes it out at once with `<Bot> left · Undo`.
- **Caption.** Under the faces, up to three lines: who is speaking and what they said (a bot speaking: its own
  caption in the call view's `voice.bots`; else the latest words of someone the room hears now; else the transcript's
  last words), and for a few seconds the voice's `note`.
- **Tabs.** Chat (the thread itself, with the composer, without the call's own posts; its `+` menu has Upload file,
  Link a file, which posts the file's card at once, Link a task and New task, which makes a task linked to the call's
  meeting at once; a task posted either way shows as a card with its state, title and owner), Transcript (the live
  transcript, each new or corrected piece applied from the meeting's event stream as it comes) and People (who is in
  the call, or while searching, people, bots, or a whole team's bots to add). Adding a person rings them into the call
  (`POST /api/v2/calls/{call_id}/ring`): someone who already reads the thread keeps their place in it, anyone else is
  added to the thread first. The count on People is who is in the call, bots tapped out not counted, the same as the
  thread's call bar.
- **Controls.** Mic, share and Leave. Share is a small menu: **Screen** (where the browser can share one) and **Show a
  Tico file**, a search of the company's files and pages that shows the pick to everyone in the call. While the viewer
  shows something, share is Stop. On the macOS desktop app, the system asks for microphone access the first time
  you join, and the share button opens the Tico file picker. The connector also has End in the Leave menu.
- **Sharing.** One thing shows at a time: someone's screen or a Tico file. It is one record on the call, its `stage`
  (`PUT /api/v2/calls/{call_id}/stage` with `kind` `screen`, or `file` and a `file_id`; `DELETE` to stop). Only
  someone in the call may set it, a file only when they may read it, and the file's current version is what shows.
  The newest write wins, as the server orders them: a new screen or file replaces the one before, and a person whose
  screen is replaced stops sharing it. Stop (or the browser's own Stop sharing) stops the screen at once, then clears
  the stage; a stop clears only the stopper's own share (the connector may clear anyone's), and a presenter who
  leaves takes theirs with them. Each change is a `call.stage` change, so every open window reads the call again. The
  call view's `stage` is checked against each viewer's own rights on every read: a file they may not read comes back
  as `no_access` with nothing that names it, so nothing of it is fetched, and a viewer who loses it mid-share sees
  "No access" from their next read. A file shows as the file view draws it, that exact version read with the viewer's
  own rights.
  While someone else's screen or any file shows, the window switches to the wide layout: the stage on the left,
  the tabs on the right, or the stage above the tabs in a window too narrow for that. It goes back to small when
  sharing stops. The desktop app's window (it asks the app, `callWindowSize`, which only the call's own window may) and
  a pop-up the page opened grow to about 1100 by 720 and go back to their size after; a tab keeps its size. On a phone
  the screen has a full-screen button.
- **After the call.** When the call ends, the open window stops its chat and transcript stream and leaves the media
  room, and the same view shows the meeting notes (`GET /api/v2/calls/{call_id}/notes`): who was in it (bots too), how
  long it ran, a short summary, the action items (each with who took it on, Create, which makes the task linked to the
  meeting for that person, and ✕ to hide it here) and the transcript, with search and copy.

A Google Meet call still opens Meet in a tab of its own.

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
   screen's sound). Each speaker's audio is cut after about 450 ms of quiet. A fragment with less than 600 ms of speech
   gets 700 ms of quiet so the speaker has more time to continue. A clip keeps 200 ms before the first loud frame and
   after the last, and is cut at 60 seconds when nobody pauses. A clip needs at least 200 ms of speech.
   Gaps between microphone frames count as quiet, and the recorder's timer uses the same pause threshold when frames
   stop coming. The recorder flushes when the track ends. Each clip goes to the Gateway's speech-to-text
   (`tico-transcribe-1.1`; when the Gateway answers 400 that it does not know that model, the call falls back once to
   `elevenlabs/scribe-v2` and logs it). Transcription workers handle Gateway requests while a separate worker writes
   the chunks in speech order.
   A ready clip can move past an earlier request that has been out for six seconds. That earlier clip is marked late.
   A clip still waits for an utterance someone else began before it and is still saying. The text becomes the call's
   live meeting chunks. Audio is held in memory until its clip is settled. At most 4 clips of a call wait for their text
   at once. Past that the oldest is dropped. In a 1:1 call with a bot, a late or dropped clip or one the Gateway
   cannot turn into text shows `Please repeat that`. The server writes one `call_clip` line per clip with only its call
   ID, clip number, duration, outcome and elapsed time since the cut.
4. **Bots.** A person who can write to the thread and has joined the call attaches bots (up to 10 in the call, counting
   the ones already in it; more is `422 bot_limit`, through the call's route and the live meeting's alike). A bot must
   be one
   that person can write to. Attached bots get turns on the call's speech and reply in the thread, as they do in a
   live meeting. The bot's runner posts its final reply in the call's thread. A bot stays attached only while the
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
   - `Action items: <title>` with one `- ` line per item, or `No action items were found.`, then `Summary:` and 3 to 5
     `- ` lines. The items and the summary come from one call to the install's meeting model through the Gateway. The
     drafting follows a prompt and rule (an item stands only when its quote is in the transcript word for word; at most
     5; its owner, when someone took it on, is one of the speakers), in `backend/call_items.py`. The message keeps them
     as data too (`refs.call.notes`), which the meeting window reads. If the Gateway does not answer, the items stay
     owed. The sweep tries again, each wait longer than the last (two minutes, doubling, up to an hour). A day after the
     first failure the thread gets one line, `Action items for <title> could not be made`, with the retry route, and the
     call gives up on them. The connector can ask again (`POST /api/v2/calls/{call_id}/action-items/retry`), and the
     sweep makes them on its next pass.
   A thread that refuses one of these posts is final: nothing retries it. The call records the refusal in its audit log
   and in its `holds` list (`kind`, `reason`, `created_at`), which only its connector sees.
   Tasks are still made by hand from the action items (the meeting window's Create).
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

**Call now.** The Meetings page's call icon (only where the server runs Tico Meet, `features.tico_meet` in the config)
picks people and bots, each named by its id or its slug, (a team, listed first with a few of its faces and how many, adds its bots in one pick) and
starts a Tico Meet call with the bots in it: in the viewer's DM when one teammate is picked,
else in a new thread with them all, named for the call's title or, without one, for who is in it. Its meeting window
opens and joins it.

**Bot notes.** Each bot has a brief for the call. If it joins without prep, Tico queues a background prep task
immediately. The bot uses the built-in `meeting-prep` skill to add the latest figures, recent work
and open tasks people are likely to ask it about. It can add useful facts as it finds them. In the meeting window,
open **People** and use the notes icon beside a bot. Scheduled prep writes to the same brief and still posts its
prep message in the thread.

**Who answers.** In a 1:1 call with a bot (one bot attached, and the thread's people are exactly one person and that
bot) the bot answers every utterance. In any other voice call, a group DM with two people and a bot among them, only a
bot whose name was said answers; the router's other windows and its decisions on chat do not run in a voice call. A
call has one open question: a bot asked while another answers waits its turn (the latest question for it), and a
question is dropped with a note (`<bot> missed a question`) 30 seconds after it was heard. Lines sent to the meeting's
own transcript and chat routes ask through the same queue, so they never open a second question. A question heard 30
seconds or more before it was transcribed (a slow Gateway) is not asked; a 1:1 call shows `Please repeat that`.

**The answer.** When a bot joins, a current runner starts a Claude Code session on the bot's computer with the
company subscription. Its first model request warms the bot's identity, the call notes and the voice instructions.
Each bot in the call holds a normal lease and uses one `turns_at_once` slot for the whole call (five slots on Shared).
Each question goes to the resident session, with updated notes when they change. At 24,000 context tokens the runner
starts a fresh process with the primer and a short excerpt of the recent call transcript.
Each bot uses its own model at medium effort with Claude Code’s Concise output style. Follow-up tasks use the bot’s usual settings.

The bot answers in plain spoken sentences, in about 80 words or fewer. It uses what it knows and its call notes.
The bot can use its usual tools and permissions. People read the transcript and chat through the call thread's
access rules. When work needs more time, the bot can say “Let me check.” and file a follow-up task. At 30 seconds
from the time it heard a question, the call skips an unanswered turn and shows `<bot> couldn't answer in time`.

**Speaking it.** The first complete sentence goes to the Gateway's text-to-speech while the model continues writing.
Later sentences are prepared as they arrive and play in order on the Tico voice track as PCM frames stream in. The full reply is posted to
the thread when the model finishes. The bot keeps its voice across calls. The Gateway's voices are read when the call
starts and kept for an hour. The spoken part is capped at about 600 characters. Before synthesis and playback, the
call must still be live and the bot's attacher must still have access. Bots speak at most 30 minutes in a call. A
failed synthesis leaves the reply text in the thread. Detach, lost access and End stop playback and close the bot's
voice session.

**Timing.** Each turn writes one `call_voice_turn` line to support diagnostics with its call, bot, turn, outcome and any skip
reason. `first_token_ms`, `first_sentence_ms` and `first_audio_ms` use the recorder's monotonic clock, starting
when it heard the utterance. The line also records transcription, queue, answer and speech durations, and
`tts_first_chunk_ms` measures the speech request to the first response bytes. A milestone the
turn never reaches is `null`.

**What the call shows.** A call's view has `voice`: each attached bot's `state` (`listening`, `waiting`, `thinking`,
`making_voice` or `speaking`) and `caption` (the sentence being spoken), and `note`, the latest short note for 10
seconds. The call bar shows the bot answering, dots while it thinks, and its sentence while it speaks.

## Scheduled calls

A call can be booked for later: the Call dialog with a time picked and **Schedule**, or `starts_at` and `ends_at` on the
start (whole minutes; seconds are dropped). The calendar is where its time lives; Tico keeps only the call and a link
to its one event, and writes that event before it changes the call.

1. **Booking.** It is a Tico Meet call in state `scheduled`, with the bots picked (checked before the calendar is
   written). Nobody is in it, nobody is rung, and it is in no live list. Its card goes in the thread, the thread
   header's phone shows its time (and joins it), and the Meetings page lists it under Upcoming
   (`GET /api/v2/calls/upcoming`) with Join and a ⋯. One private event is written as the person booking it: on their
   Google calendar when they are connected, else on their Tico calendar. It invites the thread's people who can read
   the thread (bots are never Google attendees), its location and notes hold the call's link (`/#/call/<id>`), and a
   Google event carries `extendedProperties.private.tico_call`. A `calendar_event_links` row (`link_type` `call`)
   ties the event to the call. A calendar that will not take the event leaves no call (`calendar_write_failed`, with
   the calendar's own status). The call's id comes from the request's idempotency key, so a retry after a lost answer
   or Google's 409 finds the event already written (its `tico_call` must match) rather than inviting twice. This is
   the one place a person's action writes their calendar (calendar decision 13 is otherwise "people view").
2. **Change time and Cancel call.** The booker and the thread's managers (`can_edit` on Upcoming rows and on
   `next_scheduled`) have both: on Upcoming under the row's ⋯, and in the header the call's time opens Join, Change
   time and Cancel call (for anyone else it only joins). Change time reopens the Call dialog filled in and sends `PATCH /api/v2/calls/{id}`: the event is
   changed first (its invitees become who can read the thread now; their answers are kept when that set is the
   same), and the call only once the calendar took it. A refusal changes nothing and says why
   (`calendar_write_failed`). Cancel call (once confirmed; no Undo, as the calendar has already told the invitees), and End on a call still
   scheduled, deletes the event and then cancels the call; a calendar that does not answer leaves it booked (`calendar_write_failed`, retryable), one that refuses for
   good (a revoked grant) lets the cancel go on.
3. **Changes made in the calendar.** A change to the event that Tico hears (the Google watcher, a write to the Tico
   calendar) reads the call again at once: a moved event moves the call, and a cancelled or deleted one cancels it
   (`end_reason: calendar_cancelled`). An event made all-day is not followed; the call keeps its times. A move that
   puts the start more than 30 minutes away clears `prepped_at`, so the bots prep again for the new time (a new task
   once the first one is closed).
4. **Before the start.** Once a minute the scheduler's Scheduled calls step is the fallback: it takes the calls due
   their prep within 30 minutes, or past their end, at most 50 and for at most 20 seconds a pass (the rest wait a
   minute). It reads each call's event again first, as above. An event that cannot be read is read again on the next
   pass, and after three passes the call is prepped with the times it has. Then each bot on the call gets one task,
   **Prepare for <title>**, that runs the built-in `meeting-prep` skill: the bot reads the thread, the notes of the
   thread's earlier calls, the tasks and files it links and its own work. It adds likely answers to its call brief
   and posts one short prep note in the thread.
   The task goes to exactly that bot, it is private unless the thread is a company thread, and none is given when the
   booker can no longer read the thread. `prepped_at` is set in the same write, so the tasks are given once.
   A bot attached after that sweep gets its prep task as soon as it is added.
5. **The start.** The meeting window shows the start time and Join. The first join reads the event live: a cancelled
   one refuses the join and cancels the call (`call_cancelled`, 409), a moved one moves it, and a calendar that cannot
   be read lets the join go on. That join starts the call: it is live from then, with the bots it was booked with, and
   rings the thread's other people as a start does. While another call is live in the thread, joining it is
   `call_in_progress`.
6. **The end.** A scheduled call nobody joined ends `missed` an hour after its end, and only from an end its calendar
   confirmed: a call whose event cannot be read is never ended `missed`.

Not done: repeating scheduled calls and replies to the invitation in Tico.

## Who may do what

| Action | Who |
| --- | --- |
| See a call, list calls, join, refresh a token | People who can read the thread |
| Start or schedule a call, attach or detach bots | People who can write to the thread (attach also needs the person to have joined) |
| Leave | The person who joined, even after losing access to the thread |
| End | The connector |
| Change a scheduled call's time, cancel it | Its booker and the thread's managers |
| Read a bot's brief | People who can read the thread and see the bot, and the attached bot itself |
| Add to a bot's brief | The attached bot, including during a private task |

BotOps acts as the person it works for when using call controls.

Errors: `meet_unavailable` (503) when no call provider is available to the caller, Google will not make the space,
Tico Meet is not set up, the LiveKit package is missing, or the recorder cannot join. `not_tico_meet` (409) for a token
on a Google Meet call, `call_in_progress` (409) for a second live call in a thread, `call_ended` (409), `not_joined` (409) when a token is
asked for without joining, and `call_access_removed` (403) after removal. A call the caller cannot read is 404, the
same as a call that does not exist. `call_meeting` (409) when the live meeting's own controls
(`/api/v2/live-meetings/{id}/control`) are used on a call's meeting: a call is paused, resumed or ended through the call.

## API

| Method and path | What it does |
| --- | --- |
| `GET /api/v2/threads/{thread_id}/calls` | The thread's calls, newest first. `limit` (1-50) and `before` page through them. A page also stays under 256 KB; `next_before` is the cursor for the next page, `null` at the end. `providers` is what the viewer could start, in order; `available` says whether that is anything; `next_scheduled` is the thread's soonest scheduled call (with `can_edit`), or `null` |
| `POST /api/v2/threads/{thread_id}/calls` | Start a call. Body: `title` (optional), `speak_replies` (optional; on for Tico Meet, off for Google Meet), `provider` (`auto`, `google_meet` or `tico`; default `auto`; bots or voice make it Tico Meet), `bots` (optional, up to 10 slugs attached at the start), `starts_at` and `ends_at` (optional, ISO 8601 with an offset: both schedule a Tico Meet call for then, up to 8 hours long; see Scheduled calls) |
| `GET /api/v2/calls/ringing` | The live calls ringing the caller now |
| `GET /api/v2/calls/live` | The live Tico Meet calls in threads the caller can read (the thread's own read rule), newest first, as call views; an ending call is left out. The Meetings page lists them under Live, above Upcoming, read again on the `threads` topic's `call.*` changes |
| `GET /api/v2/calls/upcoming` | The scheduled calls the caller is in (booked by them, or with them among the thread's people), soonest first. Each row is compact: `id`, `title`, `thread_id`, `scheduled_start`, `scheduled_end`, `people` (up to 8, the booker first), and `can_edit` |
| `GET /api/v2/calls/{call_id}` | One call: state (`scheduled`, `live`, `ending` or `ended`), `scheduled_start` and `scheduled_end`, connector, people (each with `in_call`), bots, `speak_replies`, how it ended, `holds` (the connector only: posts the thread refused at the end), and `provider`, `meet_uri`, `join_url`, `live_count`, `status_source`, `seen_at`, `duration_s`, `ring`, `card_message_id`, `transcript_message_id`, and `voice` (see Voice; `null` without voice) |
| `POST /api/v2/calls/{call_id}/join` | Join; the first join of a scheduled call starts it. Tico Meet returns `url`, `token` and `expires_in` (300); Google Meet records the open and returns `url` (the viewer's `join_url`) |
| `POST /api/v2/calls/{call_id}/token` | A fresh token while joined (Tico Meet only) |
| `POST /api/v2/calls/{call_id}/leave` | Leave |
| `PUT /api/v2/calls/{call_id}/stage` | Show your screen or a Tico file on the stage (in the call, Tico Meet). Body: `kind` (`screen` or `file`), `file_id` for a file you may read (`404` otherwise). Replaces whatever showed |
| `DELETE /api/v2/calls/{call_id}/stage` | Stop showing: the presenter's own, or the connector's for anyone's; anyone else's changes nothing |
| `POST /api/v2/calls/{call_id}/end` | End (the connector). Answers the call `ended`, or `ending` while speech already heard is transcribed. A scheduled call is cancelled (its booker or the thread's managers), its event deleted first |
| `PATCH /api/v2/calls/{call_id}` | Change a scheduled call's time. Body: `starts_at` and `ends_at` (as on the start). Its event changes first; see Scheduled calls |
| `GET /api/v2/calls/{call_id}/notes` | The call's own summary and action items (each `title` and `owner`), read from its items message under that message's rules; `pending` while still owed |
| `GET /api/v2/calls/{call_id}/bot-notes` | A bot's brief as `{text}`, newest additions last, up to 8,000 UTF-8 bytes. People who can read the call's thread name a bot they can see with `?bot=<id-or-slug>`. Attached bots read their own brief |
| `POST /api/v2/calls/{call_id}/bot-notes` | An attached bot appends to its own brief. Body: `{text}`. The oldest text falls out as the brief grows |
| `POST /api/v2/calls/{call_id}/ring` | Ring a person into a live call. Body: `actor`. Someone who reads the thread keeps their role; anyone else is added to it first. Answers `actor` and `added` |
| `POST /api/v2/calls/{call_id}/action-items/retry` | Ask again for action items the Gateway could not make (the connector, after the call ended) |
| `POST /api/v2/calls/{call_id}/bots` | Attach bots. Body: `bots`, 1 to 10 slugs; at most 10 in the call (`422 bot_limit`) |
| `DELETE /api/v2/calls/{call_id}/bots/{bot}` | Detach a bot |

The same operations are `tico call list|start|show|join|token|leave|end|retry` and `tico call bot add|remove` in the
`tico` CLI, and `tico_call_list`, `tico_call_show`, `tico_call_start`, `tico_call_join`, `tico_call_token`, `tico_call_leave`,
`tico_call_end`, `tico_call_retry`, `tico_call_bot_add` and `tico_call_bot_remove` over MCP. The OpenAPI operations are
`listCalls`, `startCall`, `listUpcomingCalls`, `listLiveCalls`, `getCall`, `changeCall`, `joinCall`, `refreshCallToken`, `leaveCall`,
`setCallStage`, `clearCallStage`, `endCall`, `retryCallActionItems`, `attachCallBots` and `detachCallBot`. The stage is
set from the meeting window only: it shows what a person in the call shares.
For a bot's brief, use `tico call notes --call <id>` to read and `tico call note --call <id> "<text>"` to append.
The MCP tools are `tico_call_notes` and `tico_call_note`. The corresponding OpenAPI operations are
`getCallBotNotes` and `appendCallBotNote`.

Changes are written to the change log on the `threads` topic: `call.scheduled`, `call.moved`, `call.prepped`,
`call.started`, `call.joined`, `call.left`,
`call.ended`, `call.stage`, `call.bot_attached`, `call.bot_detached`, `call.participant_removed`, `call.post_refused`,
`call.action_items_given_up`, `call.action_items_retry`, `call.updated` and `call.notes`. Notes changes carry the call
and thread IDs. Call writes other than `call.updated` and `call.notes` have an audit row. A refusal's event tells the thread's
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
- **Bot replies need a runner.** The bot's runner claims the call turn and posts its final reply.
