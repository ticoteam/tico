# Files

A bot's page lists **Files** in its right rail: the useful things it created, revised or delivered, newest
activity first, as a plain list of names. Three rows show by default; a small "+N" opens the rest in
place. A bot with no files has no Files section. A stored file opens in the app's viewer (a CSV as a table, its first 1,000 rows, with Download);
a linked document opens at its provider in a new tab.

## What gets listed

A file is something the bot makes and hands over: a report, a draft, a spreadsheet, an export, a
Google Doc it wrote. Attachments humans send a bot are its inputs and are **not** listed unless the
bot itself publishes or changes them. A git checkout is a workspace, not a file store: only what the
bot publishes appears.

One row is one file. Editing it does not add rows: the row keeps its title, gains a version and an
activity entry, and moves to the top. A row is identified by the bot, where it came from (its task,
its conversation, or bot-wide) and a canonical identity:

| Identity | Meaning |
|---|---|
| `blob-series:<id>` | bytes published from a path, one series per path |
| `google-drive:<file id>` | a Google Doc, Sheet, Slides or Drive file, whatever its URL form |
| `url:<address>` | any other https document, without fragment or tracking parameters |
| `s3:<bucket>/<key>` | an S3 object the bot's computer copied |

## The three kinds

1. **Stored files.** The bytes are in Tico's private blob store (content-addressed, backed up,
   never overwritten). Tico authorizes each open: `GET /api/v2/files/{id}` for the latest version
   and `/api/v2/files/{id}/versions/{n}` for an older one, in the same safe viewer or download as
   any attachment. Adopted legacy attachment IDs keep their original bytes without a version;
   current file lists include an explicit version in their links to those IDs. Tico streams
   authorized bytes; storage addresses are never shown.
2. **Cloud documents.** A Google Doc, Sheet or Slides, a Notion page, a Figma file or any https
   document. `hub file link <url> --title "..."` registers the address (http, `javascript:`,
   credentials in the URL and private-network hosts are refused). Open goes to the provider, which
   decides who may read it; the row says "Link opens in Google (requires access)". Tico never
   fetches, proxies or copies the document and never holds a provider token. Each time the bot says
   it edited the document (`hub file touch <id|url>`, or add-link again) the row moves to the top.
3. **S3 objects.** `hub file import s3://bucket/key [--title ...] [--task ...]` runs on the bot's
   computer. It copies the object with the credentials that computer already has (the bot's credentials
   or its AWS environment; boto3, or the aws CLI when boto3 is missing), refuses a type or size
   outside the limits, and uploads the bytes to Tico's store. The object's ETag (and version id) is
   recorded; importing again after the object changed adds a version, importing the same ETag adds
   only an activity entry. Tico never reads a team bucket with its own credentials.

### Links to bucket objects

A raw `s3://bucket/key` opens nothing in a browser. `TICO_S3_VIEW_URLS` maps a bucket to an https base people can
open, comma-separated as `bucket=https://base[/prefix]`, for example
`acme-files=https://d1234example.cloudfront.net`: put a CloudFront distribution in front of the bucket (the bucket as
its origin, with origin access control) and name the distribution's address here. `s3://acme-files/reports/q3.pdf`
then becomes `https://d1234example.cloudfront.net/reports/q3.pdf`, each path segment URL-encoded. Tico shows such
links in task bodies, comments and chat (images as thumbnails, PDFs and documents as cards), and `hub` and the MCP
tools rewrite them in a bot's text before sending. An entry that is not https, names a malformed bucket, or names
`TICO_BLOB_BUCKET` (Tico's own private store, served only through its access checks) is ignored and logged. Whether the distribution is public or behind signed URLs or cookies is the operator's choice; Tico only
rewrites the link and never signs or fetches it. An unmapped `s3://` URI shows as a file chip with a Copy button;
`hub file import` makes it a Tico file that shows inline.

## Publishing

**Explicitly**, from a run (the same tools exist over MCP as `hub_file_*`):

```
hub file publish reports/2026-09-29-pipeline.md [--title T] [--task ID] [--scope task|bot]
hub file link https://docs.google.com/document/d/... --title "Q4 plan"
hub file touch <file id or link>
hub file import s3://bucket/key
hub file list
```

A bot can write only its own files: the authenticated bot decides, never a parameter.

**Automatically**, at the end of a completed run, the runner uploads new or changed files under
the bot checkout's `reports/` and `artifacts/`, comparing against what it published before. Choose
other folders (or none) per bot in `bot.yaml`:

```yaml
files:
  publish: [reports/, artifacts/, deliverables/]   # publish: [] turns it off
```

Documents, images, video, audio, csv, tsv, json, yaml, md, html, pdf and office files can be published.
Automatic file publishing keeps its 25 MiB cap. Task attachments use the server upload limit
(2 GiB by default; 10 MB on older servers).
A `.env`, anything with a credential-like name, a symbolic link, a file with more than one hard link, and any path that resolves outside
the checkout is refused. Uploads go through a durable outbox in the runner's own state with an
idempotency key per file and content, so a restart or a lost reply retries safely and lands once. A
file that could not be uploaded is not linked and never opens nothing.

When the bot's repository is on GitHub and the file is committed and pushed, the version records
the commit and the row offers **View on GitHub** at that exact commit.

A bot's deliverable attached to a task (`hub task attach`) is listed as a file for that task too.
Text attachments preserve indentation, spaces and final newlines. Unsupported bot deliverable types return a clear
422 error listing the allowed types.

`hub file archive <id>` also accepts a task attachment's ID. A task participant or a human who may move the task can
archive it; the attachment is detached from the task and the action is recorded. Stored bytes and version history remain.

## Visibility

A file inherits the visibility of where it came from:

- made for a **task**: whoever can see the task;
- made in a **conversation**: whoever can see the conversation. A direct chat's files are visible
  to its participants and to the team owner, who can open direct chats. Personal Assistant rooms are the exception:
  only their human sees them, the owner included. In a shared bot room, only its members do. Anyone else does not learn a file name, a count, a version or an
  activity entry from it, on the bot's page or anywhere else;
- **bot-wide**: anyone with **Read** permission on the bot ([See, Read and Write](permissions.md)). **See** alone lets them reach the bot's page, not read its files.

The check applies to every list row, the total, metadata, activity, versions and every download.

A bot cannot make a chat's file bot-wide. The owner or a bot administrator **promotes** a task or
conversation file to bot-wide, explicitly and never automatically (`PATCH /api/v2/files/{id}` with `promote: true`); the move is
recorded in the file's activity. Archiving (`archived: true`) takes a file off the list; its bytes stay, and it returns when
the bot publishes a changed version.

## API

Stable v2 (`docs/openapi/v2.json`): `GET /api/v2/bots/{bot}/files?limit=&cursor=` (ordered by last
activity, with the visible `total`), `POST /api/v2/files/uploads`, `/links`, `/imports`,
`PATCH /api/v2/files/{id}` (title, task, archive, promote), `GET /api/v2/files/{id}/activity` and
`/versions`. Display names come as `actor_name` and in `actors`, as everywhere in v2.
`GET /api/v2/bots/{bot}/instructions` reads the Computer's latest Instructions snapshot with the bot's Read permission.
It returns `content`, `published`, `updated` and `source`; `published: false` means the Computer has not reported content yet.
It never substitutes the bot's description for its Instructions.
`docs/custom-frontend.md` and `examples/custom-frontend` show a bot's Files in a frontend of your own.

## Retention

Every version is kept: versions are immutable and nothing prunes them. The owner or a bot administrator
removes a file from the bot's page (`PATCH /api/v2/files/{id}` with `archived: true`). That hides the row
and keeps the bytes; the file returns when the bot publishes a changed version. A bot cannot remove a file.

## Storage

Local disk is the default. To use S3:

1. Create a dedicated bucket in the AWS console, or preview
   `deploy/aws/attachments-s3.sh --bucket acme-files --region us-east-1 --role acme-server`
   and apply it with `--apply`. The script defaults to dry run and creates a private bucket with
   public access blocked, AES256 encryption, BucketOwnerEnforced ownership, and a lifecycle rule
   aborting incomplete multipart uploads after two days. It preserves other lifecycle rules.
2. Grant the server's IAM user or role `s3:ListBucket` and `s3:GetBucketLocation` on the bucket,
   and `s3:GetObject`, `s3:PutObject`, `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts`
   on its objects. The script installs this inline policy for `--user` or `--role`.
3. For Docker installs, put `TICO_BLOB_BUCKET=acme-files` (or `s3://acme-files/prefix`) in
   `.env` next to `compose.yaml`, then run `docker compose up -d` in that directory to recreate
   the server with the setting. Grant the server role or backup keys the file bucket
   permissions from step 2. For other installs, set it in the server's environment and restart.
   Optionally set `TICO_BLOB_REGION` and `TICO_BLOB_ENDPOINT` for an S3-compatible store.

`TICO_BLOB_CREDENTIALS` selects the source for attachments and desktop downloads:

- `auto` (default, also used for an empty setting): at startup, try the dedicated file keys
  (`TICO_BLOB_ACCESS_KEY_ID` and `TICO_BLOB_SECRET_ACCESS_KEY`) when both are set, then backup
  keys (`LITESTREAM_ACCESS_KEY_ID` and `LITESTREAM_SECRET_ACCESS_KEY`) when both are set,
  then boto3's default chain. The first source whose write check succeeds is used for the
  process lifetime. Setting both file keys therefore gives them first preference.
- `role`: use only boto3's default chain for writes, including server AWS environment settings,
  shared credentials or profiles, and IAM roles.
- `backup`: use only the backup key pair for writes.
- `keys`: use only the dedicated file key pair for writes.

Docker forwards this setting from `.env`. Explicit `backup` and `keys` require both keys;
missing or denied keys produce a Health warning without selecting another source for writes.
If no source passes the check in `auto`, the first candidate remains available for reads.
Uploads return a retryable storage error until a source passes the check, and after a denied
check. Each upload keeps the same client through all parts, completion and abort.
A denied GET or HEAD tries each other configured source once before attachments fall back to
retained local copies. Download links are signed by the source that passed their HEAD check.
Read retries never change the selected write source.

Keys are passed directly to S3 clients, never exported into the server's environment or logged.
Only the source kind is reported. Region uses `TICO_BLOB_REGION`, then `TICO_BACKUP_REGION`
when both storage and backups use AWS (both endpoint settings unset), then `AWS_REGION` or
`AWS_DEFAULT_REGION` inside the server, then boto3's default. Docker does not forward the
operator's shell AWS credentials; `AWS_REGION` and `AWS_DEFAULT_REGION` pass through when set. Empty settings are treated as unset. The bucket
script needs Python with boto3 and provisioning rights; it is idempotent. Use a dedicated
bucket since it sets security controls.

Owners receive a read-only `storage` field on `/api/v2/health`: mode (`local` or `s3`), bucket,
region, credential source (`credentials`: `role`, `backup`, or `keys`) after the check,
unique stored files, bytes, and copy counts (`done`, `total`, `failed`). A server on local
storage shows one Not urgent note recommending S3; local laptop installs do not. On startup, an S3
server checks write permission by starting and aborting an empty multipart upload under the file
prefix for each candidate until one succeeds. This publishes no file and needs no delete permission.
A denied check shows a warning such as **S3 storage can't write: AccessDenied on acme-files
(s3:PutObject)**. If creation succeeds but cleanup is
denied, Health instead names the missing `s3:AbortMultipartUpload` permission. The probe uses
5-second connect and 10-second read timeouts with at most two retries, and a five-minute overall
wait, including SDK credential discovery. Concurrent checks share one probe; shutdown does not
wait for an in-flight call, and a canceled probe cannot later select a write source.
Read client credential discovery waits at most five seconds per source and access mode, sharing
one background construction for each, so a delayed credential provider preserves local read
fallback without accumulating workers. Desktop bucket manifest requests share one fetch,
read at most 1 MiB plus one byte to detect oversized manifests, and always close the response.
GET and HEAD use separate read clients with 2-second connect and 5-second read timeouts and
one attempt; upload clients keep their existing timeout budget. These are socket inactivity
limits, not a wall-clock deadline for DNS, credential refresh or a continuously active stream.
Storage stays in S3 mode, new uploads
report the failure, and reads still fall back to retained local copies. After fixing credentials or
permissions, the check repeats every 30 minutes while failing and switches to the first working
source automatically. Retained local files are then copied again. Restarting also repeats the
check. At most one unfinished probe is retained for cleanup, so denied cleanup cannot
accumulate multipart uploads. Rehearsals skip this write check.


With `TICO_BLOB_BUCKET` configured, every new attachment goes straight to private S3 storage.
Without a bucket, local storage keeps laptop and demo installs working. Files are addressed by
SHA-256; uploads larger than 8 MiB use S3 multipart upload. Objects carry their media type, a safe
inline/download disposition, server-side encryption, and
`Cache-Control: private, max-age=31536000, immutable`.

On startup, a server with a bucket copies registered local files in the background. One worker
limits copy upload and verification traffic to 8 MiB/s, verifies the uploaded SHA-256, and records
the bucket location. Authenticated Health shows **Moving files to S3: n of m** and any failure. Local copies stay
on disk; nothing deletes them automatically. Reads prefer S3 and fall back to the retained local
copy. Failed copies retry on the next server start. `/healthz` returns only counts-free liveness and release identity; copy details require sign-in.

`POST /api/v2/tasks/{id}/files` accepts multipart fields `file`, optional `name`, `note`, `ask`
(a JSON string), and `poster` (PNG or JPEG). Uploads spool to private temporary files and are
hashed in chunks. `TICO_UPLOAD_MAX_BYTES` defaults to 2 GiB; larger files get a 413 with the limit.
Staging needs enough free disk space for the upload and media processing. Temporary files are
removed after processing; abandoned media staging directories expire after a day.
The JSON `text`/`content_base64` route still accepts files up to 10 MB for older clients.

`hub task attach <id> cut.mp4 --poster poster.jpg --note "Rough cut"` streams from disk when
`/api/v2/config` advertises `features.task_files_multipart`. An older server receives the existing
JSON upload; the client refuses files over 10 MB before reading them. The Computer's automatic
file publishing also hashes and uploads from disk. Video and audio extensions are accepted
alongside the existing document and image types. The local MCP adapter can use `hub_task_attach`
with `path` and optional `poster`; server MCP calls keep using text/base64 and cannot read server paths.

Every file, version, poster, and thumbnail request checks the existing Tico rights first.
`GET /api/v2/files/{id}?v=N` selects a version; `/versions/N` remains supported for stored bot
files. `/poster?v=N` and `/thumb?v=N` return 404 when a preview is unavailable. Tico streams S3 or local bytes, supports HEAD and a single HTTP byte range (206/416; multiple ranges return the full 200 response), and returns a digest
ETag. Versioned responses are immutable for a year; latest responses use `no-cache` and can return
304. Downloads preserve the stored bytes exactly, including JSON whitespace and encoding;
API display-name annotations never change file contents. `X-Content-SHA256` and ETag identify
the whole stored file, including on HEAD and partial (206) responses.
Images, supported video, audio, PDF, plain text, Markdown, and CSV can open inline. SVG,
HTML, and unknown types download. All byte responses retain `nosniff`. PDFs are inline with `default-src 'none'; style-src 'unsafe-inline'`
and no sandbox, allowing the browser PDF viewer. Other types retain the sandbox CSP.

Media fields live on each file version: `width`, `height`, `duration_ms`, `poster_blob_id`,
`thumb_blob_id`, and `media_state` (`pending`, `ready`, `none`). One background worker fills them;
existing versions default to `none` and are not automatically backfilled. New versions are `pending`.
Transient storage failures retry with bounded backoff; permanent decode failures become `none`.
Missing tools or unsupported media never fail an upload. Pillow decodes only PNG/JPEG/GIF/WebP in a subprocess capped at 1 GiB memory, 20 CPU seconds,
50 MP and a 30-second timeout (macOS uses parent RSS supervision for memory), and makes thumbnails up to 480 px; without Pillow, PNG/JPEG/GIF/WebP headers still provide dimensions. ffprobe/ffmpeg make
video posters at the earlier of 1 second or 10% of duration, up to 640 px. pdftoppm renders PDF
page one. A supplied poster is preserved.

The optional ffmpeg + poppler-utils install was measured in a disposable
`python:3.12-slim-bookworm` container with the server's ca-certificates/curl baseline:
181,808 KiB before, 557,780 KiB after (apt lists removed), **375,972 KiB / 367.2 MiB added**.
APT reported 119 MB of download archives and 417 MB of installed packages. This exceeds the
150 MB image budget, so these tools are omitted from the standard server image. Install them in
a custom server image, or supply a poster. Pillow and the multipart parser ship with the server.
## Task file reviews

Task attachments are versioned by name within their task, across uploaders. Reusing a name
adds a version; an archived name starts a new file. Existing attachments are v1. Task files
and their versions are listed by `GET /api/v2/tasks/{id}/files`, with notes, questions, all
answers and nullable media metadata. Downloads accept `?v=<n>` for an exact version.
The version's author can edit its note or question through
`PATCH /api/v2/files/{id}/versions/{n}`. File bytes remain immutable. Reviews use the existing
ask/answer messages, so their text remains readable by older Computers.
See [Files, versions and questions](tasks.md#files-versions-and-questions) for the CLI, MCP
and answer contracts.

Upload retries verify their idempotency receipt before writing bytes to durable storage.
Multipart retries still parse and hash the incoming bytes to reject changed content, using
short-lived private spools. Parsing, hashing and spool writes run in a worker thread so large
uploads do not block the server's event loop.
