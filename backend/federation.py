"""This install's federation state: its Ed25519 key and its peerings with other installs. Owner-only writes come through
backend/federation_routes.py; the HQ side is backend/federation_relay.py. Nothing here makes a network call.

A peering is pinned at approval: this install records the peer's key id and public key when it approves, and an envelope
from the peer is checked against that pin. HQ's list of keys can change under it (a rotation resets the peering to
pending on HQ), so the pin never follows HQ silently.
"""
import base64

from . import federation_envelope as E
from . import gateway_connection as GC
from . import hubdb as H
from .store import Problem

CONTEXT = "tico-federation-key"      # what the vault seals this install's private key for
PEERINGS_PAGE = 50                   # a status read lists at most this many peerings, newest first


def install(c):
    """This install's Gateway id (`gi_...`), or '' before it has signed up with Tico HQ."""
    return GC.install_id(c)


def key(c):
    """This install's current key row, or None."""
    row = c.execute("SELECT * FROM federation_keys WHERE retired_at IS NULL ORDER BY created_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def make_key(c, vault):
    """A new current key. The one before it is retired; registering the new one with HQ is the caller's (relay) step."""
    if not vault or not vault.cipher.configured:
        raise Problem("vault", "This server has no credential vault, so it cannot keep a federation key. Set one up first.", 409)
    private = E.generate()
    raw = E.raw_public(private)
    key_id = E.key_id_of(raw)
    sealed = vault.cipher.seal(c, CONTEXT, base64.b64encode(E.private_bytes(private)).decode("ascii"))
    now = H.now()
    c.execute("UPDATE federation_keys SET retired_at=? WHERE retired_at IS NULL", (now,))
    c.execute("INSERT INTO federation_keys(key_id,public_key,sealed_private_key,registered_at,created_at,retired_at) "
              "VALUES(?,?,?,NULL,?,NULL)", (key_id, base64.b64encode(raw).decode("ascii"), sealed, now))
    return key_id


def private(c, vault, row):
    """The private key of a key row, unsealed. Only for signing, inside the call that signs."""
    raw = base64.b64decode(vault.cipher.unseal(c, CONTEXT, row["sealed_private_key"]))
    return E.private_from(raw)


def public_raw(row):
    return base64.b64decode(row["public_key"])


def peering(c, install_id):
    """The live (not revoked) peering with an install, or None."""
    row = c.execute("SELECT * FROM federation_peerings WHERE peer_install=? AND status<>'revoked'", (install_id,)).fetchone()
    return dict(row) if row else None


def company(c):
    """This install's display name (its settings' company name), as the relay last recorded it. Peers see it on the
    peering, and the home sends it in each membership item."""
    row = c.execute("SELECT company FROM federation_states WHERE singleton=1").fetchone()
    return row["company"] if row else ""


def set_company(c, name):
    c.execute("INSERT INTO federation_states(singleton,company) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET "
              "company=excluded.company WHERE federation_states.company<>excluded.company", (name,))


def company_of(c, install_id):
    """The company name a peer gave for its live peering, or ''."""
    row = c.execute("SELECT peer_company FROM federation_peerings WHERE peer_install=? AND status<>'revoked'",
                    (install_id,)).fetchone()
    return row["peer_company"] if row else ""


def remember_company(c, install_id, name):
    """A peer's company name, from a membership item it signed. Kept on its live peering."""
    if isinstance(name, str) and 0 < len(name.strip()) <= 120:
        c.execute("UPDATE federation_peerings SET peer_company=? WHERE peer_install=? AND status<>'revoked'",
                  (name.strip(), install_id))


def note_approver(c, row_id, actor):
    """The local person who approved the peering. They read the threads it brings in: their admin accepted the peer."""
    c.execute("UPDATE federation_peerings SET approved_by=COALESCE(approved_by, ?) WHERE id=?", (actor, row_id))


def peering_by_id(c, peering_id):
    row = c.execute("SELECT * FROM federation_peerings WHERE id=?", (peering_id,)).fetchone()
    return dict(row) if row else None


def active(c, install_id):
    """The peering when envelopes may cross with that install now: active, its key pinned, and not paused here or there."""
    row = peering(c, install_id)
    if row and row["status"] == "active" and row["peer_public_key"] and not row["paused_at"]:
        return row
    return None


def peerings(c, limit=PEERINGS_PAGE):
    rows = c.execute("SELECT * FROM federation_peerings ORDER BY updated_at DESC, id LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def show(row):
    """A peering as the API shows it: no key material, only the ids."""
    return {"id": row["id"], "peer": row["peer_install"], "direction": row["direction"], "status": row["status"],
            "approved": row["approved_at"] is not None, "paused": row["paused_at"] is not None,
            "paused_by": row["paused_by"], "peer_key_id": row["peer_key_id"], "company": row["peer_company"] or "",
            "updated": row["updated_at"]}


def new_id(prefix):
    return prefix + H.new_id()
