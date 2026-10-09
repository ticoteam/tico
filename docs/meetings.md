# Meetings

**Meetings** is where the team's meeting transcripts live. Tico does not record: humans already
have Granola, Zoom, Google Meet, Otter, Fireflies or Close for that. Tico takes what those tools
produce, keeps it in one list, and turns it into work. How the pieces fit:
[How Tico works](how-it-works.md).

A meeting is a title, when it started, who was in it, a transcript (a list of segments: speaker,
start, end, text), optional notes or a summary, and optionally a link to the recording that lives
in the tool that made it. Media files can be attached too.

## Getting a meeting in

| Door | For | How |
|---|---|---|
| **Add notes** on the Meetings page | a human with notes to write, a transcript file or a paste | title, date and time (default now), participants from the roster plus emails or names, markdown notes, and an optional bot to send them to. **Upload a file** (or drop one on the dialog) or **Paste** adds a transcript: a `.txt`, `.vtt`, `.srt` or `.json` file, with its source (`upload` by default) and an optional recording link. Files through this same API: notes alone as source `manual`, a transcript as the source you chose |
| `hub meeting import <file>` | a human or script on a computer | `hub meeting import call.vtt --title "Pricing call" --date 2026-09-28T16:00 --participant dana@example.com --source zoom --external-id 123` |
| `hub_meeting_import` (MCP) | a human's external agent | the same fields; the agent reads the file and sends the text |
| `POST /api/v2/meetings/import` | anything else | one JSON body, below |
| Close | Close calls and Notetaker meetings | the `close-calls` worker on a Mac or a Linux runner; see [Close](#close) |
| Zoom, Google Meet, Granola | the team's meeting tools | the `importers` job on an enrolled computer, turned on in **Tools > Meeting importers**; see [Meeting importers](#meeting-importers) |

The CLI and the API act as the human whose credential they carry: set `HUB_API_URL` and `HUB_TOKEN` to
a personal token (see [Who can do what](#who-can-do-what)). All of them land in the same place, in the same shape, so the list, search, sharing, Send and
action items work identically whatever the source.

## Review before sharing

Imported meetings wait in the importing person's **Pending** queue by default. Only the person
they are filed for can open that queue or review a meeting, including through their own BotOps
acting with their rights. The Team owner, participants, other people and bots cannot see pending
or dismissed meetings. They do not appear in search, SQL exports, routines, Updates or KPIs.
Pending is quiet: it does not add to **Needs you**. Existing meetings stay live after upgrade.
In Meetings, **Shared** keeps the existing live list; **Pending** and **Dismissed** show only
meetings filed for you. The page opens on Pending while it has meetings. Open a pending meeting to read it
and choose Team or Private and the bot to send it to before sharing, or use the row's **Share & send** to keep
its source privacy and send it to your Assistant. Sharing alone files no tasks: the bot a meeting is sent to
does, and the row shows **Processing** while it works and then what it created. Tick two or more rows (or
the select-all box) for Share and Dismiss on all of them; they apply only to ticked meetings shown by the current
search and filters. Hidden meetings are never included, even if previously selected. Bulk sharing
keeps each meeting's source privacy. Restore in Dismissed
returns a meeting to Pending. **Settings** controls future imports and, for the owner, the Team default.
Human **Add notes** stays live, including a pasted or uploaded transcript; a human API caller
can also choose `review: "live"`. Computer importers keep using the same payloads: the server
decides the review state for the person named in `owner_email`.

`GET /api/v2/meetings?review=pending` returns `{"meetings": [...], "count": N, "pending_count": N}`.
The default list is live; `review=dismissed` reads the person's dismissed meetings. Every listed
meeting has `review_state` (`pending`, `live` or `dismissed`). The rail's existing
`GET /api/v2/updates/unread` also returns `meetings_pending`, scoped to the caller.
`GET /api/v2/meetings/{id}` opens a meeting under the same access rules as the older detail route.

- `POST /api/v2/meetings/{id}/review {"action": "approve", "private": false}` shares a pending meeting.
  `send_to` (a bot slug or `auto`) also hands it to that bot once shared, as Send does.
  Omit `private` to keep its source default: Granola is private by default; other sources are team
  meetings unless their importer chose private. A team meeting with a transcript fires
  `meeting.ready` and the older `recording.ready` once. Repeating approval does not fire again.
- The same route with `{"action": "dismiss"}` hides a pending meeting from Pending. Live meetings cannot be
  dismissed; existing deletion controls remain available. Later syncs may update its
  contents but keep it dismissed under the same dedup key. `{"action": "restore"}` brings a
  dismissed meeting back to Pending. Re-import never shares a pending or dismissed meeting,
  and source syncs preserve the privacy chosen at approval.
- `POST /api/v2/meetings/review {"action": "approve_all"}` shares the caller's pending queue;
  `dismiss_all` dismisses it. Optional `ids` selects meetings filed for that caller; an empty
  list does nothing. Batch sharing keeps each source's privacy. A selection containing someone
  else's meeting is refused as a whole. The answer has `meetings`, `count` and `pending_count`.
  Invalid transitions refuse the entire batch. Repeated approval of live meetings, dismissal of
  dismissed meetings and restore of pending meetings are harmless and never repeat ready events.

`GET /api/v2/meetings/settings` returns `auto_share` (the person's choice, or `null` when unset),
`review_default` (`review` or `auto`) and `effective_auto_share` (a boolean).
`POST /api/v2/meetings/settings {"auto_share": true}` shares future imports automatically;
`false` always requires review, and `null` clears the person's choice to use the Team default.
The same preference is available at `GET`/`POST /api/v2/preferences/meetings.auto_share`, using
the existing `{"value": true|false}` body. Only the owner can set the Team default through
`POST /api/v2/meetings/settings {"review_default": "auto"}`; its initial value is `review`.
A person's choice wins. Turning auto-share on leaves their existing queue pending: tick the meetings shown
and Share them, or the explicit whole-queue CLI/API action.

CLI: `hub meeting pending`, `hub meeting approve <id>` or `hub meeting approve --all`,
`hub meeting dismiss <id>`, and `hub meeting restore <id>`. MCP equivalents are
`hub_meeting_pending`, `hub_meeting_approve` (`id` or `all: true`), `hub_meeting_dismiss`, and
`hub_meeting_restore`. All use the caller's own meetings; BotOps uses the requesting person's
rights. `hub meeting import ... --review live` shares immediately. `send_to` on a pending import
is held until approval; direct Send and pushing items require a live meeting.


## The import API

`POST /api/v2/meetings/import`, JSON (or `multipart/form-data`, with the media files as `files` parts
and `participants` and `context` as JSON-encoded text fields). Only a transcript or notes is required.

| Field | |
|---|---|
| `title` | What the meeting was, up to 300 characters. Default on a new meeting: "Imported meeting". On an update, empty leaves the title alone |
| `started_at` | ISO-8601 **with a timezone**. Default: now |
| `duration_seconds` | Default: the last segment's end |
| `participants` | Up to 100 entries: an email, a name, or `{"name", "email"}`. Each is linked to the roster human it matches (by email, else by exact name); other participants are kept as written |
| `source` | Where it came from, a short lower-case name: `zoom`, `google-meet`, `granola`, `otter`, `fireflies`, `upload`, `api` (default `api`). `close` is reserved for the Close worker |
| `external_id` | That system's id for the meeting, up to 200 characters of `A-Za-z0-9_.:@/-`. The idempotency key: see below. Omit it and every call makes a new meeting |
| `transcript` | Plain text, WebVTT, SRT or JSON segments, as a string, or a list of segment objects |
| `format` | `auto` (default), `text`, `vtt`, `srt` or `json`. Auto is right unless the file is unusual |
| `notes` | Notes or a summary in Markdown, up to 200,000 characters. The source's own summary goes here |
| `media_url` | An `https` link to the recording in the source |
| `context` | A flat map of up to 20 short text fields the source wants kept (`lead_url`, `room`); keys are lower-case identifiers and `*_url` values must be `https` |
| `private` | `true` narrows the meeting to its participants and its owner. Default: private for Granola; otherwise a team meeting, readable by everyone signed in after sharing |
| `review` | `live` shares immediately when a human or their personal token imports. Otherwise the person's review setting applies; `pending` explicitly keeps it in their queue. Computers cannot request `live` |
| `send_to` | A bot slug. Hands a live meeting to that bot as a task, exactly as **Send** does; a pending meeting waits until approval |
| `owner_email` | Computers only: the roster human the meeting is filed for |

A transcript is at most 1,000,000 characters and 10,000 segments. A file attached by multipart is at
most 10 MB, ten to a request, and the request at most 20 MB. Unknown fields are refused, so a typo
never silently changes what is stored.

The answer:

```json
{"id": "20260928-161200-a3f1", "title": "Pricing call", "kind": "meeting", "status": "done",
 "turns": 42, "review_state": "pending", "existing": false, "changed": true, "link": "#/meetings?meeting=20260928-161200-a3f1"}
```

`existing` says the (source, `external_id`) already had a meeting; `changed` says something in the
body differed from what was stored. A meeting somebody deleted answers
`{"status": "deleted", "existing": true, "changed": false}` and is not brought back, so an importer
that keeps offering it stops there. `422` says which field is wrong and why.


```sh
curl -X POST "$HUB_API_URL/api/v2/meetings/import" \
  -H "Authorization: Bearer $HUB_TOKEN" -H "Content-Type: application/json" \
  -d '{"title": "Pricing call", "source": "zoom", "external_id": "883-201-0042",
       "started_at": "2026-09-28T16:00:00-07:00", "participants": ["dana@example.com", "Ben"],
       "format": "vtt", "transcript": "WEBVTT\n\n00:00:05.000 --> 00:00:08.500\n<v Dana>Can you send pricing?</v>\n"}'
```

### Idempotency: re-import updates, never duplicates

The key is (source, `external_id`, the human the meeting is filed for). Send the same three again and
you get the same meeting: if the body is identical nothing happens (`changed: false`); if the
transcript, notes, participants, title, time or link differ, the meeting is updated in place and the
previous state stays in its version history (`GET /api/meetings/{id}/versions`). Two humans who both
import the same Zoom meeting each get their own copy, and the same human importing from two
sources gets two. Files sent again with the same name and size are not attached twice.

### Transcript formats

Every format becomes the same segment list, `{speaker, start_ms, end_ms, text}`, in file order
(`clients/transcript_formats.py`, the parser the API, the CLI and the tests share).

- **Plain text**: one line per turn. A leading `[mm:ss]`, `[h:mm:ss]` or `(mm:ss)` is the turn's time,
  and `Name: text` gives the speaker. A line without a time takes the previous one's.
  ```
  [00:05] Dana: Can you send pricing?
  [01:10] Ben: Ten percent, then.
  ```
- **WebVTT**: cues with `-->` times. The speaker is a `<v Name>` voice tag or a leading `Name: `
  (Zoom and Teams write the latter). `NOTE`, `STYLE` and `REGION` blocks are skipped.
- **SRT**: numbered blocks with `00:00:05,000 --> 00:00:08,500`; the same `Name: ` convention.
- **JSON**: a list of `{speaker, start, end, text}`, or an object holding one under `segments`,
  `transcript`, `utterances` or `results`. `start` and `end` are **seconds** (a number, or
  `"mm:ss"` / `"h:mm:ss.fff"`); `start_ms` and `end_ms` are milliseconds. `speaker` may also be
  `speaker_name`, `name`, or `{"name": ...}`.

A transcript with nothing readable in it, a time line missing from an SRT block, or an unknown
format is a `422` that names the problem.

## Who can do what

- **A human** (the owner or anyone on the roster) imports meetings of their own with their own
  credential, from the page or with a personal token. **Connect an external agent** makes a token
  with that human's own rights; members can make one by default unless the owner switches off member tokens. The meeting is theirs: only they and the team owner edit, delete or
  Send it.
- **A computer** that runs an importer (a registered runner whose owner is in
  `TICO_PROCESSING_OPERATORS`, else the owner's) files meetings for a roster human it names in
  `owner_email`. It cannot file for someone who is not on the roster.
- **A bot** cannot import. Bots read: `hub_meeting_search` and `hub_meeting_read` give them
  team meetings that are not private, never personal notes or private meetings.
- **Reading**: a team meeting is readable by everyone signed in. A private one is readable by its
  owner, its participants (matched by email, ignoring case) and the team owner.
- `send_to` applies the ordinary Send rule: the caller must be allowed to hand that bot work.

## What a meeting turns into

- **Send to a bot** (the button, `POST /api/meetings/{id}/send`, or `send_to` on import): one Tico task
  for the bot, carrying the notes, the readable transcript and the meeting context. Sending is once;
  asking again for the same bot returns the same task.
- **Action items.** Beside each meeting are three lists: **Doc updates**, **Tasks** and **Feature
  requests**. A human adds items (with the quote and time they came from if they like) and the
  meeting's owner pushes them. Push creates ordinary Tico tasks, never a card in another tool: a
  task item becomes a task for its named owner; a doc update becomes a task for `doc-updater`; a
  feature request becomes a task for whoever is primary for the product group. Product area, app and label
  are optional: use your team's names or leave them blank. Existing B/F and CA/PA codes still work.
  A near-identical open task returns a link (**Push anyway** overrides).
  Every pushed task ends with a footer naming the meeting and quoting the line.
- **Comments.** Anyone who can open the meeting can add to the thread beside it.
- **Routines.** `meeting.ready` fires when a team meeting with a transcript is first imported,
  once per meeting however often it is re-imported; a routine written `on: meeting.ready` gets one
  task carrying the notes and transcript. A private meeting fires nothing. The older name
  `recording.ready` is still emitted for routines written before the rename; new routines use
  `meeting.ready`. Close imports that arrive live do not fire the event; approving a pending
  Close meeting fires it under the same transcript and privacy rules.
- **Search.** `hub meeting search "pricing" --person Dana --since 2026-09-01` and
  `hub meeting read <id>` (the old `/api/v2/recordings/*` paths still answer for installed
  clients).

## Close

On a Linux runner nothing is installed: the job starts when `secrets/close-calls.env` (`CLOSE_API_KEY=<key>`, mode
600) exists in the runner's `workspace/secrets/` folder, and stops when it is removed. Keep the key on one computer.

The `python -m runner close-calls` job (on a Mac, or a Linux runner) polls completed Close call and Notetaker meeting activities
every five minutes and imports their timed speaker turns and Close's text summary through
`POST /api/v2/imports/transcripts`. That door is the Close worker's own: it keeps each provider
revision, joins a call to the Notetaker meeting it belongs to, and follows Close's corrections until a
meeting has been sent or has items. It does not fetch, upload or store Close audio. A Close activity
without a transcript is revisited, because transcripts may arrive after completion. Close Call Assistant
must already be enabled in the Close account for ordinary call transcripts to exist; Tico does not enable
it. A Notetaker meeting transcript is available only after the meeting concludes. See
[Close tool](../integrations/close-crm.md).

Close meetings filed for a roster person use that person's review setting too. A team-wide
Close import with no person (`owner_email` omitted or empty on the transcript API) stays live,
filed under the Team owner. Older Close computers already send a filed-for person, so their
imports use that person's queue without a computer update.

The first pull covers 30 days of Close activity creation. Close's organization-wide activity API does
not allow filtering by meeting time, so the worker revisits pending future meetings until their
transcripts arrive. To scan meetings created before that window, stop the `close-calls` service and
run `python -m runner close-calls --backfill-days DAYS` from the enrolled computer, with `DAYS` between 1
and 365; it makes one bounded pass, then exits. Restart the service afterward. On a Linux runner there is no
service to stop: move `secrets/close-calls.env` aside so the runner stops the job, run the backfill, then put it back. The worker stops with an
error if a single day exceeds its 5,000-activity page limit, so narrow the window and retry.

The **Add source** menu beside Settings on the Meetings page lists Close and each importer below, with the worker's
heartbeat in one word: **Connect** until the worker connects, **Connected** (with the last import) after a recent
successful pull, **Delayed** when its heartbeat is stale, **Error** after a failed pull (a red dot on the menu).
With no meetings and nothing connected, the page shows them as large tiles, with **Add notes**. An entry opens that source's setup: Close its tool page,
the others a dialog with the same form as Tools > Meeting importers (owners only).

## Meeting importers

Zoom, Google Meet and Granola each have an importer. They run as one job, `python -m runner
importers`, on an enrolled computer (`scripts/tico install importers` on a Mac; a Linux runner (Docker) starts it by itself once an importer is assigned to it; the Sources strip on the
Meetings page shows each one's health). Each importer:

- reads its tool's official API and files what it finds through `POST /api/v2/meetings/import`, so a
  meeting from any of them is the same object as an upload;
- keeps its credential **on that computer**, in `secrets/<tool>.env` under the runner's projects
  folder (the same place as Close's `secrets/close-calls.env`, mode 600). The credential never reaches
  the server or the browser, and a failure is reported as a short code, never with a URL, header or
  provider message;
- is idempotent. The key is (source, the tool's own meeting id, the human it is filed for); a
  meeting that changes is updated in place, an unchanged one is not sent again, and one somebody
  deleted stays deleted;
- reads a bounded window: the first pass covers the last 30 days, then each pass re-reads the last
  72 hours (summaries and transcripts arrive late) and moves on. A meeting still being transcribed
  keeps the window from passing it for up to seven days. For older history, stop the job and run
  `python -m runner importers --backfill-days DAYS [--only zoom|google-meet|granola]`
  (1 to 365; Google Meet keeps only 30 days, so it is capped there); it makes one pass and exits;
- files a meeting for the roster human it belongs to. A human who is not on the roster is filed
  under the team owner, except Granola, which skips notes it cannot place, because those are
  someone else's private notes.

**Turn one on.** As the owner open Tools > Meeting importers, tick **Enabled**,
choose the computer that holds the credential file, and **Save**. The card shows the last sync, the
last import, how many meetings it has filed and the last error. Only computers whose owner may run
importers (`TICO_PROCESSING_OPERATORS`, else the owner) are offered. `python -m runner
importers-doctor` on the computer says which credential files are present, without calling any tool.

**On a Linux runner** there is nothing to install: the runner starts the job while an importer is assigned to it and
stops it when none is. Put the credential file in the runner's `workspace/secrets/` folder (`/home/runner/workspace/secrets/` in the Docker image), mode 600, owned by
the runner user, then assign the importer to that computer. `Tico side jobs:` lines in `docker logs` show it starting. A Google Meet service-account file goes in the same folder.

Options every importer's file accepts: `<TOOL>_PRIVATE=1` files its meetings as private (readable by
their participants and the owner) and `<TOOL>_PRIVATE=0` files them for the team. The default is
team-readable except Granola, which defaults to private. A credential can also be set in the job's
environment (for example `GRANOLA_API_KEY`), which wins over the file.

Errors the card can show: `missing_credentials` (the file or a key is missing), `auth_failed` (the tool
refused it), `forbidden` (a scope or plan feature is missing), `rate_limited` (the importer retries on
its own), `unreachable`, `provider_error`, `bad_response`, `hub_rejected` and `sync_error`.

What was checked against the providers' published documentation and what was not is in each section's
"Verified" note. None of it has run against a live account in this repository: the tests use
recorded-shape fixtures and no network.

### Fireflies

Fireflies is no longer available as an importer. Existing Fireflies meetings keep their source
label, transcripts, notes, attachments and recording links, and remain searchable and readable.
Saved importer settings and local credential files are retained, but the server no longer assigns
Fireflies to a computer and an updated runner does not run it. An older settings page can still
switch it off; enabling it returns an unavailable message.

### Zoom

Cloud recordings through a **Server-to-Server OAuth** app. It lists each user's cloud recordings, downloads
the **TRANSCRIPT** (WebVTT) file of each meeting, and files it for the meeting's host. Audio and video are
never downloaded.

1. In the [Zoom App Marketplace](https://marketplace.zoom.us) choose **Develop > Build App >
   Server-to-Server OAuth App**, name it (for example "Tico meetings") and note the **Account ID**,
   **Client ID** and **Client secret**.
2. Under **Scopes** add (granular scopes, admin level):
   - `cloud_recording:read:list_user_recordings:admin` (list a user's recordings)
   - `cloud_recording:read:list_recording_files:admin` (recording files; add it if Zoom refuses the transcript download)
   - `user:read:list_users:admin` (which users to read)
   - `meeting:read:list_past_participants:admin` (attendees; optional, without it the participants come
     from the transcript's speaker names)
   Zoom's classic scopes `recording:read:admin`, `user:read:admin` and `meeting:read:admin` also work.
3. **Activate** the app.
4. Turn on **Cloud recording** and its **Audio transcript** setting for the account (Zoom Pro or higher);
   only meetings that have a transcript file are imported.
5. On the computer, create `secrets/zoom.env`:
   ```
   ZOOM_ACCOUNT_ID=...
   ZOOM_CLIENT_ID=...
   ZOOM_CLIENT_SECRET=...
   # optional: read only these hosts
   ZOOM_USERS=ana@example.com,ben@example.com
   ```
6. Enable **Zoom** in Settings and choose that computer.

The token comes from `POST https://zoom.us/oauth/token` (`grant_type=account_credentials`, Basic auth) and
lasts an hour; there is no refresh token, so a new one is requested. Recordings are listed a week at a time
(`/users/{userId}/recordings`, at most 300 per page, Zoom allows a month per request). The transcript is
downloaded with the token as a Bearer header, only from `zoom.us` and `zoomgov.com` hosts, and redirects
are followed only inside them. The external id is the meeting's UUID, with base64 characters mapped
(`+` to `-`, `=` dropped) to fit Tico's id alphabet. The host is the meeting's owner, so only meetings a
user hosts appear under that user.
*Verified from Zoom's published OpenAPI (developers.zoom.us/api-hub):* the token request, `/users`,
`/users/{userId}/recordings` (parameters, `next_page_token`, `recording_files[].file_type` = `TRANSCRIPT`,
`download_url`), `/past_meetings/{meetingId}/participants` (including double-encoding a UUID that starts
with `/` or holds `//`), and the scopes above.

### Google Meet

The Google Meet REST API v2 (`conferenceRecords`, `participants`, `transcripts`, `transcripts.entries`).
Authentication is a **Google Workspace service account with domain-wide delegation**, impersonating each
human whose meetings should be imported. Google keeps a conference record for 30 days after the meeting
ends, so the importer never looks back further than that; leave it running.

1. In the Google Cloud console create (or pick) a project, **enable the Google Meet REST API**, and create
   a **service account** (no roles needed). Under **Keys** add a JSON key and download it.
2. Note the service account's **Unique ID** (its numeric OAuth client ID).
3. In the Google **Admin console** (a super administrator) go to **Security > Access and data control >
   API controls > Domain-wide delegation > Add new**. Enter the client ID and this one scope:
   `https://www.googleapis.com/auth/meetings.space.readonly`
4. Meet must record transcripts: **Admin console > Apps > Google Workspace > Google Meet > Meet video
   settings**, turn on **Recording** and **Transcripts** for the humans involved (a Workspace edition
   that includes them). Transcript files are created only for meetings where somebody turned them on.
5. Copy the key to the computer, for example `secrets/google-meet-key.json` (mode 600), and create
   `secrets/google-meet.env`:
   ```
   GOOGLE_MEET_USERS=ana@example.com,ben@example.com
   GOOGLE_SERVICE_ACCOUNT_FILE=google-meet-key.json
   ```
   A relative file name is read from the same `secrets` folder. `GOOGLE_SERVICE_ACCOUNT_JSON` can hold the
   key inline instead.
6. Enable **Google Meet** in Settings and choose that computer.

For each listed user the importer signs an RS256 assertion with the key (`sub` is that user, scope as
above, audience `https://oauth2.googleapis.com/token`), exchanges it for an access token, lists the
conference records that user can see for the window (`filter` on `start_time`), and for each ended
meeting with a generated transcript reads its participants and transcript entries. Speaker names come
from the participants' display names; entries by one speaker are joined into turns. The meeting is filed
for the impersonated user, so two listed users who were in the same meeting each get a copy, as with
every source. The title is "Google Meet" plus the meeting code, since the API has no meeting title; the
recording link is the Google Docs transcript and the meeting link is kept as context. **Limits worth
knowing:** the API returns display names, not emails, for signed-in participants, so participants are
matched to the roster by exact name; and the entries can differ slightly from the Docs transcript
(Google says so). Errors mean either the key or the delegation is wrong: `auth_failed` from the token
exchange usually means the client ID or the scope in step 3 is missing.
*Verified from Google's Discovery document for `meet.googleapis.com` v2 and the Meet API docs:* every
resource and method used, the page-size maximum of 100, the filter fields, the transcript states
(`FILE_GENERATED` is the only one with content), `docsDestination.exportUri`, and the 30-day record
expiry. The service-account token exchange is Google's standard JWT-bearer flow; it is fixture-tested
here but not run against a Workspace.

### Granola

**Connect your own Granola account in Meetings (default).** Choose **Connect Granola** (in Meetings, Add source, or Tools > Granola), open the
verification link and enter the code if asked. Each person connects their own account through
Granola's official remote MCP at `https://mcp.granola.ai/mcp`. Tico stores tokens encrypted in the
server credential vault; bots and Computers cannot read them. Disconnect in Meetings to delete them.

The free plan imports your own notes and AI summaries from the last 30 days, without transcripts or
folders. Paid plans can also import transcripts when Granola permits them. Private notes typed by
the note-taker are never imported. Imported meetings default to private. Tico syncs in the background
every 25 minutes and when you open Meetings, reusing a sync from the last two minutes. Recent meetings
are revisited for late summaries. Only an invalid or rejected OAuth grant requires sign-in again.
Network failures, rate limits and server outages keep your connection and retry on the next schedule
with backoff. Meetings and your Health page say
**Granola needs sign-in again** when the grant is rejected; connect again to continue.
Transcript failures still import shared notes. Unmappable meetings are skipped, counted in status,
and do not block later notes. Transcript access is checked again on each sync after a plan change.
The plan hint uses account details when available, or stays free until a transcript is successfully
read; an advertised transcript tool alone does not indicate a paid plan. Account details are fetched
once after a successful read, or omitted when unsupported or malformed. A transient failure can retry
on the next sync; rate limits and sign-in failures pause the import. A paid-tier denial stops further
transcript requests for that sync.
Granola's XML-like responses accept introductory text, bare participant emails and markdown containing `<` and `&`;
shared summaries retain their markdown. Dates such as `Feb 4, 2026 7:30 PM` and `Feb 4, 2026` are
treated as UTC. Unknown dates do not prevent importing a meeting's notes.
MCP calls share an install-wide pace of at most 60 per minute, and scheduled starts are staggered after restart.
OAuth calls have a separate paced queue so Connect and sign-in polling can finish while imports run.
Notes are fetched in batches of up to ten, at least six seconds apart per connection, including
individual requests used to recover from a failed batch. The first note fetch of a sync waits until ten
seconds have passed since its previous MCP call. When a failed batch's first two individual requests fail
with the same code, the rest of that batch is counted as skipped with that code without further requests.
A rate-limited note fetch is retried up to twice in the sync, after 20 and then 60 seconds (or Granola's
`Retry-After` when longer), unless Granola names a `Retry-After` over 60 seconds. If the limit
persists, the sync stops with `rate_limited: get_meetings`, retains its checkpoint and does not count
the blocked meetings as skipped. Each attempt resets its skipped count. A rate-limited sync retries
in about five minutes, or later when Granola supplies a longer `Retry-After`, and resumes from that
checkpoint. The first free-plan
sync requests `last_30_days` when supported; otherwise it uses a custom date range.
Disconnect cancels an active sync and removes the stored token promptly; revocation is attempted in
background when Granola advertises a trusted endpoint. Connections are removed when a person leaves
or loses sign-in. A pending reconnect retains the working token until success or expiry; a successful
new sign-in starts a fresh 30-day backfill.

`hub meetings granola status` shows your connection and `hub meetings granola sync` starts a background
sync. `POST /api/v2/meetings/granola/sync` returns `state: "syncing"` when a job is running
(or `recent`, `off`, `needs_signin`). `GET /api/v2/meetings/granola` includes `syncing: bool`
and `skipped` for the last attempt. The account email comes only from display claims supplied by
Granola's token endpoint (including userinfo or ID-token claims), unless marked unverified, or from
its account information tool, and is null when absent. These details never change the person's
Tico rights; the ID token is not retained.
Sync failures show and log a fixed code with the failed step, such as `bad_response: list_meetings`,
without provider content or tokens. Skipped notes also name the step while retaining their count.
Status also includes `skip_reasons`, the last attempt's skipped count per code (for example
`{"provider_error: get_meetings": 9}`), and `last_error_detail` for the last failure or skip: `step`,
`http_status`, the JSON-RPC `rpc_code`, `tool_error` (an MCP `isError` result), `retry_after` in seconds,
the fixed `signal` that classified a throttle (`rate limit`, `slow down` or `too many requests`) and the
`batch` size. It never holds Granola's text, note IDs or titles.
The MCP tools `hub_meeting_granola_status` and `hub_meeting_granola_sync` use the caller's person
rights; BotOps can give the Meetings link but cannot complete the browser sign-in.

The account sync matches existing meetings by person and Granola ID, then by the Granola web URL
when the IDs differ, and fills only empty fields. Summaries written by the account sync can update
when Granola regenerates them, provided the stored summary still matches the last source version.
Human logs, edited summaries, API-imported notes, existing titles, calendar times, attendees,
privacy settings and transcripts are preserved. Older summaries without recorded source ownership
are kept as-is.

**Granola API key (Business/Enterprise)** remains an alternative for a Computer importer. Both
connections use the same source. Account sync deduplicates by external meeting ID or the shared
Granola web URL, so an existing meeting keeps its richer API-imported fields.

The API-key importer uses Granola's official public API. Nothing reads Granola's local cache or
app files, and nothing needs to run on the Mac where Granola is installed.

1. In Granola (Business or Enterprise plan) open **Settings > Workspaces > API** and **Generate API Key**.
   A **personal** key (any Business member can make one) reads that human's notes and what is shared
   with them; a **workspace** key (an admin makes it) reads notes the workspace made public. On
   Enterprise, admins control who may create keys.
2. On the computer that runs the importer, create `secrets/granola.env`:
   ```
   GRANOLA_API_KEY=grn_...
   ```
   A comma-separated list takes several humans' personal keys.
3. Enable **Granola API key (Business/Enterprise)** in Tools > Meeting importers and choose that Computer.

The importer lists notes (`GET https://public-api.granola.ai/v1/notes`, `created_after` and
`created_before`, `page_size` at most 30, cursor pagination), then reads each note with its transcript
(`include=transcript`, falling back to the paged `/transcript` endpoint when Granola answers `413`). It
files the title, the calendar time, attendees and invitees, the Granola summary as notes, the web link,
and the transcript, with `microphone`/`speaker` lines attributed to the note's owner and to others when
Granola gives no speaker name. Granola returns only notes that have a summary and transcript, and
allows about five calls a second, which the importer stays under. **Private notes typed by the
note-taker are never imported.** Meetings default to private (readable by their participants and the
owner); put `GRANOLA_PRIVATE=0` in the file to file them for the team. A note is filed for its owner
if that human is on the roster, and skipped otherwise. Because the list is read by creation date,
edits to a note more than three days old are not picked up unless it is re-read with `--backfill-days`.
*Verified from Granola's published OpenAPI (docs.granola.ai/api-reference/openapi.json) and API
changelog:* the base URL, Bearer `grn_` keys, the three endpoints and their parameters, the note,
attendee, calendar-event and transcript shapes, and the `413` behaviour.

### Otter and others

Otter has no importer here. Use `hub meeting import`, the API, or write one as below.

## Writing an importer

Each of the importers above is a small module that reads one tool's API, maps it onto the fields above,
and posts it; nothing on the Tico side changes when one is added. `runner/importers/base.py` is the
shared shape: subclass `Importer`, say how to list credentials (`scopes`) and meetings (`fetch`), and it
supplies the window, cursor, idempotent filing, roster fallback and status reporting. Add it to
`runner/importers/__init__.py` and to `IMPORTERS` in `backend/meeting_importers.py` to get a card in
Settings. The steps below are the same whether it is a module or a standalone script.

1. **Pick the door.**
   - *Runs on a human's computer or in their external agent*: post to `/api/v2/meetings/import` with that
     human's own credential (a personal token). No `owner_email`; the meeting is theirs.
   - *Runs for the team on a computer that is enrolled as a runner*, as the Close worker and the
     importers above do: post to the same route with the runner's credential and `owner_email` for
     each meeting. See `runner/importers/base.py` (or `runner/close_calls.py`) for the shape (a poll
     loop, a cursor in the runner's state database, a heartbeat, `CodeWatch` to restart on new code)
     and `clients/tico.py` for the client.
2. **Choose a `source` name** (`granola`, `zoom`...). It is a label on the meeting, a filter on
   the page and part of the idempotency key. Do not reuse another tool's name.
3. **Choose `external_id` as the tool's own stable id** for the meeting (Zoom's meeting UUID, the
   Granola note id). Never a timestamp or a hash of the text, or an edited transcript will
   arrive as a second meeting.
4. **Map the fields.**

   | Tool gives you | Send |
   |---|---|
   | title, topic | `title` |
   | start time (convert to an offset, not local time) | `started_at` |
   | duration | `duration_seconds` |
   | attendees (emails when the tool has them) | `participants` |
   | speaker-labelled transcript | `transcript`: pass the tool's JSON as segments, or its VTT/SRT file as text; send `format` if you know it |
   | AI summary or notes | `notes` |
   | share link to the recording | `media_url` (`https` only) |
   | anything else worth keeping | `context` (flat text, no payloads) |
   | audio or video file (small) | multipart `files` |

5. **Send nothing you should not.** Send `private: true` for a meeting the tool marks private; leave
   audio, tokens and provider payloads out of `context`.
6. **Be re-runnable.** Poll from a cursor, post each meeting once it is finished (a transcript that is
   still being written is not yet a meeting), and post it again whenever the tool says it changed:
   the API turns identical bodies into `changed: false` and changed bodies into an update.
7. **Handle the answers.** `deleted` means stop offering that meeting; `422` is a bug in the mapping
   (the message names the field); `403` and `404` are credential or roster problems, not something
   to retry.
8. **Test it without the network**: feed a recorded API response through your mapper and check the
   body with `MeetingImport.model_validate(body)` (`backend/imports.py`), or post it to a test Tico
   as `backend/tests/test_meetings_import.py` does.

A minimal importer, for a tool that hands back VTT:

```python
import json, os, urllib.request

def file_meeting(meeting, vtt):
    body = {"title": meeting["topic"], "source": "zoom", "external_id": meeting["uuid"],
            "started_at": meeting["start_time"], "duration_seconds": meeting["duration"] * 60,
            "participants": [p["email"] for p in meeting["participants"] if p.get("email")],
            "media_url": meeting.get("share_url", ""), "format": "vtt", "transcript": vtt}
    request = urllib.request.Request(
        os.environ["HUB_API_URL"] + "/api/v2/meetings/import", json.dumps(body).encode(),
        {"Authorization": "Bearer " + os.environ["HUB_TOKEN"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.load(response)
```

## Upgrading from Recordings

Tico used to record meetings itself. That is gone: no browser or desktop capture, no live captions,
no live meeting brain, no processing worker on the Mac, no xAI or Gemini keys on the server. What
stays is everything that was ever imported or recorded. An upgrading install keeps all of it:

- The tables are unchanged (`meetings`, `meeting_versions`, `meeting_items`, `meeting_comments`,
  `meeting_deliveries`, `recording_source_refs`, `recording_transcripts`, `import_refs`,
  `media_assets` and the rest), so no migration rewrites data. The empty `meeting_brain` table is left in place.
- A meeting that was still waiting on audio or transcription when the server updates is marked
  finished with what it has, once, at startup. Stored audio stays in file storage and remains
  attached to its meeting.
- Routes moved from `/api/recordings/...` to `/api/meetings/...` and the page from `#/recordings`
  to `#/meetings` (old links redirect). `hub recordings ...` became `hub meeting ...` and the
  `hub_recordings_*` tools `hub_meeting_*`; `hub_recordings_submit` and `POST /api/v2/recordings`
  are replaced by the import API. `/api/v2/recordings/search` and `/transcript` still answer.
- `recording.ready` is still emitted beside `meeting.ready`.
- A Mac set up earlier has a processing job (`team.tico.tico-processing`); `scripts/tico install`,
  `restart` and `uninstall` remove it.
- The desktop app no longer captures audio or shows a meeting prompt; it is Tico in a window
  with a tray item. It no longer asks for microphone or system-audio permission.
- `TICO_GEMINI_SECRET_ARN` and `TICO_XAI_SECRET_ARN` are no longer read;
  `TICO_PROCESSING_OPERATORS` still names the computers that may run importers and calendar publishers.
