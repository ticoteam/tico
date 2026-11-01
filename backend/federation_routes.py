"""Federation's REST routes, the owner's side: this install's key, its peerings with other installs, and the threads it
shares with them. Owner only, reads and writes alike (a peering shows the other install's id). A write asks Tico HQ
first where it must, then records the local change through `store.write`, so it carries the audit row (H.event) and the
change event every write does. `/api/v2/federation/...` is the surface; the CLI and MCP tools call these routes.
"""
from fastapi import Query, Request

from . import federation as F
from . import federation_envelope as E
from . import federation_relay as R
from . import federation_sync as S
from . import federation_threads as T
from . import hook_relay as HR
from . import hubdb as H
from .auth import validate_identity
from .models import Contract
from .store import Problem

PAGE_LIMIT = 50          # peerings and threads per page, newest first; a read asks for up to PAGE_MAX
PAGE_MAX = 100
MEMBER_LIMIT = 20        # members listed per thread: 50 threads x 20 members stays inside the list byte budget
HQ_MESSAGES = {"unknown_install": "That install has no key with Tico HQ yet.",
               "no_key": "Register this install's key first.",
               "already_peered": "This install already has a peering with that one.",
               "key_changed": "That install's key changed since it was reviewed. Look again before approving.",
               "idempotency_conflict": "That Idempotency-Key was used for another request.",
               "not_found": "No such peering at Tico HQ.", "revoked": "That peering is revoked."}


class PeerBody(Contract):
    peer: str


class PauseBody(Contract):
    paused: bool


class InstallBody(Contract):
    install: str


class ReaderBody(Contract):
    actor: str


def install(app, settings, store, auth):
    relay = R.Relay(store, settings, app)
    app.state.federation_relay = relay

    def owner(request):
        who = request.state.identity
        with store.read_transaction() as c:
            validate_identity(c, who)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner may manage federation with other installs", 403)
        return who

    def link_of(c):
        link = relay.link(c)
        if link is None:
            raise Problem("gateway", "Connect the Tico Gateway in Settings > Gateway first: federation goes through Tico HQ.", 409)
        return link

    def hq(call):
        try:
            return call()
        except HR.Stop:
            raise Problem("gateway", "Tico HQ refused the Gateway key. Check it in Settings > Gateway.", 502) from None
        except HR.Failed as failed:
            if failed.status and failed.status < 500:
                raise Problem(failed.code, HQ_MESSAGES.get(failed.code, "Tico HQ refused the request."), failed.status) from None
            raise Problem("gateway", "Tico HQ did not answer. Try again in a minute.", 502) from None

    def view_of(c, row_id):
        row = F.peering_by_id(c, row_id)
        if row is None:
            raise Problem("not_found", "No such peering", 404)
        return row

    def shown(row):
        if row is None:                       # HQ's answer named no peering this server can keep
            raise Problem("gateway", "Tico HQ gave an answer this server cannot use. Try again.", 502)
        return F.show(row)

    def page(c, table, after, limit):
        """One keyset page of a table, newest first: rows below `after` (a rowid; None for the first page). `pos` is the
        cursor for the next page, or None when this page is the last."""
        rows = [dict(r) for r in c.execute(f"SELECT rowid AS pos, * FROM {table} WHERE rowid<? ORDER BY rowid DESC LIMIT ?",
                                           (after if after is not None else 2**62, limit + 1)).fetchall()]
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, (str(rows[-1]["pos"]) if more and rows else None)

    def cursor_of(value):
        if not value:
            return None
        if not value.isdigit():
            raise Problem("cursor", "A cursor is one from a page's next_cursor or threads_next_cursor", 422)
        return int(value)

    def guests_of(c, thread_id):
        """The remote people a thread here has as guests (`<actor>@<install>` rows), with their install's company name."""
        return [{"actor": r["actor"], "company": F.company_of(c, r["actor"].rsplit("@", 1)[1])}
                for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=? AND instr(actor, '@') > 0 "
                                   "ORDER BY actor LIMIT ?", (thread_id, MEMBER_LIMIT)).fetchall()]

    def threads_of(c, rows):
        me = F.install(c)
        out = []
        for row in rows:
            row.pop("pos", None)
            members = [dict(m) for m in c.execute(
                "SELECT m.install, m.status, m.joined_at_seq, m.removed_at_seq, COALESCE((SELECT p.peer_company FROM "
                "federation_peerings p WHERE p.peer_install=m.install AND p.status<>'revoked'), '') AS company "
                "FROM federation_members m WHERE m.thread_id=? ORDER BY m.install LIMIT ?", (row["thread_id"], MEMBER_LIMIT))]
            out.append({"thread_id": row["thread_id"], "home": row["home_install"] == me, "home_install": row["home_install"],
                        "applied_through": row["applied_through"], "high_seq": row["high_seq"],
                        "removed_at_seq": row["removed_at_seq"], "members": members if row["home_install"] == me else [],
                        "guests": guests_of(c, row["thread_id"])})
        return out

    def status_of(c, limit=PAGE_LIMIT, cursor="", threads_cursor=""):
        peering_rows, next_cursor = page(c, "federation_peerings", cursor_of(cursor), limit)
        thread_rows, threads_next = page(c, "federation_threads", cursor_of(threads_cursor), limit)
        peerings = [F.show(dict({k: v for k, v in r.items() if k != "pos"})) for r in peering_rows]
        return {"federation": relay.status(c), "peerings": peerings, "next_cursor": next_cursor,
                "threads": threads_of(c, thread_rows), "threads_next_cursor": threads_next}

    @app.get("/api/v2/federation")
    def status_read(request: Request, limit: int = Query(PAGE_LIMIT, ge=1, le=PAGE_MAX), cursor: str = "",
                    threads_cursor: str = ""):
        """This install's federation: its key, the Gateway, what waits at HQ, and one page of its peerings and of the
        threads it shares (newest first; `limit` up to 100). `next_cursor` and `threads_next_cursor` give the next page
        of each, or null on the last one. No key material. Owner only."""
        owner(request)
        with store.read() as c:
            return status_of(c, limit, cursor, threads_cursor)

    @app.post("/api/v2/federation/threads/{thread_id}/readers")
    def reader_add(request: Request, thread_id: str, body: ReaderBody):
        """Adds a local person to a thread on this install. A replica's readers are the people the admin adds here, and
        the thread's home mirrors its own members onto this install as guests. Owner only."""
        who = owner(request)
        actor = body.actor.strip()

        def add(c):
            result = T.add_reader(c, thread_id, actor)
            H.event(c, who.actor, "federation.reader_added", thread_id, {"actor": actor})
            return result
        return store.write(who, add)

    def keys(request, rotate):
        who = owner(request)
        with store.read() as c:
            link = link_of(c)
            if not F.install(c):
                raise Problem("not_set_up", "Connect the Tico Gateway first: federation needs this install's Gateway id", 409)
            has = F.key(c) is not None
        if rotate or not has:
            def make(c):
                key_id = F.make_key(c, relay.vault())
                H.event(c, who.actor, "federation.key_rotated" if has else "federation.key_created", key_id, {})
                return key_id
            store.write(who, make)
        with store.read() as c:
            current = F.key(c)
        hq(lambda: relay.register(link, current))
        if has:
            hq(lambda: relay.sync(link))             # a new key resets every peering at HQ: their state here follows
        with store.read() as c:
            return status_of(c)

    @app.post("/api/v2/federation/keys")
    def key_create(request: Request):
        """Makes this install's federation key if it has none, and registers it with Tico HQ. Owner only."""
        return keys(request, rotate=False)

    @app.post("/api/v2/federation/keys/rotate")
    def key_rotate(request: Request):
        """A new key; the old one is retired. Every peering needs approving again, on both sides. Owner only."""
        return keys(request, rotate=True)

    @app.post("/api/v2/federation/peerings")
    def peering_request(request: Request, body: PeerBody):
        """Asks another install to peer with this one. Needs an Idempotency-Key: a repeat with the same key is the same
        request. Owner only."""
        who = owner(request)
        key = request.headers.get("idempotency-key", "").strip()
        if not key or len(key) > 200:
            raise Problem("idempotency_key", "Provide an Idempotency-Key of 1-200 characters", 422)
        peer = body.peer.strip()
        if not E.INSTALL.fullmatch(peer):
            raise Problem("peer", "The other install's id starts with gi_ and has 24 hex digits", 422)
        with store.read() as c:
            link = link_of(c)
            if not F.install(c) or F.key(c) is None:
                raise Problem("not_set_up", "Register this install's federation key first", 409)
        view = hq(lambda: relay.request(link, peer, key))

        def record(c):
            row = R.record(c, view)
            if row is None:
                raise Problem("gateway", "Tico HQ gave an answer this server cannot use. Try again.", 502)
            H.event(c, who.actor, "federation.peering_requested", row["id"], {"peer": peer})
            return F.show(row)
        return store.write(who, record)

    @app.post("/api/v2/federation/peerings/{peering_id}/approve")
    def peering_approve(request: Request, peering_id: str):
        """Approves a peering, having reviewed the other install's key: this records that key as pinned, and envelopes from
        the other install must be signed with it from now on. Active once both installs approve. Owner only."""
        who = owner(request)
        with store.read() as c:
            row = dict(view_of(c, peering_id))
            link = link_of(c)
        found = hq(lambda: relay.directory(link, row["peer_install"]))
        view = hq(lambda: relay.approve(link, row["hq_peering_id"], found["key_id"]))

        def record(c):
            fresh = R.record(c, view, pin=(found["key_id"], found["public_key"]))
            F.note_approver(c, fresh["id"], who.actor)
            H.event(c, who.actor, "federation.peering_approved", peering_id, {"peer": row["peer_install"],
                                                                              "peer_key_id": found["key_id"]})
            return shown(fresh)
        return store.write(who, record)

    @app.post("/api/v2/federation/peerings/{peering_id}/pause")
    def peering_pause(request: Request, peering_id: str, body: PauseBody):
        """The kill switch: while paused, no envelope crosses this peering in either direction. Resuming repairs what was
        missed through a catch-up. Owner only."""
        who = owner(request)
        with store.read() as c:
            row = dict(view_of(c, peering_id))
            link = link_of(c)
        view = hq(lambda: relay.pause(link, row["hq_peering_id"], body.paused))

        def record(c):
            fresh = R.record(c, view)
            H.event(c, who.actor, "federation.peering_paused" if body.paused else "federation.peering_resumed", peering_id,
                    {"peer": row["peer_install"]})
            return shown(fresh)
        return store.write(who, record)

    @app.post("/api/v2/federation/peerings/{peering_id}/revoke")
    def peering_revoke(request: Request, peering_id: str):
        """Ends a peering. Nothing more crosses it; a new request starts over and needs both approvals again. Owner only."""
        who = owner(request)
        with store.read() as c:
            row = dict(view_of(c, peering_id))
            link = link_of(c)
        view = hq(lambda: relay.revoke(link, row["hq_peering_id"]))

        def record(c):
            fresh = R.record(c, view)
            H.event(c, who.actor, "federation.peering_revoked", peering_id, {"peer": row["peer_install"]})
            return shown(fresh)
        return store.write(who, record)

    @app.post("/api/v2/federation/threads/{thread_id}/installs")
    def share_install(request: Request, thread_id: str, body: InstallBody):
        """Shares a thread of this install (audience members) with another install it has an active peering with. The
        other install gets the thread from now on, not its history. Owner only."""
        who = owner(request)
        install_id = body.install.strip()
        if not E.INSTALL.fullmatch(install_id):
            raise Problem("install", "The other install's id starts with gi_ and has 24 hex digits", 422)

        def share(c):
            result = S.share(c, relay.vault(), R.now(), thread_id, install_id)
            H.event(c, who.actor, "federation.thread_shared", thread_id, {"install": install_id,
                                                                          "joined_at_seq": result["joined_at_seq"]})
            return result
        return store.write(who, share)

    @app.delete("/api/v2/federation/threads/{thread_id}/installs/{install_id}")
    def unshare_install(request: Request, thread_id: str, install_id: str):
        """Removes an install from a thread this install is the home of. It gets the removal and nothing after. Owner only."""
        who = owner(request)
        if not E.INSTALL.fullmatch(install_id):
            raise Problem("install", "The other install's id starts with gi_ and has 24 hex digits", 422)

        def unshare(c):
            result = S.unshare(c, relay.vault(), R.now(), thread_id, install_id)
            H.event(c, who.actor, "federation.thread_unshared", thread_id, {"install": install_id,
                                                                            "removed_at_seq": result["removed_at_seq"]})
            return result
        return store.write(who, unshare)

    return relay
