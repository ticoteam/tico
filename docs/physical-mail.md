# Physical mail

> **Unreleased**: the install side and Settings > Mail are built; Tico Gateway's mailboxes are not on sale yet. The API
> below may change.

Tico Mail brings the company's paper mail into Tico. Each scanned letter is saved as a file, read, sorted by kind and
sent to whoever handles that kind. Legal papers reach the owner at once.

## Where letters come from

- **A Tico Gateway address.** Tico HQ keeps the mailbox (a shared or private street address, or an office lease, in a
  state). Each new letter arrives on the same Gateway queue as webhooks and Tico Team notices, so a Gateway key is
  all a server needs. The scan is fetched from Tico Gateway.
- **Your own provider, by email.** An owner or admin names one company email inbox (Email & Phone) as the mail-intake
  address: `PUT /api/v2/postal/intake` with `{"inbox_id": "..."}`, or `null` to stop. Have your mail provider send
  its scans there. Each PDF or image attached to a message in that inbox becomes a letter; other attachments and other
  inboxes are left alone.

## What happens to a letter

1. The scan is kept as a file on the letter's task (Library > Files), for good; mailbox providers keep scans for a
   limited time.
2. It is read through Tico Gateway (Low tier, which reads PDFs and images): sender, a one-line summary, the response
   deadline, if any.
3. One Decide call ([question set](../questions/README.md) `physical-mail`) says its kind.
4. It goes where its kind goes:

| Kind | Goes to |
|---|---|
| Legal (summons, lawsuit, subpoena, court papers) | The owner's Needs you, private, due on the deadline |
| Government or tax | The Finance bot, or the owner when there is none |
| Bill | The Finance bot, or the owner |
| Financial (statements, bank notices) | The Finance bot, or the owner |
| Check | The owner, private: depositing it needs their approval |
| Junk | Archived: no task; the scan is still kept |
| Other, or not sure | The Assistant, or the owner |
| Could not be read | The owner, private |

Legal goes to the owner whenever the reader or Decide says legal, however unsure: a missed summons costs more than
a wrong card. Junk is archived only when Decide is sure; anything else short of its threshold goes to the Assistant.
The routing lives in one table (`ROUTES` in `backend/postal_mail.py`) so it can become editable later.

A letter's text comes from outside the company. The reading prompt and the Decide question both treat it as data,
never as instructions, and each task says so.

## When something fails

A letter whose scan or reading fails is marked `failed` and tried again after 1 minute, 5 minutes, 30 minutes and
2 hours. After five tries it waits; an owner or admin retries it with `POST /api/v2/postal/items/{id}/retry`. On the
last try a scan that still cannot be read is filed for the owner unread, so nobody misses it. A retry never makes a
second task.

## Settings > Mail

Addresses (state, kind, the full address once active, registered agent, Form 1583 status) with open orders as
Pending; Get an address (owner or admin: state, kind and price; an office lease only in California, Delaware and
Florida); Bring your own (the intake inbox); Recent letters, with Retry on a failed one for an owner or admin.

## API, CLI and MCP

| | |
|---|---|
| `GET /api/v2/postal/items` (`status`, `kind`, `after`, `limit`) | Letters, newest first. Owners and admins see every letter; anyone else sees the letters whose task they can read. |
| `GET /api/v2/postal/items/{id}` | One letter: summary, kind, sender, deadline, task, file id and scan link, mailbox flags. |
| `POST /api/v2/postal/items/{id}/retry` | Try a failed letter again (owner or admin). |
| `GET /api/v2/postal/addresses` | The company's addresses at Tico Gateway, its orders, and prices when Tico Gateway sends them. |
| `POST /api/v2/postal/orders` | Order an address (`kind`: `shared`, `private` or `lease`; `state`; `company_name`), owner or admin, with an `Idempotency-Key`. Tico staff fulfil it. |
| `GET` / `PUT /api/v2/postal/intake` | The email-in inbox. |

Bots and people read letters with `hub mail list` and `hub mail read <id>` (MCP `hub_mail_list`, `hub_mail_read`).
