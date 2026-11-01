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

## Share a thread

`hub peer share <thread> <install>` gives an active peer a thread this install owns (its audience is `members`).
The peer gets the thread from that moment, not its history. Messages, edits and deletes after that point reach it.

`hub peer unshare <thread> <install>` removes an install. It gets the removal and nothing after it.

## Pause, resume, revoke, rotate

- `hub peer pause <peering>` is the kill switch. While paused, nothing crosses the peering in either direction.
  Messages wait on the sending side.
- `hub peer resume <peering>` lets it carry again. What was missed is caught up.
- `hub peer revoke <peering>` ends the peering. A new request starts over and needs both approvals.
- `hub peer rotate-key` makes a new key and retires the old one. Every peering needs approving again, on both sides.

## Commands and routes

| CLI | MCP tool | REST (`/api/v2/federation`) |
|---|---|---|
| `hub peer list` | `hub_peer_list` | `GET` |
| `hub peer key` | `hub_peer_key` | `POST /keys` |
| `hub peer rotate-key` | `hub_peer_rotate_key` | `POST /keys/rotate` |
| `hub peer request <install>` | `hub_peer_request` | `POST /peerings` (needs `Idempotency-Key`) |
| `hub peer approve <peering>` | `hub_peer_approve` | `POST /peerings/{id}/approve` |
| `hub peer pause` / `resume <peering>` | `hub_peer_pause` / `hub_peer_resume` | `POST /peerings/{id}/pause` |
| `hub peer revoke <peering>` | `hub_peer_revoke` | `POST /peerings/{id}/revoke` |
| `hub peer share <thread> <install>` | `hub_peer_share` | `POST /threads/{id}/installs` |
| `hub peer unshare <thread> <install>` | `hub_peer_unshare` | `DELETE /threads/{id}/installs/{install}` |

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
- **Order.** The home numbers each shared thread's messages (`home_seq`). A replica applies them in that order. An edit
  or delete for a message the replica does not have yet waits for a catch-up. A catch-up asks the home for everything
  after the last `home_seq` the replica applied.
- **Membership.** The home is the membership authority. A removed install gets a membership tombstone, and nothing after it.

## Limits

- An envelope body is at most 180 KB. HQ accepts at most 256 KB per envelope.
- A peering carries at most 3,600 envelopes an hour. HQ holds at most 2,000 envelopes (64 MB) per install until it pulls them.
- `GET /api/v2/federation` lists at most 50 peerings and 50 threads, and 20 members per thread. It is not cursor-paged.

## What HQ can see

HQ stores each envelope body as opaque bytes until the recipient pulls and acknowledges it. HQ does not parse the body,
but the body is **not end-to-end encrypted**: an operator with access to HQ's storage could read it while it waits.
Treat HQ as a relay you trust for delivery, not as a place a shared thread is kept private from.

## Not in this version

- Rules for what leaves an install, and for guests from another install, are not yet applied to shared threads
  (they depend on the rules work for people and bots).
- A thread's history from before it was shared is never sent to a later member.
- A sender is not told when the other install refuses a message. It can learn of a refusal only from the message never
  appearing.
- The status read is capped (see Limits), not cursor-paged.
