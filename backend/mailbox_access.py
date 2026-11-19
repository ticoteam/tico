"""Who may use which mailbox: the one check every mail call goes through.

A mailbox (`mailboxes`, migration 0092) is one of:
- personal: linked to a person by their verified primary email (`person_id`), never by name;
- shared: no person; an admin made it shared;
- awaiting person: a personal row whose person has not joined (`person_id` NULL). It is private: nobody has access, and
  no grant on it counts, until its person joins (`link_person`) or an admin makes it shared.

A level is `read` or `read_write` (write = organize, draft, send). What a principal may do on a mailbox is the level it
holds, capped by the company's What Tico may do switches (backend/google_workspace.py `permissions`), and read only for
an address outside the company's domains.

Implicit levels, with no row:
- a person on their own personal mailbox (their own email, or the row linked to them): read_write;
- an admin (the owner included) on a shared inbox: read_write;
- a person's Assistant: exactly that person's access (backend/auth.py `assistant_principal` makes its turns that person).
Everyone else needs an explicit `mailbox_accesses` row. Managers and admins get nothing automatic on a personal mailbox,
and the owner holds no bypass. Nobody reshares: only a personal mailbox's person, or an admin for a shared inbox, may
grant or revoke on it. A computer is nobody here: the server reads and acts on mail itself.
"""

import json
import sqlite3
from dataclasses import dataclass

from .store import H, Problem

LEVELS = ("none", "read", "read_write")
GRANT_LEVELS = ("read", "read_write")
RANK = {level: n for n, level in enumerate(LEVELS)}
KINDS = ("personal", "shared")
CONNECTIONS = ("workspace", "personal")
STATUSES = ("active", "stopped")
GRANTEES = ("person", "bot")
PROVIDER = "google_workspace"
MIGRATION = "migration"


@dataclass(frozen=True)
class Principal:
    """Who is asking: a person (`id` their person id), a bot (`id` its bot id) or nobody."""

    kind: str
    id: str = ""

    @property
    def ref(self):
        return f"{self.kind}:{self.id}" if self.id else self.kind


NOBODY = Principal("none")


def _addr(value):
    return str(value or "").strip().lower()


def principal(c, who):
    """The principal behind an auth Identity, an actor string (`person:<id>`, `human:<id>`, `bot:<id or slug>`) or a
    Principal. A person's Assistant arrives as that person already (backend/auth.py `assistant_principal`). A runner
    or a service key is nobody here."""
    if isinstance(who, Principal):
        return who
    if who is None:
        return NOBODY
    if isinstance(who, str):
        kind, _, ident = who.strip().partition(":")
        ident = ident.strip()
        if kind in ("person", "human") and ident:
            return Principal("person", ident)
        if kind == "bot" and ident:
            row = H.resolve_bot(c, ident)
            return Principal("bot", row["id"]) if row else NOBODY
        return NOBODY
    role = getattr(who, "role", "")
    actor = str(getattr(who, "actor", "") or "")
    if role in ("owner", "human") and actor.startswith("human:"):
        return Principal("person", H.actor_id(actor))
    if role == "bot" and actor.startswith("bot:"):
        row = H.resolve_bot(c, H.actor_id(actor))
        return Principal("bot", row["id"]) if row else NOBODY
    return NOBODY


# ------------------------------------------------------------------------------------------------- what is known
class _Facts:
    """What one answer reads once: the roster, the admins, the company's domains and switches."""

    def __init__(self, c):
        from . import access as A
        from . import routines

        self.c = c
        self.roster = routines._roster(c)
        self.people = {p["id"]: p for p in self.roster.get("people") or []}
        self.by_email = {}
        for person in self.roster.get("people") or []:
            email = _addr(person.get("email"))
            if email:
                self.by_email.setdefault(email, person)
        self.admins = set(A.owner_and_admin_emails(c))
        self._domains = None
        self._perms = None

    def person(self, pid):
        """A person on the team now: on the roster and not marked as left."""
        found = self.people.get(str(pid or ""))
        return found if found and not found.get("hidden") else None

    def is_admin(self, pid):
        person = self.person(pid)
        return bool(person) and _addr(person.get("email")) in self.admins

    @property
    def domains(self):
        if self._domains is None:
            self._domains = company_domains(self.c)
        return self._domains

    @property
    def perms(self):
        if self._perms is None:
            from . import google_workspace

            self._perms = google_workspace.permissions(self.c)
        return self._perms


def company_domains(c):
    """The company's email domains: the ones allowed to sign in, else the owner's own company domain, else the domain
    named at first run plus the roster's company domains; the connected Google Workspace's domain always."""
    from . import access as A

    stored = A._load_json(c, A.ACCESS) or {}
    out = list(A.domains(stored.get("allowed_domains")))
    if not out:
        domain = A.domain_of((A._load_json(c, A.OWNER) or {}).get("email"))
        if domain and domain not in A.PUBLIC_MAIL:
            out = [domain]
        else:
            named = _addr(((A._load_json(c, "onboarding") or {}).get("names") or {}).get("team_domain"))
            out = list(dict.fromkeys([*([named] if named else []), *A.roster_domains(c)]))
    try:
        from . import google_workspace

        row = google_workspace.active(c)
    except sqlite3.OperationalError:
        row = None
    if row and row.get("domain"):
        out.append(_addr(row["domain"]))
    return list(dict.fromkeys(d for d in out if d))


def inside(address, domains):
    """Whether `address` is at one of the company's domains. With none known, every address counts as inside."""
    if not domains:
        return True
    return _addr(address).rpartition("@")[2] in set(domains)


def _row(c, address):
    try:
        found = c.execute("SELECT * FROM mailboxes WHERE address=?", (_addr(address),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(found) if found else None


def row_by_id(c, mailbox_id):
    try:
        found = c.execute("SELECT * FROM mailboxes WHERE id=?", (str(mailbox_id or ""),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(found) if found else None


def row_by_address(c, address):
    """The mailbox row for an address, or None. Reads only: nothing is made."""
    return _row(c, address)


def feeds_directory(c, row):
    """Whether this mailbox's mail feeds the Directory (backend/entities/ingest.py). It does unless its owner turned it
    off. A bot's inbox, a mailbox no person owns that a bot is granted on, always does."""
    if not row:
        return False
    if row.get("feeds_directory", 1):
        return True
    if row.get("person_id"):
        return False
    return (
        c.execute(
            "SELECT 1 FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type='bot' LIMIT 1", (row["id"],)
        ).fetchone()
        is not None
    )


def stopped(c):
    """The addresses an admin or their person stopped using in Tico: no computer syncs or acts for them."""
    try:
        return {row[0] for row in c.execute("SELECT address FROM mailboxes WHERE status='stopped'")}
    except sqlite3.OperationalError:
        return set()


def awaiting(row):
    """A personal mailbox whose person has not joined: private, nobody has access."""
    return bool(row) and row["kind"] == "personal" and not row.get("person_id")


def _granted(c, row, kind, ident):
    if not row or awaiting(row):
        return "none"
    found = c.execute(
        "SELECT level FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
        (row["id"], kind, ident),
    ).fetchone()
    return found["level"] if found and found["level"] in GRANT_LEVELS else "none"


def _owns(row, address, pid, email):
    """Whether person `pid` (whose email is `email`) is the person of this mailbox."""
    if row is None:
        return bool(email) and _addr(address) == email
    if row["kind"] != "personal":
        return False
    return row.get("person_id") == pid


def _live_bot(c, bot_id):
    found = c.execute("SELECT state FROM bots WHERE id=?", (bot_id,)).fetchone()
    return bool(found) and found["state"] != "archived"


def _best(*levels):
    return max(levels, key=lambda level: RANK.get(level, 0))


def _level(facts, p, address, row):
    c = facts.c
    address = _addr(address)
    if not address or "@" not in address:
        return "none"
    if row and row["status"] != "active":
        return "none"
    if p.kind == "person":
        person = facts.person(p.id)
        if not person:
            return "none"
        email = _addr(person.get("email"))
        level = "none"
        if _owns(row, address, p.id, email):
            level = "read_write"
        elif row and row["kind"] == "shared" and email in facts.admins:
            level = "read_write"
        level = _best(level, _granted(c, row, "person", p.id))
    elif p.kind == "bot":
        if not _live_bot(c, p.id):
            return "none"
        level = _granted(c, row, "bot", p.id)
    else:
        return "none"
    if level == "read_write" and not inside(address, facts.domains):
        return "read"
    return level


# ---------------------------------------------------------------------------------------------------- contract
def level(c, who, mailbox_address):
    """ "none", "read" or "read_write": what `who` holds on this mailbox now."""
    p = principal(c, who)
    if p.kind == "none":
        return "none"
    return _level(_Facts(c), p, mailbox_address, _row(c, mailbox_address))


def _may(lvl, perms):
    rw = lvl == "read_write"
    return {
        "read": lvl != "none",
        "organize": rw and perms["organize_mail"],
        "send": rw and perms["send_mail"],
        "change_calendar": rw and perms["change_calendar"],
        # A draft is written with the mail write scope, which Organize or Send turns on.
        "draft": rw and bool(perms["organize_mail"] or perms["send_mail"]),
    }


def may(c, who, mailbox_address):
    """{read, organize, send, change_calendar, draft}: the level capped by the company's What Tico may do."""
    p = principal(c, who)
    facts = _Facts(c)
    lvl = "none" if p.kind == "none" else _level(facts, p, mailbox_address, _row(c, mailbox_address))
    return _may(lvl, facts.perms)


def mailboxes_for(c, who, min_level="read"):
    """The mailboxes `who` holds at least `min_level` on: [{id, address, kind, person_id, level, may}], their own
    first, then by address. A person's own email counts as their personal mailbox before it has a row."""
    p = principal(c, who)
    if p.kind == "none":
        return []
    facts = _Facts(c)
    need = RANK.get(min_level, 1)
    candidates = {}

    def add(address, row=None):
        address = _addr(address)
        if address and "@" in address and address not in candidates:
            candidates[address] = row if row is not None else _row(c, address)

    try:
        if p.kind == "person":
            person = facts.person(p.id)
            if person:
                add(person.get("email"))
                for row in c.execute(
                    "SELECT * FROM mailboxes WHERE kind='personal' AND person_id=? ORDER BY address", (p.id,)
                ):
                    add(row["address"], dict(row))
                if _addr(person.get("email")) in facts.admins:
                    for row in c.execute("SELECT * FROM mailboxes WHERE kind='shared' ORDER BY address"):
                        add(row["address"], dict(row))
        if p.kind in ("person", "bot"):
            for row in c.execute(
                "SELECT m.* FROM mailbox_accesses a JOIN mailboxes m ON m.id=a.mailbox_id "
                "WHERE a.grantee_type=? AND a.grantee_id=? ORDER BY m.address",
                (p.kind, p.id),
            ):
                add(row["address"], dict(row))
    except sqlite3.OperationalError:
        pass  # a database without the tables yet: only a person's own email
    own = _addr((facts.person(p.id) or {}).get("email")) if p.kind == "person" else ""
    out = []
    for address, row in candidates.items():
        lvl = _level(facts, p, address, row)
        if RANK[lvl] < need or lvl == "none":
            continue
        out.append(
            {
                "id": (row or {}).get("id"),
                "address": address,
                "kind": (row or {}).get("kind") or "personal",
                "person_id": (row or {}).get("person_id") if row else (p.id if address == own else None),
                "level": lvl,
                "may": _may(lvl, facts.perms),
            }
        )
    out.sort(key=lambda box: (box["address"] != own, box["address"]))
    return out


def bot_mailboxes(c):
    """{bot id: [address]}: the addresses `mailboxes_for(c, "bot:<id>")` lists, for every bot at once (a page that
    shows each bot's mailboxes asked it once a bot, five statements each)."""
    live = {row["id"] for row in c.execute("SELECT id, state FROM bots") if row["state"] != "archived"}
    found = {}
    for grant in c.execute(
        "SELECT a.grantee_id AS grant_bot, a.level AS grant_level, m.* FROM mailbox_accesses a "
        "JOIN mailboxes m ON m.id=a.mailbox_id WHERE a.grantee_type='bot'"
    ):
        row = dict(grant)
        bot, level = row.pop("grant_bot"), row.pop("grant_level")
        address = _addr(row["address"])
        if bot not in live or not address or "@" not in address or row["status"] != "active" or awaiting(row):
            continue
        if level in GRANT_LEVELS:  # read_write outside the company's domains reads as read: listed either way
            found.setdefault(bot, set()).add(address)
    return {bot: sorted(addresses) for bot, addresses in found.items()}


def best_level(c, principals, mailbox_address):
    """The most any of `principals` holds on one mailbox (a computer's turns asking for one token)."""
    facts, row, best = _Facts(c), _row(c, mailbox_address), "none"
    for who in principals:
        p = principal(c, who)
        if p.kind != "none":
            best = _best(best, _level(facts, p, mailbox_address, row))
    return best


def levels_for(c, who, min_level="read"):
    """{address: level} for `mailboxes_for`."""
    return {box["address"]: box["level"] for box in mailboxes_for(c, who, min_level)}


# What each mail action an automation queues needs of its owner (backend/automation_actions.py MAIL_ACTIONS).
ACTION_NEEDS = {
    "archive": "organize",
    "label": "organize",
    "leave": "organize",
    "unsubscribe": "organize",
    "draft": "draft",
    "send": "send",
    "forward": "send",
}


def action_refusal(c, owner, mailbox, action, args=None, *, queued=False):
    """Why an automation of `owner` may not have Tico carry out `action` on `mailbox`, or None when it may.
    `queued`: asked when the action is queued, when a send or forward still becomes a draft if sending is off at the
    claim, so a draft is enough then. An action this does not know needs what a send needs."""
    need = ACTION_NEEDS.get(action, "send")
    if action == "forward" and (args or {}).get("draft_only"):
        need = "draft"
    allowed = may(c, owner, mailbox)
    if allowed.get(need) or (queued and need == "send" and allowed.get("draft")):
        return None
    words = {"organize": "organize", "draft": "write drafts in", "send": "send from"}
    address = str(mailbox or "").strip().lower()
    if level(c, owner, mailbox) == "read_write":
        return f"Tico may not {words[need]} {address}: it is off in Tools > Settings > Email > What Tico may do"
    return f"the automation's owner may not {words[need]} {address} (Tools > {address} > Who can use it)"


# ---------------------------------------------------------------------------------------------------- changes
def _actor_ref(actor):
    if isinstance(actor, Principal):
        return actor.ref
    if isinstance(actor, str):
        return actor
    return str(getattr(actor, "actor", "") or "")


def _manager(c, actor, row, what):
    """Refuse unless `actor` may change who uses this mailbox: its person for a personal one, an admin for a shared
    one. A person's Assistant may not (it proposes; the person decides), and neither may a bot."""
    if getattr(actor, "via", "") == "assistant":
        raise Problem("forbidden", f"The Assistant may not {what}; the mailbox's person does that", 403)
    p = principal(c, actor)
    if p.kind != "person":
        raise Problem("forbidden", f"Only a person may {what}", 403)
    facts = _Facts(c)
    if not facts.person(p.id):
        raise Problem("forbidden", f"Only a person on the team may {what}", 403)
    if row["kind"] == "shared":
        if not facts.is_admin(p.id):
            raise Problem("forbidden", f"Only an admin may {what} on a shared inbox", 403)
    elif awaiting(row):
        raise Problem(
            "awaiting_person",
            f"{row['address']} is awaiting its person: nobody may {what} until they "
            "join, or an admin makes it a shared inbox",
            409,
        )
    elif row.get("person_id") != p.id:
        raise Problem("forbidden", f"Only {row['address']}'s own person may {what}", 403)
    return p, facts


def _require_row(c, mailbox_id):
    row = row_by_id(c, mailbox_id)
    if not row:
        raise Problem("not_found", "Mailbox not found", 404)
    return row


def grant(c, actor, mailbox_id, grantee_type, grantee_id, level):
    """Give a person or a bot `level` on a mailbox (or change the level it has). Only the mailbox's person, or an
    admin for a shared inbox. Returns the access row."""
    row = _require_row(c, mailbox_id)
    p, facts = _manager(c, actor, row, "share this mailbox")
    if row["status"] != "active":
        raise Problem("stopped", f"{row['address']} is not used in Tico", 409)
    if grantee_type not in GRANTEES:
        raise Problem("invalid", "grantee_type is person or bot", 422)
    if level not in GRANT_LEVELS:
        raise Problem("invalid", "level is read or read_write", 422)
    ident = str(grantee_id or "").strip()
    if grantee_type == "person":
        ident = H.actor_id(ident)
        if not facts.person(ident):
            raise Problem("not_found", "That person is not on the team", 404)
        if row["kind"] == "personal" and row.get("person_id") == ident:
            raise Problem("invalid", "This is their own mailbox", 422)
    else:
        bot = H.resolve_bot(c, ident)
        if not bot or bot["state"] == "archived":
            raise Problem("not_found", "That bot is not on the team", 404)
        ident = bot["id"]
    now = H.now()
    found = c.execute(
        "SELECT * FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
        (row["id"], grantee_type, ident),
    ).fetchone()
    by = _actor_ref(actor)
    if found:
        c.execute(
            "UPDATE mailbox_accesses SET level=?, granted_by=?, updated_at=? WHERE id=?", (level, by, now, found["id"])
        )
        access_id, before = found["id"], found["level"]
    else:
        access_id, before = H.new_id(), None
        c.execute(
            "INSERT INTO mailbox_accesses(id, mailbox_id, grantee_type, grantee_id, level, granted_by, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (access_id, row["id"], grantee_type, ident, level, by, now, now),
        )
    H.event(
        c,
        by,
        "mailbox.access_granted",
        row["id"],
        {"address": row["address"], "grantee": f"{grantee_type}:{ident}", "level": level, "before": before},
    )
    return dict(c.execute("SELECT * FROM mailbox_accesses WHERE id=?", (access_id,)).fetchone())


def revoke(c, actor, mailbox_id, access_id):
    """Take one access row away. Only the mailbox's person, or an admin for a shared inbox."""
    row = _require_row(c, mailbox_id)
    _manager(c, actor, row, "change who uses this mailbox")
    found = c.execute(
        "SELECT * FROM mailbox_accesses WHERE id=? AND mailbox_id=?", (str(access_id or ""), row["id"])
    ).fetchone()
    if not found:
        raise Problem("not_found", "Access not found", 404)
    c.execute("DELETE FROM mailbox_accesses WHERE id=?", (found["id"],))
    H.event(
        c,
        _actor_ref(actor),
        "mailbox.access_revoked",
        row["id"],
        {
            "address": row["address"],
            "grantee": f"{found['grantee_type']}:{found['grantee_id']}",
            "level": found["level"],
        },
    )
    return dict(found)


def link_person(c, person_id, email):
    """A person joined with verified primary `email`: a personal mailbox awaiting them at that address becomes theirs.
    Joining never makes a mailbox: with no row, nothing is written. Returns the linked row or None."""
    address, pid = _addr(email), H.actor_id(person_id)
    if not address or not pid:
        return None
    row = _row(c, address)
    if not row or row["kind"] != "personal" or row.get("person_id"):
        return None
    now = H.now()
    c.execute("UPDATE mailboxes SET person_id=?, updated_at=? WHERE id=? AND person_id IS NULL", (pid, now, row["id"]))
    H.event(c, H.human_actor(pid), "mailbox.linked", row["id"], {"address": address})
    return _row(c, address)


def make_shared(c, actor, mailbox_id):
    """An admin makes a mailbox no person holds a shared inbox. A mailbox linked to a person stays theirs."""
    row = _require_row(c, mailbox_id)
    if getattr(actor, "via", "") == "assistant":
        raise Problem("forbidden", "The Assistant may not make a shared inbox", 403)
    p = principal(c, actor)
    facts = _Facts(c)
    if p.kind != "person" or not facts.is_admin(p.id):
        raise Problem("forbidden", "Only an admin may make a shared inbox", 403)
    if row["kind"] == "shared":
        return row
    if row.get("person_id"):
        raise Problem("linked", f"{row['address']} is its person's own mailbox", 409)
    now = H.now()
    c.execute(
        "UPDATE mailboxes SET kind='shared', shared_by=?, shared_at=?, updated_at=? WHERE id=?",
        (_actor_ref(actor), now, now, row["id"]),
    )
    H.event(c, _actor_ref(actor), "mailbox.shared", row["id"], {"address": row["address"]})
    return row_by_id(c, row["id"])


def stop(c, actor, mailbox_id):
    """Stop using a mailbox in Tico: nobody may use it (its grants are kept for a restart). Its person, or an admin."""
    row = _require_row(c, mailbox_id)
    if getattr(actor, "via", "") == "assistant":
        raise Problem("forbidden", "The Assistant may not stop a mailbox", 403)
    p = principal(c, actor)
    facts = _Facts(c)
    own = p.kind == "person" and row["kind"] == "personal" and row.get("person_id") == p.id and facts.person(p.id)
    if not own and not (p.kind == "person" and facts.is_admin(p.id)):
        raise Problem("forbidden", "Only its person or an admin may stop using this mailbox", 403)
    if row["status"] == "stopped":
        return row
    c.execute("UPDATE mailboxes SET status='stopped', updated_at=? WHERE id=?", (H.now(), row["id"]))
    H.event(c, _actor_ref(actor), "mailbox.stopped", row["id"], {"address": row["address"]})
    return row_by_id(c, row["id"])


def set_feeds_directory(c, actor, mailbox_id, on):
    """Turn a mailbox's feed into the Directory on or off. Its person (or an admin, for a shared inbox) decides. Turning it
    off stops new ingest; what it alone made stops showing (visible.py counts only feeding mailboxes)."""
    row = _require_row(c, mailbox_id)
    _manager(c, actor, row, "switch its feed into Contacts")
    if bool(row.get("feeds_directory", 1)) == bool(on):
        return row
    c.execute("UPDATE mailboxes SET feeds_directory=?, updated_at=? WHERE id=?", (1 if on else 0, H.now(), row["id"]))
    H.event(c, _actor_ref(actor), "mailbox.directory_feed", row["id"], {"address": row["address"], "on": bool(on)})
    return row_by_id(c, row["id"])


def ensure_mailbox(
    c, address, *, person_id=None, kind=None, connection="workspace", provider=PROVIDER, created_by="system"
):
    """The row for `address`, made when missing: a personal one, linked to `person_id` or else to the team member whose
    verified email it is, and awaiting its person when there is none; a shared one when `kind` says so. An existing
    row is returned as it is."""
    address = _addr(address)
    if "@" not in address:
        raise Problem("mailbox", "That is not an email address", 422)
    kind = kind or "personal"
    if kind not in KINDS or connection not in CONNECTIONS:
        raise Problem("invalid", "Unknown mailbox kind or connection", 422)
    if kind == "personal" and not person_id:
        person_id = (_Facts(c).by_email.get(address) or {}).get("id")
    now = H.now()
    c.execute(
        "INSERT OR IGNORE INTO mailboxes(id, address, provider, person_id, kind, connection, status, shared_by, "
        "shared_at, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?,?,?)",
        (
            H.new_id(),
            address,
            provider,
            person_id if kind == "personal" else None,
            kind,
            connection,
            created_by if kind == "shared" else None,
            now if kind == "shared" else None,
            created_by,
            now,
            now,
        ),
    )
    return _row(c, address)


def give(c, mailbox, grantee_type, grantee_id, level, granted_by):
    """Record an access the system itself decides (a message bot assigned to its person's mailbox, the migration),
    never lowering a level already held. No check: callers are the code that already decided."""
    if level not in GRANT_LEVELS or grantee_type not in GRANTEES:
        raise ValueError("bad level or grantee")
    row = mailbox if isinstance(mailbox, dict) else _row(c, mailbox)
    if not row:
        return None
    now = H.now()
    found = c.execute(
        "SELECT id, level FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
        (row["id"], grantee_type, grantee_id),
    ).fetchone()
    if found:
        if RANK[level] > RANK.get(found["level"], 0):
            c.execute(
                "UPDATE mailbox_accesses SET level=?, granted_by=?, updated_at=? WHERE id=?",
                (level, granted_by, now, found["id"]),
            )
        return found["id"]
    access_id = H.new_id()
    c.execute(
        "INSERT INTO mailbox_accesses(id, mailbox_id, grantee_type, grantee_id, level, granted_by, created_at, "
        "updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (access_id, row["id"], grantee_type, grantee_id, level, granted_by, now, now),
    )
    return access_id


def _bot_config(c, bot):
    from . import bot_settings as BS

    found = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    try:
        return json.loads(found["config"] or "{}") if found else {}
    except ValueError:
        return {}


def message_bot_mailbox(c, person, bot):
    """The address `bot` reads for `person` as their message bot: the mailbox it declares, else their email."""
    from . import routines

    return _addr(routines.declared_mailbox(_bot_config(c, bot)) or (person or {}).get("email"))


def message_bot_changed(c, person, old_bot, old_address, bot):
    """People > a person's message bot was set (backend/access.py assign_message_bot): the new bot gets its grant on
    the mailbox it reads for them (Read and write when its gmail entry may draft or send, else Read), and a bot that
    stopped being their message bot, or now reads another address, loses the grant it had on `old_address`. A mailbox that is not the person's own address is a
    shared inbox the person keeps Read and write on."""
    address = message_bot_mailbox(c, person, bot) if bot else ""
    if old_bot and (old_bot, _addr(old_address)) != (bot, address):
        old = _row(c, old_address)
        if old:
            c.execute(
                "DELETE FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type='bot' AND grantee_id=?",
                (old["id"], old_bot),
            )
    if not bot:
        return None
    from . import routines

    if not routines.MAILBOX.fullmatch(address):
        return None
    own = address == _addr((person or {}).get("email"))
    kind, pid = ("personal", person["id"]) if own else kind_for(address, _Facts(c).by_email)
    row = ensure_mailbox(c, address, person_id=pid, kind=kind, created_by="message_bot")
    entry = next((e for e in _gmail_entries(_bot_config(c, bot)) if _addr(e.get("identity")) == address), None)
    give(c, row, "bot", bot, manifest_level(entry), "message_bot")
    if row["kind"] == "shared":
        give(c, row, "person", person["id"], "read_write", "message_bot")
    return row


def kind_for(address, by_email):
    """(kind, person_id) a new row at `address` gets: personal for a roster person's own email, else shared."""
    person = by_email.get(_addr(address))
    return ("personal", person["id"]) if person else ("shared", None)


def _gmail_entries(config):
    from clients.manifest import tools_of

    tools = tools_of(config) if isinstance(config, dict) else []
    return [e for e in tools or [] if isinstance(e, dict) and str(e.get("service", "")).lower() == "gmail"]


def manifest_level(entry):
    """What a bot.yaml `gmail` entry asked for: read_write when it may draft or send, read otherwise (and always
    for `read_only: true`)."""
    if not entry or entry.get("read_only") is True:
        return "read"
    can = {str(v).strip().lower() for v in (entry.get("can") or [])}
    return "read_write" if can & {"draft", "send"} else "read"
