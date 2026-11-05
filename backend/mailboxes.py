"""Settings > Mailboxes: which inboxes Tico uses and who may use each (ui/app/mailboxes.js, ui/app/mailbox.js).

GET /api/v2/mailboxes is the page: for an admin every mailbox with its person, status and routines; for everyone their
own mailbox and the ones shared with them. One mailbox's page lists who can use it (Read / Read and write) and its
routines (message automations whose trigger inbox is this address; they are switched on and off with the automation
routes). Who holds what, and who may grant, revoke, share and stop, is backend/mailbox_access.py's rule, which also
writes those changes' events; this module reads it and calls it. Every write is a person acting as themselves: never a
bot, BotOps or the Assistant (backend/assistant.py NEVER)."""
import json
import re

from fastapi import Request

from . import hubdb as H
from . import mailbox_access as MA
from . import people as P
from .models import MailboxAccessCreate, MailboxAccessUpdate, MailboxCreate
from .store import Problem

PATH = "/api/v2/mailboxes"
PROVIDER = "google_workspace"
ADDRESS = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
USED_BY_NAMES = 3


def _person(who):
    """A person acting as themselves, in Tico or with their own token."""
    if who.role not in ("owner", "human") or who.via or who.task_actor:
        raise Problem("forbidden", "Only a person signed in to Tico themselves changes who can use a mailbox", 403)


def _admin(c, who):
    from .teammate_access import is_admin
    return who.role in ("owner", "human") and is_admin(c, who)


def _me(who):
    return H.actor_id(who.actor) if who.role in ("owner", "human") else ""


def _box(c, mid):
    row = c.execute("SELECT * FROM mailboxes WHERE id=?", (str(mid or ""),)).fetchone()
    if not row:
        raise Problem("not_found", "No such mailbox", 404)
    return dict(row)


def _manages(c, who, box):
    """Whether `who` may change who uses `box`: MA's own check (its person for a personal mailbox, an admin for a shared
    inbox; nobody while it awaits its person or once it is stopped), asked without raising."""
    if box["status"] != "active":
        return False
    try:
        MA._manager(c, who, box, "change who uses this mailbox")
    except Problem:
        return False
    return True


def _routines(c):
    """{address: [{id, name, enabled, flow}]}: the live message automations, by the inbox their trigger hears."""
    out = {}
    for row in c.execute("SELECT id, name, enabled, flow FROM automations WHERE trigger_type='message' AND "
                         "deleted_at IS NULL ORDER BY name"):
        try:
            flow = json.loads(row["flow"])
        except ValueError:
            continue
        inbox = str(((flow or {}).get("trigger") or {}).get("inbox") or "").strip().lower()
        if inbox:
            out.setdefault(inbox, []).append({"id": row["id"], "name": row["name"], "enabled": bool(row["enabled"]),
                                              "flow": flow})
    return out


def _table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _failing(c):
    """{address: {reason, error}} for each mailbox a computer reports it cannot read (google_rights.counted)."""
    from . import google_rights
    out = {}
    if not _table(c, "connector_reports"):
        return out
    for row in google_rights.counted(c, "mail", H.now()):
        try:
            reason = json.loads(row["failing"] or "{}").get("reason") or ""
        except ValueError:
            reason = ""
        address = row["account"].lower()
        # One computer refusing the sign-in outranks another's passing network error: only a person fixes it.
        if address not in out or reason == "signin":
            out[address] = {"reason": "signin" if reason == "signin" else "failed", "error": row["error"][:300]}
    return out


def _state(c, box, live):
    from .google_workspace import active
    if box["status"] != "active":
        return "stopped"
    if box["kind"] == "personal" and not box["person_id"]:
        return "awaiting_person"
    key = active(c)
    covered = bool(key) and key["status"] == "connected" and box["address"].split("@")[-1] == key["domain"]
    return "connected" if covered or box["address"] in live else "not_connected"


class _Reader:
    """What a page read needs once: the roster, the live Connect Google grants and the routines."""

    def __init__(self, c, who):
        from .google_workspace import _live_mailboxes
        from .views import roster
        self.c, self.who, self.roster = c, who, roster(c)
        self.live, self.routines = _live_mailboxes(c), _routines(c)
        self.facts, self.principal = MA._Facts(c), MA.principal(c, who)
        self.synced = {r["address"]: r for r in c.execute(
            "SELECT address, synced_at, newest_epoch FROM mail_mailboxes")} if _table(c, "mail_mailboxes") else {}
        self.failing = _failing(c)
        self.grants = {}
        for row in c.execute("SELECT mailbox_id, grantee_type, grantee_id FROM mailbox_accesses ORDER BY created_at"):
            self.grants.setdefault(row["mailbox_id"], []).append((row["grantee_type"], row["grantee_id"]))
        self._bot_names = None

    def bot_name(self, bid):
        if self._bot_names is None:
            self._bot_names = {b["id"]: b.get("display_name") or b["slug"] for b in H.bots(self.c)}
        return self._bot_names.get(bid, bid)

    def used_by(self, box):
        """Who may use `box` beside admins: its person and every grant, counted, with the first few names."""
        people, bots = [], []
        if box["kind"] == "personal" and box["person_id"]:
            people.append(self.person_name(box["person_id"]))
        for kind, gid in self.grants.get(box["id"], []):
            (people if kind == "person" else bots).append(
                self.person_name(gid) if kind == "person" else self.bot_name(gid))
        return {"people": len(people), "bots": len(bots), "names": (people + bots)[:USED_BY_NAMES]}

    def sync(self, box):
        """The last successful read of `box` and, while a computer reports it failing, why: `signin` when Google
        refused the sign-in (only a person signing in again fixes it), else `failed`."""
        seen = self.synced.get(box["address"])
        failing = self.failing.get(box["address"])
        return {"synced_at": seen["synced_at"] if seen else None,
                "newest_epoch": seen["newest_epoch"] if seen else None,
                "sync": (failing or {}).get("reason") or "",
                "sync_error": (failing or {}).get("error") or ""}

    def level(self, box):
        """What the viewer holds on `box` (MA.level, with the roster and admins read once for the page)."""
        if self.principal.kind == "none":
            return "none"
        return MA._level(self.facts, self.principal, box["address"], box)

    def person_name(self, pid):
        found = P.person(pid, self.roster) if pid else None
        return (found or {}).get("name") or pid or ""

    def row(self, box):
        from .google_workspace import covers
        return {"id": box["id"], "address": box["address"], "provider": box["provider"], "kind": box["kind"],
                "person_id": box["person_id"], "person_name": self.person_name(box["person_id"]),
                "connection": "workspace" if covers(self.c, box["address"]) else "personal", "status": box["status"],
                "state": _state(self.c, box, self.live),
                "level": self.level(box),
                "routines": [{"id": r["id"], "name": r["name"]} for r in self.routines.get(box["address"], [])],
                "used_by": self.used_by(box), **self.sync(box)}


def _company(c):
    from .google_workspace import active, pending, permissions
    key = active(c) or pending(c)
    on = permissions(c)
    # What Tico may do in a mailbox someone has Read and write on: reading always, the rest the company's switches.
    may = {"read": True, "organize": bool(on.get("organize_mail")), "send": bool(on.get("send_mail")),
           "change_calendar": bool(on.get("change_calendar"))}
    return {"domain": (key or {}).get("domain") or "", "may": may}


def listing(c, who):
    """The page: an admin's table of every mailbox, else this person's own mailbox and what is shared with them."""
    read = _Reader(c, who)
    admin = _admin(c, who)
    boxes = [dict(r) for r in c.execute("SELECT * FROM mailboxes ORDER BY address")]
    me = _me(who)
    out = {"admin": admin, **_company(c)}
    mine = next((b for b in boxes if me and b["kind"] == "personal" and b["person_id"] == me and b["status"] == "active"),
                None)
    out["mine"] = read.row(mine) if mine else None
    out["shared"] = [read.row(b) for b in boxes if b is not mine and b["status"] == "active"
                     and read.level(b) != "none"]
    out["mailboxes"] = [read.row(b) for b in boxes] if admin else []
    return out


def _candidates(read, box, taken):
    """Who may be given access: people on the roster and active bots, less the owner and anyone with a row."""
    people = [{"type": "person", "id": p["id"], "name": p.get("name") or p["id"]}
              for p in (read.roster or {}).get("people") or [] if not p.get("hidden")]
    bots = [{"type": "bot", "id": b["id"], "name": b.get("display_name") or b["slug"]}
            for b in H.bots(read.c, state="active")]
    return [x for x in people + bots if (x["type"], x["id"]) not in taken
            and not (x["type"] == "person" and x["id"] == box["person_id"])]


def detail(c, who, mid):
    box = _box(c, mid)
    read = _Reader(c, who)
    lvl = read.level(box)
    manages = _manages(c, who, box)
    if lvl == "none" and not manages and not _admin(c, who):
        raise Problem("not_found", "No such mailbox", 404)
    acting_for = bool(who.via or who.task_actor)   # the Assistant reads it as the person, with no controls
    can_manage = manages and not acting_for
    accesses = []
    if box["kind"] == "personal" and box["person_id"]:
        accesses.append({"id": None, "grantee_type": "person", "grantee_id": box["person_id"],
                         "name": read.person_name(box["person_id"]), "level": "read_write", "implicit": True})
    elif box["kind"] == "shared":
        accesses.append({"id": None, "grantee_type": "admins", "grantee_id": "", "name": "Admins",
                         "level": "read_write", "implicit": True})
    bot_names = {b["id"]: b.get("display_name") or b["slug"] for b in H.bots(c)}
    taken = set()
    for row in c.execute("SELECT * FROM mailbox_accesses WHERE mailbox_id=? ORDER BY created_at", (box["id"],)):
        taken.add((row["grantee_type"], row["grantee_id"]))
        name = read.person_name(row["grantee_id"]) if row["grantee_type"] == "person" else \
            bot_names.get(row["grantee_id"], row["grantee_id"])
        accesses.append({"id": row["id"], "grantee_type": row["grantee_type"], "grantee_id": row["grantee_id"],
                         "name": name, "level": row["level"], "implicit": False})
    from .automations import may_pause
    routines = [{"id": r["id"], "name": r["name"], "enabled": r["enabled"],
                 "can_edit": bool(who.role in ("owner", "human", "bot") and may_pause(c, who, r["flow"]))}
                for r in read.routines.get(box["address"], [])]
    own = box["kind"] == "personal" and bool(box["person_id"]) and box["person_id"] == _me(who)
    return {"mailbox": read.row(box), "can_manage": can_manage, "can_stop": box["status"] == "active" and (
                own or _admin(c, who)) and not acting_for,
            "accesses": accesses, "routines": routines,
            "may": _company(c)["may"],
            "candidates": _candidates(read, box, taken) if can_manage else []}


def install(app, store):
    """The Mailboxes routes. Reads go through store.read(); writes check their own rights in one transaction."""

    @app.get(PATH)
    def list_mailboxes(request: Request):
        """Settings > Mailboxes for whoever is signed in: the admin's table, or your own mailbox and what is shared
        with you."""
        with store.read() as c:
            return listing(c, request.state.identity)

    @app.post(PATH)
    def connect_mailbox(request: Request, body: MailboxCreate):
        """An admin adds a mailbox for Tico to use. The address of a person on the roster is that person's own; any
        other is a shared inbox when `shared`, else it waits for its person (private, nobody has access)."""
        from .google_workspace import covers
        from .views import roster
        who = request.state.identity
        _person(who)
        address = body.address.strip().lower()
        if not ADDRESS.match(address):
            raise Problem("invalid", "Enter an email address", 422)
        with store.transaction() as c:
            if not _admin(c, who):
                raise Problem("forbidden", "Only an admin connects a mailbox", 403)
            person = P.person_by_email(address, roster(c))
            found = c.execute("SELECT * FROM mailboxes WHERE address=?", (address,)).fetchone()
            stamp = H.now()
            if found and found["status"] == "active":
                raise Problem("conflict", f"{address} is already in Tico", 409)
            kind = "personal" if person or not body.shared else "shared"
            shared_by, shared_at = (who.actor, stamp) if kind == "shared" else (None, None)
            connection = "workspace" if covers(c, address) else "personal"
            pid = person["id"] if person else None
            if found:
                mid = found["id"]
                c.execute("UPDATE mailboxes SET status='active', person_id=?, kind=?, connection=?, shared_by=?, "
                          "shared_at=?, updated_at=? WHERE id=?", (pid, kind, connection, shared_by, shared_at, stamp, mid))
            else:
                mid = H.new_id()
                c.execute("INSERT INTO mailboxes(id, address, provider, person_id, kind, connection, status, shared_by, "
                          "shared_at, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?,?,?)",
                          (mid, address, PROVIDER, pid, kind, connection, shared_by, shared_at, who.actor, stamp, stamp))
            H.event(c, who.actor, "mailbox.connect", mid, {"address": address, "kind": kind, "person_id": pid})
        with store.read() as c:
            return {"mailbox": _Reader(c, who).row(_box(c, mid))}

    @app.get(PATH + "/{mid}")
    def get_mailbox(request: Request, mid: str):
        """One mailbox: who can use it, its routines, and (for whoever manages it) who could be added."""
        with store.read() as c:
            return detail(c, request.state.identity, mid)

    @app.post(PATH + "/{mid}/share")
    def share_mailbox(request: Request, mid: str):
        """An admin makes a mailbox that has no person a shared inbox: admins get Read and write on it."""
        who = request.state.identity
        _person(who)
        with store.transaction() as c:
            MA.make_shared(c, who, mid)   # writes mailbox.shared
        with store.read() as c:
            return detail(c, who, mid)

    @app.post(PATH + "/{mid}/accesses")
    def add_mailbox_access(request: Request, mid: str, body: MailboxAccessCreate):
        """Give a person or a bot Read, or Read and write, on this mailbox."""
        from .views import roster
        who = request.state.identity
        _person(who)
        with store.transaction() as c:
            box = _box(c, mid)
            MA._manager(c, who, box, "change who uses this mailbox")   # refused before anything is looked up
            grantee = body.grantee_id.strip()
            if body.grantee_type == "person":
                grantee = H.actor_id(grantee)
                if not P.person(grantee, roster(c)):
                    raise Problem("not_found", "No such person", 404)
                if grantee == box["person_id"]:
                    raise Problem("conflict", "That person's own mailbox: they already have Read and write", 409)
            else:
                bot = H.resolve_bot(c, grantee)
                if not bot or bot["state"] == "archived":
                    raise Problem("not_found", "No such bot", 404)
                grantee = bot["id"]
            if c.execute("SELECT 1 FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
                         (box["id"], body.grantee_type, grantee)).fetchone():
                raise Problem("conflict", "They already have access: change their level instead", 409)
            MA.grant(c, who, box["id"], body.grantee_type, grantee, body.level)   # writes mailbox.access_granted
        with store.read() as c:
            return detail(c, who, mid)

    @app.patch(PATH + "/{mid}/accesses/{access_id}")
    def change_mailbox_access(request: Request, mid: str, access_id: str, body: MailboxAccessUpdate):
        """Change one access between Read and Read and write."""
        who = request.state.identity
        _person(who)
        with store.transaction() as c:
            box = _box(c, mid)
            row = c.execute("SELECT * FROM mailbox_accesses WHERE id=? AND mailbox_id=?", (access_id, box["id"])).fetchone()
            if not row:
                raise Problem("not_found", "No such access", 404)
            MA.grant(c, who, box["id"], row["grantee_type"], row["grantee_id"], body.level)   # mailbox.access_granted
        with store.read() as c:
            return detail(c, who, mid)

    @app.delete(PATH + "/{mid}/accesses/{access_id}")
    def remove_mailbox_access(request: Request, mid: str, access_id: str):
        """Take one person's or bot's access away."""
        who = request.state.identity
        _person(who)
        with store.transaction() as c:
            box = _box(c, mid)
            MA.revoke(c, who, box["id"], access_id)   # writes mailbox.access_revoked
        with store.read() as c:
            return detail(c, who, mid)

    @app.delete(PATH + "/{mid}")
    def stop_mailbox(request: Request, mid: str):
        """Stop using this mailbox in Tico: an admin, or the person whose mailbox it is."""
        who = request.state.identity
        _person(who)
        with store.transaction() as c:
            MA.stop(c, who, mid)   # writes mailbox.stopped
        with store.read() as c:
            return {"mailbox": _Reader(c, who).row(_box(c, mid))}
