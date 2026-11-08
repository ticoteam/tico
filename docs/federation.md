# Federation: shared threads between Tico installs

Two Tico installs can share a thread. Messages in a shared thread reach the other install, and its people and bots can
reply in it. Federation goes through Tico HQ: each install talks to HQ only, never directly to the other install.

Terms: an **install** is one Tico (`gi_` and 24 hex digits, the Gateway id). A **peering** is an approved link between
two installs. A **home** install owns a shared thread; the others hold replicas.

## Set up a peering

Both installs need the Tico Gateway connected (Settings > Gateway), because federation goes through HQ.

1. Each install makes its key once: `hub peer key`. The key is registered with HQ, and its public half is published there.
2. One install asks the other: `hub peer request gi_<other>`. HQ records a pending peering.
3. The other install reviews the key and approves: `hub peer approve <peering>`.
4. The first install approves too, having checked the key it sees. Only then is the peering **active**.

Approval pins the other install's public key. Envelopes must be signed with the pinned key from then on.
`hub peer list` shows this install's view of each peering's status.

The request and each approval carry this install's company name (its settings), and the other install shows it as the
peering's `company`. The install that approves a peering is its accepting admin: that person reads the threads the
peering brings in.

In the app, the owner or an admin does all of this in Library > Connections > Other companies: this install's id and key, Connect a
company (by install id), and Approve, Pause, Resume or Revoke on each peering. A thread's Who can read sheet shows
Shared with, where they add or remove an install.

## Share a thread

`hub peer share <thread> <install>` gives an active peer a thread this install owns (its audience is `members`). You must
read and manage the thread: an admin cannot share, or see listed, a members thread they are not in. While it is shared, the
thread stays a members thread: neither side can open it to the company or a team. The home's own member list is the
thread's: another company's people come from their posts and its copies mirror them. Companies join and leave only through
`share` and `unshare`, never the thread's member list.
The peer gets the thread from that moment, not its history. A message reaches it only if it was posted after the peer
joined; a sub-thread reply counts from its parent's time. Each message goes out in its current state: an edit made before
the first send sends the new text, and a message deleted before anything left is never sent. A message restricted to a
task or a meeting is never sent.

`hub peer unshare <thread> <install>` removes an install. It gets the removal and nothing after it.

On the receiving install, a shared thread is a replica. Only the accepting admin can read it at first. Other local people
get a 404 until an admin who reads it adds them: `hub peer add-reader <thread> human:<name>`, or
`POST /api/v2/federation/threads/{thread_id}/readers` with `{"actor": "human:<name>"}`. Once no local person is left in a
replica, any admin can add one. Readers are added only
to a replica of a members thread. The home's own thread, a DM, a local thread, or a thread this install was removed from
is refused with a 409.

## Pause, resume, revoke, rotate

- `hub peer pause <peering>` is the kill switch. While paused, nothing crosses the peering in either direction.
  Messages for that peer wait on the sending side and go out on resume. Envelopes the peer sends are held here until
  resume. Other peers and other roots keep flowing.
- `hub peer resume <peering>` lets it carry again. What waited is applied in order, and what was missed is caught up.
- `hub peer revoke <peering>` ends the peering. A new request starts over and needs both approvals.
- `hub peer rotate-key` makes a new key and retires the old one. Every peering needs approving again, on both sides.

## Commands and routes

| CLI | MCP tool | REST (`/api/v2/federation`) |
|---|---|---|
| `hub peer list` | `hub_peer_list` | `GET` (paged: `limit`, `cursor`, `threads_cursor`) |
| `hub peer key` | `hub_peer_key` | `POST /keys` |
| `hub peer rotate-key` | `hub_peer_rotate_key` | `POST /keys/rotate` |
| `hub peer request <install>` | `hub_peer_request` | `POST /peerings` (needs `Idempotency-Key`) |
| `hub peer approve <peering>` | `hub_peer_approve` | `POST /peerings/{id}/approve` |
| `hub peer pause` / `resume <peering>` | `hub_peer_pause` / `hub_peer_resume` | `POST /peerings/{id}/pause` |
| `hub peer revoke <peering>` | `hub_peer_revoke` | `POST /peerings/{id}/revoke` |
| `hub peer share <thread> <install>` | `hub_peer_share` | `POST /threads/{id}/installs` |
| `hub peer unshare <thread> <install>` | `hub_peer_unshare` | `DELETE /threads/{id}/installs/{install}` |
| `hub peer retry <thread> <install>` | `hub_peer_retry` | `POST /threads/{id}/installs/{install}/retry` |
| `hub peer add-reader <thread> <actor>` | `hub_peer_add_reader` | `POST /threads/{id}/readers` |

Every route is for the owner or an admin, except `retry`, which is for whoever manages the thread. A thread's routes also
ask that the caller reads and manages it, and `GET` lists only the threads the caller may read. BotOps does each of these
with the rights of the person who asked. Each write records an audit row and a change event, like other admin settings.

## When a send fails

A message's delivery to another install stays `pending` until Tico HQ takes the envelope, then it is `delivered`. If HQ
refuses it for good, the delivery is `failed`. If sends keep failing (about 25 minutes), or one has failed, one Needs you
item, **Not sent**, goes to whoever manages the thread, one per thread and company. It clears when the send goes through
(Retry in the thread's Shared with, or `hub peer retry`), when the message is edited or deleted, or when that company leaves
the thread. A paused or revoked peering lists nothing: its messages wait for it.

## How it works

- **Envelopes.** Each message or control request is one envelope: the recipient, a unique envelope id, the sent time, the
  sender's key id and an Ed25519 signature over those and a digest of the body. HQ checks the peering, the pause state
  and the signature, then stores and forwards the envelope. The recipient checks everything again with the key it pinned.
- **Replay.** An envelope older than 5 minutes (either way) is refused. An envelope id the recipient has already
  received is refused the second time.
- **Namespace.** An actor is named `human:<id>@<install>` or `bot:<slug>@<install>`. Only the install named after the `@`
  may send for that actor, so `human:ana@gi_x` cannot come from any other install. A local actor is qualified with its
  own install when it leaves.
- **Remote authors.** A message from another install is written as its real author, `human:ana@gi_x`, a guest on the
  receiving install. Each duplicate (origin id and revision) is written once.
- **Origin binding.** An origin id belongs to the install, author and thread that wrote it. A peer can edit or delete only
  its own messages, and an origin id another author or thread already uses is refused.
- **Rules and screening.** A message from another install is checked before anything is written. The org rules and the
  injection screener (source `federation`, default `warn`) run first, outside the write, and the verdict is applied inside
  it. A message the rules block is written nowhere, and the sender gets a refusal. It leaves a hold: the reviewer's Send
  anyway posts it as its author, and on the home it goes on to the others as the author's signed envelope. A later revision
  is checked afresh, and closes holds on older ones. The injection screen goes first, so a hold never carries a text it
  stopped. A check that did not run (no verdict, or the rules could not check) is never treated as a pass: the receiver
  holds that envelope, and the later envelopes for the same peer and root wait behind it. Other roots and peers are not
  held. Outbound needs no extra step: a local member's post passes the org rule check in `threads.post` before it is
  stored, so it is checked before it can replicate.
- **Refusals.** A receiver that refuses a message (rules, namespace or unknown thread) sends a signed refusal to the
  install that sent it. That install posts a quiet note to the message's author in the thread, like a held message's note.
  If the author can no longer read the thread, nothing is posted and the refusal is recorded as `refused`.
- **Other company's words stay in.** A thread shared with another company can't be linked to Slack, on either side, and
  a thread linked to Slack can't be shared until it is unlinked (`shared_thread`, `slack_linked`). A message from another
  company never goes to a Slack channel, whatever the link. Webhooks and email digests leave out a title that may be
  theirs: any title on a copy, and a sub-thread's title on a shared thread (the first line of its parent message). A
  webhook names a remote author by install only. Message text never goes to either. A sub-thread is not shared by itself
  (`sub_thread`): it goes with its thread. Only the home can add a company, so a replica can't pass a thread on.
- **Revoked.** When the other company revokes a peering, this install learns it from HQ's list of revoked peerings, read
  after the working list, once a minute. Nothing more is sent to that company. Its peering shows as Revoked, and a thread
  shared with it no longer has its company name. An older HQ lists revoked peerings in the working list instead.
- **Order.** The home numbers each shared thread's messages (`home_seq`). A replica applies them in that order. An edit
  or delete for a message the replica does not have yet waits for a catch-up, and is never made into a message: only the
  first send of a message (`first`) can create one, whatever its revision. A catch-up asks the home for everything after
  the last `home_seq` the replica applied. The home sends it a page at a time from a stored place, and the next page goes
  when the last has left, so nothing is missed however long a peer was offline. A replica that saw a gap asks again until
  it is closed, even if nothing more arrives, and a replica of a thread quiet for a day asks once in case the last item
  never reached it. The home refuses an edit of a message it does not have.
- **Lanes.** Order is kept per peer and per root, in both directions. A lane is one peer and one root. Outbound, a
  message waits behind an earlier one for the same peer and root, and only the first is sent next. A send HQ refuses
  for a reason that will not change (a 4xx other than a pause, a timeout or a rate limit) is settled as failed; a
  pause or any other failure waits and is tried again. Inbound, an envelope this install cannot apply yet is held (see
  below), and the later envelopes for that peer and root wait behind it. Nothing else waits: another root or another
  peer keeps flowing.
- **Held envelopes.** This install keeps what it cannot apply yet in its own database, in arrival order, and acks it to
  HQ, so the mailbox moves on. Each held envelope is retried with a backoff (30 seconds, doubling, at most 5 minutes),
  so a rule check that finishes can take up to 5 minutes to apply. `GET /api/v2/federation` shows how many are held
  (`held`).
- **Membership.** The home is the membership authority. Its member list goes out as a signed membership item whenever it
  changes, and a replica mirrors it as guest rows (`<actor>@<home install>`). Guests from a third install are in the same
  roster as `<actor>@<that install>`, so every replica can name their author. A removed install gets a membership
  tombstone, and nothing after it.
- **Sub-threads.** Messages in a shared thread's sub-threads are federated too. Each one carries its parent's origin id, so
  a replica hangs the reply from the same message. Federation queues these itself, because core writes outside deliveries
  only for a root's own messages. Keeper notes (system notices) are never sent.
- **Wake.** A replicated message never starts a run for a remote guest bot. A local post cannot address one either: core
  refuses a bot name it does not know. A local bot follows the normal rule.

## Limits

- An envelope body is at most 180 KB. HQ accepts at most 256 KB per envelope.
- A peering carries at most 3,600 envelopes an hour. HQ holds at most 2,000 envelopes (64 MB) per install until it pulls them.
- `GET /api/v2/federation` pages its peerings and its shared threads separately, newest first. `limit` is 1 to 100 (default
  50). Pass `next_cursor` as `cursor` and `threads_next_cursor` as `threads_cursor` for the next page; a null cursor is the
  last page. A thread lists at most 20 members and 20 guests.

## What HQ can see

HQ stores each envelope body as opaque bytes until the recipient pulls and acknowledges it. HQ does not parse the body,
but the body is **not end-to-end encrypted**: an operator with access to HQ's storage could read it while it waits.
Treat HQ as a relay you trust for delivery, not as a place a shared thread is kept private from.

## Not in this version

- A later member's manager cannot read a replica yet. Only the accepting admin and the people an admin adds can.
- A refusal is passed on one hop only.
- Core's member lists do not show company names yet. `GET /api/v2/federation` does (`company` on each peering, `members` and
  `guests` on each thread).
- A thread's history from before it was shared is never sent to a later member.
- Held envelopes live in this install's database. If that database is lost before they are applied, they are lost with
  it: the sender does not keep a copy once HQ has taken the envelope.
