"""The tables behind peering between Tico installs (backend/federation*.py). They are made by migration
backend/migrations/0045_native_threads_schema.py; this module only describes them.

- `federation_keys`: this install's Ed25519 keys, the private half sealed by the vault. One is current (`retired_at`
  NULL); registering a new one with HQ resets every peering (HQ side and here).
- `federation_peerings`: one row per peering with another install, keyed to HQ's peering id. The peer's key is pinned
  here at this install's approval; an envelope from the peer is checked against that key, not against HQ's list.
- `federation_directory_entries`: HQ's published keys for installs this one met through a shared thread (author signatures).
- `federation_threads`: a thread shared over HQ. `thread_id` is this install's conversation; `origin_id` is the id the
  home gave it. The home numbers the stream (`high_seq`); a replica records how far it has applied the stream
  (`applied_through`). `removed_at_seq` is the membership tombstone on a removed install.
- `federation_members`: which installs the home shares a thread with, and from which home number (`joined_at_seq`). A
  message is for a member only when it was created after the member joined: `joined_message_seq` is the newest core
  message when the member joined, so eligibility is fixed at creation, not by when the home numbers it.
  `catch_up_after` is how far a catch-up the home is still paging for that member has got (NULL when none is).
- `federation_states.history_through`: HQ's time of the newest revoked peering a finished read of its history saw;
  `history_cursor` and `history_top` hold a read that was cut short.
- `federation_threads.subtree_seq`: how far the sub-threads of a shared thread have been looked at for deliveries.
- `federation_messages.sent_revision`: the newest revision of the message HQ took from this install (a tombstone is sent
  only when an earlier revision may have gone out).
- `federation_items`: the home's stream, one row per number. Each keeps the signed payload it was sent with, so a
  catch-up re-sends exactly what was signed.
- `federation_messages`: which local message an origin id is, its author and revision, and its home number once known.
- `federation_receipts`: every envelope processed, by (sender, envelope id), for replay refusal.
- `federation_outbox_items`: what this install still has to send: a delivery from core (a replica's post to the home), an
  item (the home's stream to a member), a catch-up request, or a refusal receipt for a message this install did not take.
  A delivery is rebuilt from the message as it is when it is sent; an item to a member the message was not for goes as
  a signed skip, so the member's stream has no hole. `thread_origin` is the root's origin id: a row waits behind an
  earlier row for the same peer and root, and only the first row of each such lane is sent.
- `federation_deferrals`: envelopes this install has acked to HQ but cannot apply yet (a rule check that has not run,
  a paused peering, or an envelope behind one of those in its own root). Kept per peer and root in arrival order; a
  lane waits for its first one, and other roots and peers are applied as they arrive.
"""
