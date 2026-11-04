"""CONTRACT STUB (track B, Mailboxes page). Track A (branch chris/mailbox-access) replaces this whole file with the real
module and adds migration 0082_mailboxes.py; until then the `mailboxes` and `mailbox_accesses` tables exist only where a
test fixture makes them (backend/tests/test_mailboxes_api.py).

Only the functions the Mailboxes routes (backend/mailboxes.py) call are here, with the semantics of the shared plan:
- levels are `read` and `read_write`; effective rights are min(company "What Tico may do", level);
- implicit access, no row: the owner of a personal mailbox and the admins on a shared inbox have read_write, and a
  person's Assistant has exactly that person's access; everyone else needs a `mailbox_accesses` row;
- only a personal mailbox's owner, or an admin for a shared inbox, grants or revokes (recipients can't reshare).
Enforcement elsewhere (mail, automations, connectors) is track A's and is not stubbed."""
from . import hubdb as H
from .store import Problem

LEVELS = ("read", "read_write")
_RANK = {"none": 0, "read": 1, "read_write": 2}


def _who(principal):
    """(grantee_type, grantee_id) for an auth Identity: a person (the Assistant acting for one counts as that person),
    else a bot, else nobody."""
    role = getattr(principal, "role", "")
    if role in ("owner", "human"):
        return "person", H.actor_id(principal.actor)
    if role == "bot":
        return "bot", H.actor_id(principal.actor)
    return None, None


def _admin(c, principal):
    from .teammate_access import is_admin
    return getattr(principal, "role", "") in ("owner", "human") and is_admin(c, principal)


def _row(c, address):
    row = c.execute("SELECT * FROM mailboxes WHERE address=?", (str(address or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def _by_id(c, mailbox_id):
    row = c.execute("SELECT * FROM mailboxes WHERE id=?", (str(mailbox_id or ""),)).fetchone()
    if not row:
        raise Problem("not_found", "No such mailbox", 404)
    return dict(row)


def _level_of(c, principal, box):
    if not box or box["status"] != "active":
        return "none"
    kind, gid = _who(principal)
    if kind is None:
        return "none"
    if kind == "person" and box["kind"] == "personal" and box["person_id"] == gid:
        return "read_write"
    if box["kind"] == "shared" and _admin(c, principal):
        return "read_write"
    row = c.execute("SELECT level FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
                    (box["id"], kind, gid)).fetchone()
    return row["level"] if row else "none"


def level(c, principal, mailbox_address):
    return _level_of(c, principal, _row(c, mailbox_address))


def may(c, principal, mailbox_address):
    from .google_workspace import permissions
    lvl = level(c, principal, mailbox_address)
    write = lvl == "read_write"
    company = permissions(c)
    return {"read": lvl != "none", "organize": write and bool(company.get("organize_mail")),
            "send": write and bool(company.get("send_mail")),
            "change_calendar": write and bool(company.get("change_calendar"))}


def mailboxes_for(c, principal, min_level="read"):
    out = []
    for box in (dict(r) for r in c.execute("SELECT * FROM mailboxes WHERE status='active' ORDER BY address")):
        lvl = _level_of(c, principal, box)
        if _RANK[lvl] >= _RANK[min_level]:
            out.append({"address": box["address"], "kind": box["kind"], "person_id": box["person_id"], "level": lvl})
    return out


def may_manage(c, actor, box):
    """Who grants and revokes on `box`: a personal mailbox's owner, an admin for a shared inbox; nobody while it awaits
    its person. (Not in the shared contract: the page reads it to show the controls; track A may name it differently.)"""
    kind, gid = _who(actor)
    if box["status"] != "active" or kind != "person":
        return False
    if box["kind"] == "personal":
        return bool(box["person_id"]) and box["person_id"] == gid
    return _admin(c, actor)


def _require_manage(c, actor, box):
    if not may_manage(c, actor, box):
        raise Problem("forbidden", "Only the mailbox's owner, or an admin for a shared inbox, changes who can use it", 403)


def grant(c, actor, mailbox_id, grantee_type, grantee_id, level):
    box = _by_id(c, mailbox_id)
    _require_manage(c, actor, box)
    if level not in LEVELS:
        raise Problem("invalid", "Level is read or read_write", 422)
    if grantee_type not in ("person", "bot"):
        raise Problem("invalid", "A grant is for a person or a bot", 422)
    stamp = H.now()
    found = c.execute("SELECT id FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
                      (box["id"], grantee_type, grantee_id)).fetchone()
    if found:
        c.execute("UPDATE mailbox_accesses SET level=?, granted_by=?, updated_at=? WHERE id=?",
                  (level, actor.actor, stamp, found["id"]))
        return found["id"]
    aid = H.new_id()
    c.execute("INSERT INTO mailbox_accesses(id, mailbox_id, grantee_type, grantee_id, level, granted_by, created_at, "
              "updated_at) VALUES(?,?,?,?,?,?,?,?)", (aid, box["id"], grantee_type, grantee_id, level, actor.actor,
                                                      stamp, stamp))
    return aid


def revoke(c, actor, mailbox_id, access_id):
    box = _by_id(c, mailbox_id)
    _require_manage(c, actor, box)
    gone = c.execute("DELETE FROM mailbox_accesses WHERE id=? AND mailbox_id=?", (access_id, box["id"])).rowcount
    if not gone:
        raise Problem("not_found", "No such access", 404)


def link_person(c, person_id, email):
    c.execute("UPDATE mailboxes SET person_id=?, kind='personal', updated_at=? WHERE address=? AND person_id IS NULL "
              "AND kind='personal'", (person_id, H.now(), str(email or "").strip().lower()))


def make_shared(c, actor, mailbox_id):
    box = _by_id(c, mailbox_id)
    if not _admin(c, actor):
        raise Problem("forbidden", "Only an admin makes a mailbox a shared inbox", 403)
    if box["person_id"]:
        raise Problem("conflict", "This mailbox is a person's own", 409)
    stamp = H.now()
    c.execute("UPDATE mailboxes SET kind='shared', shared_by=?, shared_at=?, updated_at=? WHERE id=?",
              (actor.actor, stamp, stamp, box["id"]))


def stop(c, actor, mailbox_id):
    box = _by_id(c, mailbox_id)
    kind, gid = _who(actor)
    own = kind == "person" and box["kind"] == "personal" and box["person_id"] == gid
    if not (own or _admin(c, actor)):
        raise Problem("forbidden", "Only an admin, or the mailbox's owner, stops Tico using it", 403)
    c.execute("UPDATE mailboxes SET status='stopped', updated_at=? WHERE id=?", (H.now(), box["id"]))
