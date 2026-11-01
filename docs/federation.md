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

In the app, the owner does all of this in Library > Connections > Other companies: this install's id and key, Connect a
company (by install id), and Approve, Pause, Resume or Revoke on each peering. A thread's Who can read sheet shows
Shared with, where the owner adds or removes an install.

## Share a thread

`hub peer share <thread> <install>` gives an active peer a thread this install owns (its audience is `members`).
The peer gets the thread from that moment, not its history. Messages, edits and deletes after that point reach it.

`hub peer unshare <thread> <install>` removes an install. It gets the removal and nothing after it.

On the receiving install, a shared thread is a replica. Only the accepting admin can read it at first. Other local people
get a 404 until an admin adds them: `hub peer add-reader <thread> human:<name>`, or
`POST /api/v2/federation/threads/{thread_id}/readers` with `{"actor": "human:<name>"}` (owner only).

## Pause, resume, revoke, rotate

- `hub peer pause <peering>` is the kill switch. While paused, nothing crosses the peering in either direction.
  Messages wait on the sending side.
- `hub peer resume <peering>` lets it carry again. What was missed is caught up.
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
| `hub peer add-reader <thread> <actor>` | `hub_peer_add_reader` | `POST /threads/{id}/readers` |

Every route is owner only. Each write records an audit row and a change event, like the other owner settings.

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
- **Rules.** A message from another install goes through inbound screening and the org rules, as its remote author.
  A message the rules block is written nowhere. The receiving install sends back a refusal.
  Outbound is covered by the normal write check: a local member's post passes the org rule check in `threads.post`
  before it is stored, so it is checked before it can replicate.
- **Refusals.** A receiver that refuses a message (rules, namespace or unknown thread) sends a signed refusal to the
  install that sent it. That install posts a quiet note to the message's author in the thread, like a held message's note.
  If the author can no longer read the thread, nothing is posted and the refusal is recorded as `refused`.
- **Order.** The home numbers each shared thread's messages (`home_seq`). A replica applies them in that order. An edit
  or delete for a message the replica does not have yet waits for a catch-up. A catch-up asks the home for everything
  after the last `home_seq` the replica applied.
- **Membership.** The home is the membership authority. Its member list goes out as a signed membership item whenever it
  changes, and a replica mirrors it as guest rows (`<actor>@<home install>`). A removed install gets a membership
  tombstone, and nothing after it.
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
- A held federated message has no "send anyway". It stays held.
- Core's member lists do not show company names yet. `GET /api/v2/federation` does (`company` on each peering, `members` and
  `guests` on each thread).
- The org rule check runs inside the receive transaction when rules are in force.
- A thread's history from before it was shared is never sent to a later member.
