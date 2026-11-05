# Physical mail

> **Unreleased**: the install side and Settings > Inboxes' Letters in are built; Tico Gateway's mailboxes are not on sale yet. The API
> below may change.

Tico Mail brings the company's paper mail into Tico. Each scanned letter is saved as a file, read, sorted by kind and
sent to whoever handles that kind. Legal papers reach the owner at once, including when a chosen bot handles them.

## Where letters come from

- **A Tico Gateway address.** Tico HQ keeps the mailbox (a shared or private mailing address, with a registered agent,
  in a state). Each new letter arrives on the same Gateway queue as webhooks and Tico Team notices, so a Gateway key is
  all a server needs. The scan is fetched from Tico Gateway.

  Prices: shared $10 a month per recipient, private from $25 a month; scans $1 a letter for up to 10 pages, then
  $0.25 a page. Tico doesn't deposit checks: ask Tico support to forward one, or deposit it yourself.
- **Your own provider, by email.** An owner or admin names one Tico address as the mail-intake
  address and who sends it scans: `PUT /api/v2/postal/intake` with `{"inbox_id": "...", "senders":
  ["scans@provider.com", "@provider.com"]}` (at least one address or `@domain`), or `{"inbox_id": null}` to stop.
  Have your mail provider send its scans there. Each PDF or image attached to a message from a listed sender becomes a
  letter; other senders, other attachments and other inboxes are left alone. The sender is what the message claims,
  so the list keeps out strangers and floods; a scan is still read as outside text.
  Someone outside a narrowed intake inbox's audience sees that an intake is set, not which inbox or address.

## What happens to a letter

1. The scan is kept as a file on the letter's task (Library > Files), for good; mailbox providers keep scans for a
   limited time.
2. It is read through Tico Gateway (Low tier, which reads PDFs and images): sender, a one-line summary, the response
   deadline, if any.
3. One Decide call ([question set](../questions/README.md) `physical-mail`) says its kind.

At most 100 letters are read in any 24 hours; past that a letter waits an hour and tries again, without using up one
of its tries.
4. It goes where its kind goes:

| Kind | Goes to |
|---|---|
| Legal (summons, lawsuit, subpoena, court papers) | The owner by default; optionally a chosen bot, with the owner as requester and a notice to the owner; private, due on the deadline |
| Government or tax | The Finance bot, or the owner when there is none; private |
| Bill | The Finance bot, or the owner; private |
| Financial (statements, bank notices) | The Finance bot, or the owner; private |
| Check (flagged by the mailbox provider, or seen by the reader) | The owner, private. Tico doesn't deposit checks |
| Junk | Archived: no task; the scan is still kept |
| Other, or not sure | The Assistant, or the owner |
| Could not be read | The owner, private |

Legal goes to the owner whenever the reader or Decide says legal, however unsure: a missed summons costs more than
a wrong card. A check works the same way: when the mailbox provider flags one in the envelope, or the reader sees
one, it goes to the owner whatever Decide says, never to junk or the Assistant. Junk is archived only when Decide is sure; anything else short of its threshold goes to the Assistant.
An owner or admin may choose an active bot for future legal mail in Settings > Inboxes > Settings > Letters in (Legal mail). The owner remains a party to
each private task and receives a notice with its deadline. A paused or unavailable bot falls back to the owner.
Resetting the setting to Owner affects future letters; existing tasks stay where they are. Upgrading enables no
grants and keeps legal mail going to the owner.

A letter's text comes from outside the company. The reading prompt and the Decide question both treat it as data,
never as instructions, and each task says so.

## Who sees a letter

A Read grant lets one person or bot read the company's letters and scans, including private legal mail. A grant
covers all addresses or one Gateway assignment. Only an owner or admin may grant or revoke access; the grant keeps
who granted it and when, and both actions leave audit events. A grant covers letter content and its scan, without
opening the private task. Revocation removes this access immediately; task-party rights and other grants still apply.

When the Assistant or BotOps reads for a person, both the person and the actual bot must independently qualify by
a mail grant or the letter's existing access policy. A person's grant alone never grants the Assistant access.
This applies to letters, hub/MCP reads, raw scan downloads and exact file-version downloads.

The built-in Counsel also supports these tools inside its claimed runs. A grant permits only the letters and scans
within its scope; without a grant, Counsel reads the legal letter handled by its current task. Its restrictions on
other files, document versions, credentials and outgoing actions still apply. Bots whose hosts only sync chat cannot
use physical mail; both the API and pickers explain this restriction.

Without a grant, a filed letter shows to whoever may read its task. Every kind but Other is a private task (account numbers and amounts
are not the whole company's to read), so it shows only to the owner and the bot it went to, not to admins. A letter with no task (just in, failed, or archived as junk) shows in full only to the
owner; admins see that it came and its status, so they can retry it, but not its sender, summary or scan. The scan
download (`/api/v2/files/{id}`) follows the same rule, and so do the hub tools.

## When something fails

A letter whose scan or reading fails is marked `failed` and tried again after 1 minute, 5 minutes, 30 minutes and
2 hours. After five tries it waits; an owner or admin retries it with `POST /api/v2/postal/items/{id}/retry`. On the
last try a scan that still cannot be read is filed for the owner unread, so nobody misses it. A retry never makes a
second task.

## Letters out

Bots can quote and send US letters through Tico's Gateway, using Tico's Click2Mail account. Print and postage cost
the provider's price plus 25%. An unconfigured Gateway shows Coming soon and does not retry in a loop.

An owner or admin grants sending with `PUT /api/v2/letters/outboxes/company/grants/bot/{bot_id}` and
`{"level":"send"}`. `DELETE` on the same address revokes it; both are audited. The grants list is
`GET /api/v2/letters/outboxes/company/grants` (`limit` up to 100, `after` from `next`, 64 KiB cap); people may also receive a `person/{person_id}` grant. BotOps or an
Assistant changing grants or company policy must use the person's Confirm flow.

Set `PUT /api/v2/letters/settings` with `return_address` (`name`, `line1`, optional `line2`, `city`, two-letter US
`state`, `postal_code`), `mode` (`ask_first`, `on_their_own`, `never`) and optional `monthly_cap_micros` (USD millionths;
null removes the cap). A structured company physical address is the default when present; otherwise set a return
address first. `GET` shows these settings and setup state. A manager can set a stricter mode and optional cap at
`/api/v2/letters/bots/{bot_id}/settings`; the company rule always wins. Omitted bot settings stay unchanged; explicit
null inherits the company default. Caps include Tico-originated letters and uncertain submission holds across the
company, plus the optional bot cap, in each UTC month. A failed delivery does not erase spend: HQ's billing state
must confirm no charge, a void or a reversal. Older HQ responses without billing fields keep the reservation.

Use `hub letter quote --text "..." --recipient '{"name":"Recipient","line1":"2 Main St","city":"Dover","state":"DE","postal_code":"19901"}'`
(MCP `hub_letter_quote`), or `--file-id` for a PDF already in Tico's upload/media store. ReportLab renders text and
markdown as searchable US Letter text, without fetching images or executing HTML; upload a PDF for characters
outside its Latin character set. The source PDF may have 1–100 pages and be at most 5 MiB. The result includes the
page count, live total and a link to HQ's rendered proof, kept in Tico's own blob store.

`hub letter send <letter_id>` (`hub_letter_send`) asks for approval by default. The existing approval card shows the
proof, recipient, return address, mail class and total. A bot's manager or an admin decides; decline closes the
letter. On their own is an explicit company opt-in and still asks for certified mail, courts/agencies, uncertain
classification, unreadable or truncated PDF text, and the first letter to an address. An incomplete PDF extraction
says "Couldn't read the whole letter, so it needs an OK"; Tico does not try OCR. Classify/Decide is triage, not a security boundary. Never and revoked
access block approved sends too.

Quotes last 15 minutes. When approval lands Tico re-quotes; any changed approval hash asks again. Earlier proofs stay
readable only while the caller has current access to the original source's tasks, runs and threads. Both the
represented person and an acting bot must qualify. Admins without source access see a metadata-only row (status,
class, pages, cost, date and sending bot) and may cancel; recipients, contents and PDFs stay private. Letters without
a task follow their sender, manager and outbox grants. HQ keeps no letter contents.

Submission durably commits its quote, approval consumption and a key derived from the Tico letter id before the
HTTP call. A known pre-claim refusal releases the hold and remains cancellable; a later attempt gets a new quote
and approval as the current policy requires. After uncertainty Tico checks Never, access and caps again before any
retry POST. If sending is no longer allowed, HQ's resolve-by-key capability reconciles the existing job or atomically
abandons the key without printing. Older HQ versions leave the letter in Needs review and keep its hold. Never create
a replacement for a `submitting` or `needs_review` letter until its existing outcome is confirmed.

`hub letter list`, `hub letter show <id>` and `hub letter cancel <id>` have matching MCP tools and v2 endpoints:
`GET /api/v2/letters`, `GET /api/v2/letters/{id}` and `POST /api/v2/letters/{id}/cancel`. Lists are paged and byte
capped. Status sync polls each active record with backoff and publishes a permission-filtered `letters` live event
with the changed row. Cancellation passes through HQ's honest answer; User Credit jobs cannot be cancelled and
there is no promised undo window.

## Settings > Inboxes

Each address is a Letters in row in Settings > Inboxes (`#/settings/inboxes`), with open orders as Pending and the
email-in inbox as Mail by email (still listed, as Not in use, once it is turned off while letters it brought in remain,
so a failed one can be retried). An address's page shows its Form 1583 progress until it is active, who reads it (an
owner or admin gives and removes Read on that address there), its automations and its letters (date, sender, kind,
scan, the task it went to, status), with Retry on a failed one for an owner or admin. Inboxes > Settings > Letters in
has the addresses with Get an address (owner or admin: state, kind, monthly and scan prices), Bring your own (the
intake inbox), Who can use it (Read and Remove on all addresses) and Legal mail (who receives legal letters).

## API, CLI and MCP

| | |
|---|---|
| `GET /api/v2/postal/items` (`status`, `kind`, `after`, `limit`) | Letters, newest first, as [Who sees a letter](#who-sees-a-letter) says; a status-only row has `redacted: true`. |
| `GET /api/v2/postal/items/{id}` | One letter: summary, kind, sender, deadline, task, file id and scan link, mailbox flags, `contains_checks`. |
| `POST /api/v2/postal/items/{id}/retry` | Try a failed letter again (owner or admin). |
| `GET /api/v2/postal/addresses` | The company's addresses at Tico Gateway, its orders, and prices when Tico Gateway sends them. |
| `POST /api/v2/postal/orders` | Order an address (`kind`: `shared` or `private`; `state`; `company_name`), owner or admin, with an `Idempotency-Key`. Tico staff fulfil it. |
| `GET` / `PUT /api/v2/postal/intake` | The email-in inbox. |
| `GET /api/v2/postal/addresses/{address_id}/grants` | Explicit Read grants, owner or admin; `limit` (1–100) and `after` page, `next` cursor, 64 KB cap. Use `all` for company-wide grants. |
| `PUT /api/v2/postal/addresses/{address_id}/grants/{grantee_type}/{grantee_id}` | Give Read with `{"level":"read"}`; owner or admin, idempotent. `grantee_type` is `bot` or `person`; a bot id or slug is accepted and stored as its stable id. |
| `DELETE /api/v2/postal/addresses/{address_id}/grants/{grantee_type}/{grantee_id}` | Revoke that grant; owner or admin. |
| `GET` / `PUT /api/v2/postal/settings` | Legal routing: `{"legal_bot_id":null}` for the owner (default), or an active bot's id or slug; only an owner or admin writes. |
| `GET /api/v2/postal/bots` | Owner/admin mail pickers: paged bot ids, slugs and read/routing errors; the server enforces these same restrictions. |

For a Gateway address, use its `assignment_id` from `GET /api/v2/postal/addresses`. The letter's `address_id` is the
same assignment from the relay delivery; older deliveries and email intake use their exact `address` instead.
An assignment grant never matches a different assignment that happens to share the same street address.
Use URL encoding for address and grantee path segments and an `Idempotency-Key` for writes.

For example, to let a bot whose slug is `legal` read physical mail at every company address, send
`PUT /api/v2/postal/addresses/all/grants/bot/legal` with `{"level":"read"}`. Optionally send
`PUT /api/v2/postal/settings` with `{"legal_bot_id":"legal"}` to give it future legal-mail tasks while keeping the
owner informed. Use `DELETE` on the grant's URL to revoke it, and `{"legal_bot_id":null}` to restore owner routing.

Bots and people read letters with `hub mail list` and `hub mail read <id>` (MCP `hub_mail_list`, `hub_mail_read`).

## Personal alerts

In **Settings → Inboxes → Alerts**, a person can verify their own US or Canadian phone and opt into texts, with
separate consent for calls. Only that person, including through their personal API token, can read or change the
number; bots, BotOps and the Assistant cannot do it for them. Verification is free. Each accepted alert, including
a test, costs the company **$1**, covering its text or call. A test explicitly accepts that charge.

The available choices map to existing Tico state: an overdue active task owned by or waiting on you; newly filed
legal mail whose task you may read; and a stalled task for a bot you manage, using Health's existing stalled-task
check. Each incident is kept once per person. Legal mail and overdue tasks are urgent; calls require both call
consent and **Call for urgent alerts**. Phone messages contain a generic category and an instruction to open Tico,
never task titles or letter contents. Tico being down and autonomous email/letter sends have no alert checkbox.

Quiet hours use your IANA timezone, including daylight saving changes. They defer alerts until outside that window,
unless you explicitly allow urgent alerts through. A resolved task or withdrawn preference cancels queued paging.
Reply STOP to the paging number to block texts **and calls**; reply START to resume existing consent. Inboxes shows
**Opted out** from HQ's current state. Removing your phone revokes it at HQ and immediately stops local queued work;
if HQ is unavailable the removal remains pending and can be retried.

The personal monthly cap defaults to $10 and the company cap to $50. Owners/admins set the company cap; a person
sets their own. Caps count accepted alerts plus reservations for sends whose outcome is not confirmed, across
concurrent sends and restarts, in UTC calendar months. Zero stops paid alerts. An uncertain send retries with the
same provider idempotency key; it never gets a fresh incident id to bypass deduplication. The last ten outcomes and
spend are visible in Alerts settings. Tests bypass quiet hours but still require consent and both spend caps.

These controls use `/api/v2/alerts/me` and `/api/v2/alerts/company`, with `/me/phone`, `/me/phone/confirm` and `/me/test`
subroutes (see [the v2 contract](openapi/v2.json)). The same actions are `hub alerts show|set|verify|confirm|remove|test|company`
and MCP's `hub_alerts_*`, offered only to people. `hub alerts test --accept-charge --operation-id <stable-id>` keeps
retries to one paid test. They are personal settings, including in the Tauri app; Slack/BotOps directs you to them.
HQ must deploy the person-scoped paging API and configure its signed Twilio opt-out webhook before personal
verification/sending works. Demo and rehearsal mode send nothing.
