# Physical mail

> **Unreleased**: the install side and Settings > Mail are built; Tico Gateway's mailboxes are not on sale yet. The API
> below may change.

Tico Mail brings the company's paper mail into Tico. Each scanned letter is saved as a file, read, sorted by kind and
sent to whoever handles that kind. Legal papers reach the owner at once.

## Where letters come from

- **A Tico Gateway address.** Tico HQ keeps the mailbox (a shared or private mailing address, with a registered agent,
  in a state). Each new letter arrives on the same Gateway queue as webhooks and Tico Team notices, so a Gateway key is
  all a server needs. The scan is fetched from Tico Gateway.

  Prices: shared $10 a month per recipient, private from $25 a month; scans $1 a letter for up to 10 pages, then
  $0.25 a page. Tico doesn't deposit checks: ask Tico support to forward one, or deposit it yourself.
- **Your own provider, by email.** An owner or admin names one company email inbox (Email & Phone) as the mail-intake
  address and who sends it scans: `PUT /api/v2/postal/intake` with `{"inbox_id": "...", "senders":
  ["scans@provider.com", "@provider.com"]}` (at least one address or `@domain`), or `{"inbox_id": null}` to stop.
  Have your mail provider send its scans there. Each PDF or image attached to a message from a listed sender becomes a
  letter; other senders, other attachments and other inboxes are left alone. The sender is what the message claims,
  so the list keeps out strangers and floods; a scan is still read as outside text.

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
| Legal (summons, lawsuit, subpoena, court papers) | The owner's Needs you, private, due on the deadline |
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
The routing lives in one table (`ROUTES` in `backend/postal_mail.py`) so it can become editable later.

A letter's text comes from outside the company. The reading prompt and the Decide question both treat it as data,
never as instructions, and each task says so.

## Who sees a letter

A filed letter shows to whoever may read its task. Every kind but Other is a private task (account numbers and amounts
are not the whole company's to read), so it shows only to the owner and the bot it went to, not to admins. A letter with no task (just in, failed, or archived as junk) shows in full only to the
owner; admins see that it came and its status, so they can retry it, but not its sender, summary or scan. The scan
download (`/api/v2/files/{id}`) follows the same rule, and so do the hub tools.

## When something fails

A letter whose scan or reading fails is marked `failed` and tried again after 1 minute, 5 minutes, 30 minutes and
2 hours. After five tries it waits; an owner or admin retries it with `POST /api/v2/postal/items/{id}/retry`. On the
last try a scan that still cannot be read is filed for the owner unread, so nobody misses it. A retry never makes a
second task.

## Settings > Mail

Addresses (state, kind, the full address once active, registered agent, Form 1583 status) with open orders as
Pending; Get an address (owner or admin: state, kind, monthly and scan prices); Bring your own (the intake
inbox); Recent letters, with Retry on a failed one for an owner or admin.

## API, CLI and MCP

| | |
|---|---|
| `GET /api/v2/postal/items` (`status`, `kind`, `after`, `limit`) | Letters, newest first, as [Who sees a letter](#who-sees-a-letter) says; a status-only row has `redacted: true`. |
| `GET /api/v2/postal/items/{id}` | One letter: summary, kind, sender, deadline, task, file id and scan link, mailbox flags, `contains_checks`. |
| `POST /api/v2/postal/items/{id}/retry` | Try a failed letter again (owner or admin). |
| `GET /api/v2/postal/addresses` | The company's addresses at Tico Gateway, its orders, and prices when Tico Gateway sends them. |
| `POST /api/v2/postal/orders` | Order an address (`kind`: `shared` or `private`; `state`; `company_name`), owner or admin, with an `Idempotency-Key`. Tico staff fulfil it. |
| `GET` / `PUT /api/v2/postal/intake` | The email-in inbox. |

Bots and people read letters with `hub mail list` and `hub mail read <id>` (MCP `hub_mail_list`, `hub_mail_read`).
