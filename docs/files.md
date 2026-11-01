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
   authorized bytes or opens a short-lived link on the company's files address when it uses
   [Tico file hosting](#tico-file-hosting).
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
Every kind but video and audio is capped at 25 MB. `hub file publish` and `hub file import` take video and audio up to
the server's upload limit (`TICO_UPLOAD_MAX_BYTES`, 2 GiB by default). They stream from disk with a raw body and a
Content-Length, never as base64, and the server hashes the bytes as they arrive. A video or audio file must start with
the container its extension names, or it is refused. Older servers do not advertise `features.file_media_stream`: they
take video and audio only up to 25 MB, and the client refuses a larger one before it reads it.
Automatic file publishing (the runner's) keeps its 25 MiB cap. Task attachments use the server upload limit
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

A picture, SVG, HTML page, video or audio clip can also go on a chat reply or task comment as a titled visual
(`--visual PATH --title "…"`); it is stored as a file there and shows inline as a card; video and audio play in it. See
[Visuals in replies](visuals.md).

`hub file archive <id>` also accepts a task attachment's ID. A task participant or a human who may move the task can
archive it; the attachment is detached from the task and the action is recorded. Stored bytes and version history remain.

## Visibility

A file inherits the visibility of where it came from:

- made for a **task**: whoever can see the task;
- made in a **conversation**: whoever can see the conversation. A direct chat's files are visible
  to its participants and to the team owner, who can open direct chats. Personal Assistant rooms are the exception:
  only their human sees them, the owner included. In a shared bot room, only its members do. Anyone else does not learn a file name, a count, a version or an
  activity entry from it, on the bot's page or anywhere else;
- **bot-wide**: anyone with **Read** permission on the bot ([See, Read and Write](permissions.md)). **See** alone lets them reach the bot's page, not read its files;
- **pinned wider**: a page a person pinned to the Dashboard or a team page is also readable by that place's audience
  ([Overview](overview.md)). A bot's own pins never widen, and a private task's file is never pinned there. Each pin and
  unpin is in the file's activity (`pinned`, `unpinned`, with the place).

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
It returns `content`, `published`, `updated_at` and `source`; `published: false` means the Computer has not reported content yet.
It never substitutes the bot's description for its Instructions.
`docs/custom-frontend.md` and `examples/custom-frontend` show a bot's Files in a frontend of your own.
Pages, Library › Files, search and deleting for good are below and in [Pages](pages.md).

## Library › Files

**Library › Files** lists every file the viewer may see, across all bots and whoever added it: bots' files and
[pages](pages.md), task and chat attachments, people's uploads, links and imports. Each row passes the same check as
the bot's page, so a file you may not read adds no row, name or count.

- **Filters:** kind (Pages, Documents, Images, Data, Video and audio, Other), bot, team (its bots' files and the
  files on tasks its bots and people own, nested teams included), task, date, pinned and archived. They ride in the
  address (`#/library/files?kind=page&bot=…`). On a phone they scroll sideways.
- **Rows:** title, kind, bot, last updated, and where a page is pinned. A page opens full size; another stored file
  opens in the file viewer; a link opens at its provider.
- **Paging:** newest activity first, 50 rows at a time, each answer capped in bytes, with no total.

`GET /api/v2/files?kind=&bot=&team=&task=&since=&until=&pinned=&archived=&limit=&cursor=`.

## Search

Search finds files by title and text: Markdown and plain text as they are, HTML as its visible text (never its
scripts or styles), and CSV or TSV by its header and first 50 rows. PDFs and office files are found by title. The
ranking follows [Docs](docs.md#search): title first, then text, each hit with an excerpt. Every hit passes the same
visibility check as a list row.

Tico indexes a version when it is published. A file from before this release is indexed in the background, a few
hundred at a time; until then only its title is found.

`GET /api/v2/files/search?q=&limit=`, `hub file search "<words>"`, file hits in the search box (⌘K), and the
Librarian, which searches files beside the docs.

## Deleting a file for good

For a file that holds something it shouldn't. **Only the owner** deletes a file for good: **Delete for good** in a
page's menu, `DELETE /api/v2/files/{id}`, or `hub file delete ID` with an owner token. BotOps asks the owner to
confirm first.

- Every version's bytes leave every place Tico stored them: this server, the company's bucket and its earlier
  places, and Tico file hosting. Bytes anything else still uses stay: another file, a task or chat attachment, a
  meeting's recording, market evidence, the team icon. Bytes this server wrote in the last two minutes (the same
  bytes, uploaded again) go on a later pass, within a few minutes.
- Its search text, page settings and pins go.
- One row stays, titled **Deleted file**, and its activity records who deleted it, when and how many versions,
  without the name or the content. Publishing the same path again makes a new file.
- The backup (`TICO_BACKUP_URL`) keeps its copy: files there are only ever added. Remove them there by hand if they
  must go.

## Retention

Versions are immutable and kept unless you stop [Tico file hosting](#tico-file-hosting) and its retention period ends,
or the owner [deletes the file for good](#deleting-a-file-for-good).
The owner or a bot administrator removes a file from the bot's page (`PATCH /api/v2/files/{id}` with `archived: true`). That hides the row
and keeps the bytes; the file returns when the bot publishes a changed version. A bot cannot remove a file.

## Storage

### Prepared file-distribution edge

`infra/files-edge/template.py` renders an **edge-only** CloudFormation template to stdout; it never
calls AWS. The stand-up supplies the private bucket and bucket policy, certificate, log bucket, broker
provisioning, HQ key synchronization and DNS. The template takes `FilesDomain`, `CertificateArn` and
`BucketRegionalDomainName`; it creates the distribution, OAC, key store, functions and response policies.
The distribution stays **disabled** unless `Enabled=true` is passed: enable it only once the bucket policy
and certificate are in place. `LogBucketDomainName` (`<bucket>.s3.amazonaws.com`, ACLs enabled, as CloudFront
standard logs require) and `LogPrefix` (default `cloudfront/`) turn on the standard logs HQ meters delivery
from. Its OAC needs a bucket policy restricted to this distribution's ARN. Keep Block Public Access on;
never grant public reads.

The v1 edge contract is:

- Hosts are `<workspace>-files.<FilesDomain>`; HQ alone writes the lowercase host **label** as the
  edge key-store key (for example `acme-files`). Its JSON value is
  `{"v":1,"prefix":"c/<lowercase-v4-install-uuid>/","signing":{"key_id":"k1","secret":"<64-lowercase-hex>"}}`.
  Host is lowercased before lookup and signature verification. Invalid record versions, prefixes,
  key IDs or secrets fail closed. HQ derives the per-install secret from its master and writes it
  before returning the address to the app; the edge never holds that master. HQ removes the record
  with the company's stored prefix at the end of retention. HQ's `HQ_FILES_KVS_ARN` identifies the store.
  Company S3 write credentials must never grant key-store writes. The signing secret is used as text,
  not decoded as base64. Deleting or rotating the record revokes links; the app must refresh its key.
- The only paths are `/blobs/<2hex>/<sha256>` and `/visuals/<2hex>/<sha256>`, all lowercase and with the
  first two hash characters repeated in `<2hex>`. The request gate checks `e`, `k` and `s`, refusing
  duplicates, expired links and expiry more than 300 seconds ahead. `s` is unpadded base64url
  HMAC-SHA256 over `host + "\n" + original_path + "\n" + e`. It binds the host and namespace before
  rewriting to `/c/<company-id>` plus the original path. Unknown paths get 404, invalid tokens 403.
  Only GET/HEAD proceed. Query strings (including S3 response overrides) and cookies are removed.
- Files always carry `default-src 'none'; sandbox`, `nosniff`, `Access-Control-Allow-Origin: *`,
  `Cross-Origin-Resource-Policy: cross-origin` and `Referrer-Policy: no-referrer`.
- Visuals have their own behavior and response function. The stored Content-Type selects HTML or SVG
  (case-insensitive, before `;`); HTML is normalized to `text/html; charset=utf-8`, SVG to `image/svg+xml`.
  Missing/unsupported type or `x-amz-meta-tico-visual-policy-version` other than exactly `1` returns an
  empty 404. HTML without a charset is accepted and normalized. Partial visual responses and redirects
  are refused. The app writes cleaned bytes, with its reporter script appended for HTML; the edge does
  not clean or inspect the body.
- Policy source is object metadata. `x-amz-meta-tico-visual-ancestors` supplies up to eight whitespace
  separated tokens, each exactly `'self'`, an HTTPS origin matching
  `https://[a-z0-9.-]+(:[0-9]{1,5})?`, or HTTP localhost/127.0.0.1 with an optional port.
  `x-amz-meta-tico-visual-script` supplies up to four quoted SHA256 hashes matching
  `^'sha256-[A-Za-z0-9+/]{43}='$`. Invalid tokens are dropped; an empty slot becomes `'none'`.
  Metadata cannot supply a full CSP or sandbox tokens. Contract changes require a new version at
  the edge first.

The fixed visual templates (only `<script>` and `<ancestors>` are substituted) are:

```text
HTML: default-src 'none'; script-src <script>; img-src data:; font-src data:; media-src data:; style-src 'unsafe-inline' data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors <ancestors>; sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox
SVG: default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline' data:; frame-ancestors <ancestors>; sandbox
```

Every visual response sets inline disposition, `nosniff`, `no-referrer`, CORP cross-origin and
`Cache-Control: private, no-cache`, with no CORS. Response functions remove all object metadata,
origin CSP/report-only values (including repeated values), and both header and CloudFront cookie-object
representations of Set-Cookie. They replace CSP with exactly one header. Arbitrary object writers cannot
introduce a directive or `allow-same-origin`: the document stays in an opaque origin. Body CSP meta tags
can only restrict this policy; Set-Cookie meta tags do not set cookies. The app still owns body cleaning.

Both behaviors also attach an overriding restrictive response policy and remove Set-Cookie/report-only
at the policy layer. CloudFront applies policies **before** the viewer-response function, so the visual
function replaces the fallback CSP without stacking it. When an S3 origin returns an error, CloudFront
skips viewer-response; the restrictive fallback still applies. S3 errors have no object metadata. There
are no custom error-page mappings. All cache TTLs are zero so a company cannot freeze mutable metadata
with its own Cache-Control; the request gate still authenticates every request. Enabling caching later
requires checking metadata invalidation and the same security boundaries.

Run the synthetic handler and wiring checks with `node --test infra/files-edge/edge.test.cjs`.
They execute the exact generated function code with only the key store and clock replaced. No AWS or
team database is used. With Playwright and Chromium installed, the optional builder check
`node infra/files-edge/browser-check.cjs` verifies that a hostile HTML fixture cannot read/write cookies
or reach its same-host parent, even with body/meta/header/metadata attempts to add `allow-same-origin`.
After approval, stand-up additionally needs actual CloudFront validation and
origin-error/header readback before activation; a local harness cannot prove AWS runtime acceptance.

The prepared Public Suffix List rule for the production base domain also covers `*-files` sibling
hosts: each hyphenated name is one DNS label, so no extra wildcard entry is needed. Publication and
browser uptake are separate deployment gates. The sandbox and cookie stripping remain mandatory
regardless of PSL status.

AWS references: [event and cookie structure](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/functions-event-structure.html),
[error-event restrictions](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/edge-function-restrictions-all.html),
[policy ordering](https://aws.amazon.com/blogs/networking-and-content-delivery/amazon-cloudfront-introduces-response-headers-policies/).

### Server storage

Local disk is the default. The owner can use [Tico file hosting](#tico-file-hosting) or
[the company's own bucket](#a-companys-own-bucket). The server's operator can also configure a bucket.

To use S3 for the server's storage:

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

### Tico file hosting

A self-hosted company with Gateway credits can keep its files in storage Tico runs, served from its own
`ticofiles.com` address. It covers task files, chat attachments, visuals, reports and recordings.
The owner turns it on in **Settings** through the Gateway page. Tico HQ supplies the storage access and files
address; you do not enter AWS keys. For example, Acme at `acme.example` can use `acme-files.ticofiles.com` for its files.

New files go to Tico storage from then on, without a restart. Existing files stay readable where they are
until the owner chooses to move them. **Settings › General › Storage** shows **Tico file hosting**, its files
address and the controls below.

**Move existing files** shows the file count and GB estimate before you confirm. It copies every file and every
version into Tico storage and checks each copy before switching reads to it. The completed move reports the GB
moved, matching the estimate. The move never deletes the originals. Tico's own copy checks do not incur delivery
charges. If the estimate changes before you confirm, Tico asks you to check the new one. An interrupted move can
be started again to copy what remains.

Files in Tico storage are reachable only through short-lived signed links. Tico checks access on every request,
private-task files stay private, and there is no public listing. Pictures, video and audio open inline from the
company's files address. Web pages and SVG files show inline from it too, so a Mac or local install with no
[visual address](visuals.md#self-hosting) of its own still shows them in conversations and on pages: Tico stores a
cleaned copy beside each one (`/visuals/…`, as described above) and, after checking the reader, sends their browser
to a four-minute link to it. A page stored before file hosting was turned on stays an **Open** card until **Move
existing files** copies it. While credits are out or hosting is stopped, a page that already has its copy still
shows inline; a new one is an **Open** card.

When the owner stops file hosting on the Gateway page, new files go back to the company's own storage: its bucket,
or the server's storage if it has no bucket. **Download all files** offers a ZIP export of every file and version
the owner may open. Files private to someone else are left out, with a count in the export's manifest.
Tico HQ keeps the hosted files for 30 days, emails the owner before deleting them, then deletes them.
The app shows the keep-until date HQ supplies. **Move files back** copies every file and version to the company's
own storage while HQ still keeps them. Download or move the files before that date.

If Gateway credits run out, existing files stay readable. New uploads go to the company's own storage until
credits are added. If that storage is unavailable, Tico refuses the upload with a plain reason.
File hosting does not change the visual upload caps: 25 MB for bots and 10 MB for people
([Visuals in replies](visuals.md#types-and-limits)).

### A company's own bucket

In **Settings › General › Storage** the owner enters a bucket, its region, an optional folder in it, and an access key ID
and secret access key. Only the owner, signed in as themselves, can see or change it; a bot, BotOps and the Assistant
cannot. Before saving, Tico uses those keys to start and cancel a multipart upload, then writes a small check object
(`<folder>/blobs/…`, about 80 bytes) and reads it back; when any step fails it saves nothing and says which S3 error it
got. The keys need `s3:PutObject`, `s3:GetObject`
and `s3:AbortMultipartUpload` on the bucket's objects (step 2 above, without the IAM role), and `s3:DeleteObject` to
replace the team icon (docs/desktop.md).

Unless Tico file hosting is taking new files, saving this setting sends new files to that bucket without a restart.
It takes priority over `TICO_BLOB_BUCKET` and the server's disk. While file hosting is on, this bucket remains the company's own
storage for when file hosting stops or Gateway credits run out.

Files already stored keep opening: Tico tries the bucket taking new files, earlier buckets (newest first), then
the server's storage. When the company's own bucket takes new files, Tico also copies files from the server's disk
into it in the background (**Settings › Health** shows progress). Files in another bucket stay where they are.
**Use the server's storage** selects the server's storage instead of the company's own bucket; it does not stop
Tico file hosting. The bucket stays on the list of places files are read from. Backups (docs/install.md) read each
file from wherever it is stored, so they use the company's keys for its bucket.

The secret access key is sealed with the credential key (`credential.key`, or `TICO_CREDENTIAL_KMS_KEY`) and is
never shown again, logged or returned by the API. Leave it empty to keep the saved one when changing the bucket's folder
or region with the same key ID. A copy made with `python -m backend.devcopy` leaves the company's bucket out.
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
Missing tools or unsupported media never fail an upload, and a version nothing can be derived from (an SVG, or a
video or PDF without its tool) becomes `none` without being read: in Tico's bucket every read is delivery. Pillow decodes only PNG/JPEG/GIF/WebP in a subprocess capped at 1 GiB memory, 20 CPU seconds,
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

### Approved versions

`hub file approved <path|FILE[@N]> [--by human|bot:SLUG|any]` (MCP `hub_file_approved`,
`GET /api/v2/file-approvals?file=&version=` or `?path=&digest=`, plus `&by=`) says whether that exact version is
approved. It prints `{approved, by, at, task, question}` and exits 0 when approved, 1 when not (the answer says
`"approved": false`), 2 on bad input. A failed call also exits 1 or 2, with an error answer instead: read the answer,
not only the code. A path is the bot's own file, matched by the sha256 of its bytes now. By default only a person's
verdict counts (an answer to a [file review](tasks.md#reviewing-files)); `--by` names whose verdicts count instead. The
newest matching verdict on that version decides (Request changes after Approve means not approved), a new version is
never approved by an older one's review, and a deleted task's verdicts stop counting.

`hub file review <path|FILE[@N]> --approve|--changes --task <task> [--note TEXT]` (MCP `hub_file_review`,
`POST /api/v2/file-reviews`) gives a verdict directly, for a company where a bot moves a stage: a bot reviews another
bot's file on a task it can read, when it can open the file. A bot never reviews its own bot's file (403). A person
may use it too; Decide is the usual way.

Upload retries verify their idempotency receipt before writing bytes to durable storage.
Multipart retries still parse and hash the incoming bytes to reject changed content, using
short-lived private spools. Parsing, hashing and spool writes run in a worker thread so large
uploads do not block the server's event loop.
