"""Who may use which mailbox: the one check every mail call goes through.

A mailbox (`mailboxes`, migration 0082) is one of:
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
grant or revoke on it.

The mail connector (a computer's own sync and the actions the server leases to it) is the `connector` principal: read
on the mailboxes this computer may act for (/api/v2/connectors/mail/targets) and nothing else here; its writes are only
the actions the server leased it, each checked against the automation owner's level when leased.
"""

import json
import sqlite3
from dataclasses import dataclass, field

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
    """Who is asking: a person (`id` their person id), a bot (`id` its bot id), the mail connector (`targets` the
    addresses its computer may act for) or nobody."""
    kind: str
    id: str = ""
    targets: frozenset = field(default_factory=frozenset)

    @property
    def ref(self):
        return f"{self.kind}:{self.id}" if self.id else self.kind


NOBODY = Principal("none")


def _addr(value):
    return str(value or "").strip().lower()


def connector(targets):
    """The mail connector of one computer: read on `targets` (addresses, or target dicts with an `address`)."""
    out = set()
    for target in targets or ():
        out.add(_addr(target.get("address") if isinstance(target, dict) else target))
    out.discard("")
    return Principal("connector", "", frozenset(out))


def connector_for(c, runner_id, named, owned=None):
    """The connector principal of computer `runner_id`: the message-bot mailboxes it may act for, as
    /api/v2/connectors/mail/targets lists them (`named`: it is a publisher)."""
    from . import google_rights
    return connector(google_rights.visible(c, runner_id, named, owned=owned))


def principal(c, who):
    """The principal behind an auth Identity, an actor string (`person:<id>`, `human:<id>`, `bot:<id or slug>`) or a
    Principal. A person's Assistant arrives as that person already (backend/auth.py `assistant_principal`). A runner
    or a service key is nobody here: the connector is made with `connector_for`, from the targets."""
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
    found = c.execute("SELECT level FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
                      (row["id"], kind, ident)).fetchone()
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
    if p.kind == "connector":
        return "read" if address in p.targets else "none"
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
    """"none", "read" or "read_write": what `who` holds on this mailbox now."""
    p = principal(c, who)
    if p.kind == "none":
        return "none"
    return _level(_Facts(c), p, mailbox_address, _row(c, mailbox_address))


def _may(lvl, perms):
    rw = lvl == "read_write"
    return {"read": lvl != "none", "organize": rw and perms["organize_mail"], "send": rw and perms["send_mail"],
            "change_calendar": rw and perms["change_calendar"],
            # A draft is written with the mail write scope, which Organize or Send turns on.
            "draft": rw and bool(perms["organize_mail"] or perms["send_mail"])}


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
        if p.kind == "connector":
            for address in sorted(p.targets):
                add(address)
        elif p.kind == "person":
            person = facts.person(p.id)
            if person:
                add(person.get("email"))
                for row in c.execute("SELECT * FROM mailboxes WHERE kind='personal' AND person_id=? ORDER BY address",
                                     (p.id,)):
                    add(row["address"], dict(row))
                if _addr(person.get("email")) in facts.admins:
                    for row in c.execute("SELECT * FROM mailboxes WHERE kind='shared' ORDER BY address"):
                        add(row["address"], dict(row))
        if p.kind in ("person", "bot"):
            for row in c.execute("SELECT m.* FROM mailbox_accesses a JOIN mailboxes m ON m.id=a.mailbox_id "
                                 "WHERE a.grantee_type=? AND a.grantee_id=? ORDER BY m.address", (p.kind, p.id)):
                add(row["address"], dict(row))
    except sqlite3.OperationalError:
        pass                    # a database without the tables yet: only a person's own email
    own = _addr((facts.person(p.id) or {}).get("email")) if p.kind == "person" else ""
    out = []
    for address, row in candidates.items():
        lvl = _level(facts, p, address, row)
        if RANK[lvl] < need or lvl == "none":
            continue
        out.append({"id": (row or {}).get("id"), "address": address,
                    "kind": (row or {}).get("kind") or "personal",
                    "person_id": (row or {}).get("person_id") if row else (p.id if address == own else None),
                    "level": lvl, "may": _may(lvl, facts.perms)})
    out.sort(key=lambda box: (box["address"] != own, box["address"]))
    return out


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
ACTION_NEEDS = {"archive": "organize", "label": "organize", "leave": "organize", "unsubscribe": "organize",
                "draft": "draft", "send": "send", "forward": "send"}


def action_refusal(c, owner, mailbox, action, args=None, *, queued=False):
    """Why an automation of `owner` may not have the connector carry out `action` on `mailbox`, or None when it may.
    `queued`: asked when the action is queued, when a send or forward still becomes a draft if sending is off at the
    claim, so a draft is enough then. An action this does not know needs what a send needs."""
    need = ACTION_NEEDS.get(action, "send")
    if action == "forward" and (args or {}).get("draft_only"):
        need = "draft"
    allowed = may(c, owner, mailbox)
    if allowed.get(need) or (queued and need == "send" and allowed.get("draft")):
        return None
    words = {"organize": "organize", "draft": "write drafts in", "send": "send from"}
    return (f"the automation's owner may not {words[need]} {str(mailbox or '').strip().lower()} "
            "(Settings > Mailboxes: Who can use this mailbox)")


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
        raise Problem("awaiting_person", f"{row['address']} is awaiting its person: nobody may {what} until they "
                      "join, or an admin makes it a shared inbox", 409)
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
    found = c.execute("SELECT * FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
                      (row["id"], grantee_type, ident)).fetchone()
    by = _actor_ref(actor)
    if found:
        c.execute("UPDATE mailbox_accesses SET level=?, granted_by=?, updated_at=? WHERE id=?",
                  (level, by, now, found["id"]))
        access_id, before = found["id"], found["level"]
    else:
        access_id, before = H.new_id(), None
        c.execute("INSERT INTO mailbox_accesses(id, mailbox_id, grantee_type, grantee_id, level, granted_by, "
                  "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                  (access_id, row["id"], grantee_type, ident, level, by, now, now))
    H.event(c, by, "mailbox.access_granted", row["id"],
            {"address": row["address"], "grantee": f"{grantee_type}:{ident}", "level": level, "before": before})
    return dict(c.execute("SELECT * FROM mailbox_accesses WHERE id=?", (access_id,)).fetchone())


def revoke(c, actor, mailbox_id, access_id):
    """Take one access row away. Only the mailbox's person, or an admin for a shared inbox."""
    row = _require_row(c, mailbox_id)
    _manager(c, actor, row, "change who uses this mailbox")
    found = c.execute("SELECT * FROM mailbox_accesses WHERE id=? AND mailbox_id=?",
                      (str(access_id or ""), row["id"])).fetchone()
    if not found:
        raise Problem("not_found", "Access not found", 404)
    c.execute("DELETE FROM mailbox_accesses WHERE id=?", (found["id"],))
    H.event(c, _actor_ref(actor), "mailbox.access_revoked", row["id"],
            {"address": row["address"], "grantee": f"{found['grantee_type']}:{found['grantee_id']}",
             "level": found["level"]})
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
    c.execute("UPDATE mailboxes SET kind='shared', shared_by=?, shared_at=?, updated_at=? WHERE id=?",
              (_actor_ref(actor), now, now, row["id"]))
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


def ensure_mailbox(c, address, *, person_id=None, kind=None, connection="workspace", provider=PROVIDER,
                   created_by="system"):
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
    c.execute("INSERT OR IGNORE INTO mailboxes(id, address, provider, person_id, kind, connection, status, shared_by, "
              "shared_at, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?,?,?)",
              (H.new_id(), address, provider, person_id if kind == "personal" else None, kind, connection,
               created_by if kind == "shared" else None, now if kind == "shared" else None, created_by, now, now))
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
    found = c.execute("SELECT id, level FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type=? AND grantee_id=?",
                      (row["id"], grantee_type, grantee_id)).fetchone()
    if found:
        if RANK[level] > RANK.get(found["level"], 0):
            c.execute("UPDATE mailbox_accesses SET level=?, granted_by=?, updated_at=? WHERE id=?",
                      (level, granted_by, now, found["id"]))
        return found["id"]
    access_id = H.new_id()
    c.execute("INSERT INTO mailbox_accesses(id, mailbox_id, grantee_type, grantee_id, level, granted_by, created_at, "
              "updated_at) VALUES(?,?,?,?,?,?,?,?)",
              (access_id, row["id"], grantee_type, grantee_id, level, granted_by, now, now))
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
            c.execute("DELETE FROM mailbox_accesses WHERE mailbox_id=? AND grantee_type='bot' AND grantee_id=?",
                      (old["id"], old_bot))
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


# ---------------------------------------------------------------------------------------------------- backfill
def _table(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(c, name):
    return {row[1] for row in c.execute(f"PRAGMA table_info({name})")}


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


def _configs(c):
    """{bot id: stored config} for every bot that is not archived."""
    if not (_table(c, "bots") and _table(c, "bot_config")):
        return {}
    from . import bot_settings as BS
    live = {row["id"] for row in c.execute("SELECT id, state FROM bots") if row["state"] != "archived"}
    sql = (f"SELECT bc.bot AS bot, {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN}" if _table(c, "bot_settings")
           else "SELECT bot, config FROM bot_config")
    out = {}
    for row in c.execute(sql):
        if row["bot"] not in live:
            continue
        try:
            out[row["bot"]] = json.loads(row["config"] or "{}")
        except ValueError:
            out[row["bot"]] = {}
    for bot in live:
        out.setdefault(bot, {})
    return out


def ever_signed_in(c, person_id):
    """Whether this roster person has ever used Tico themselves: a sign-in session, an API token, a message they sent,
    or anything they did (an event they are the actor of). Sessions expire, the rest stays."""
    actor = H.human_actor(person_id)
    for table, column, value in (("oidc_sessions", "human", person_id), ("human_tokens", "human", person_id),
                                 ("messages", "sender_id", actor), ("events", "actor", actor)):
        if _table(c, table) and column in _columns(c, table) and c.execute(
                f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (value,)).fetchone():
            return True
    return False


def placeholders(c, people, configs):
    """Roster entries that stand for a mailbox, not a person ("legal@ (mailbox)", read by Counsel): one with a message
    bot that reads exactly their email, who has never signed in. Their mailbox is a shared inbox, linked to nobody.
    {person_id: address}."""
    from . import routines
    out = {}
    for person in people:
        bot, email = person.get("inbox_bot"), _addr(person.get("email"))
        if not bot or bot not in configs or not routines.MAILBOX.fullmatch(email):
            continue
        if _addr(routines.declared_mailbox(configs[bot]) or email) == email and not ever_signed_in(c, person["id"]):
            out[person["id"]] = email
    return out


def plan(c):
    """What the backfill would write, from today's sources: ({address: {kind, person_id, connection}},
    {(address, grantee_type, grantee_id): level}). Nobody loses the access they had through a message bot, a bot.yaml
    gmail entry, a Connect Google grant or a mail automation; a manager's automatic view of reports' mail is dropped."""
    from . import people as P
    from . import routines
    roster = routines._roster(c) if _table(c, "registry_metadata") and _table(c, "humans") else {"people": []}
    everyone = roster.get("people") or []
    by_email = {}
    for person in everyone:
        email = _addr(person.get("email"))
        if email:
            by_email.setdefault(email, person)
    live_people = {p["id"]: p for p in everyone if not p.get("hidden")}
    configs = _configs(c)
    boxes, grants = {}, {}
    stand_ins = placeholders(c, live_people.values(), configs)
    not_people = set(stand_ins.values())

    def box(address, connection="workspace"):
        address = _addr(address)
        if not routines.MAILBOX.fullmatch(address):
            return None
        if address not in boxes:
            kind, pid = ("shared", None) if address in not_people else kind_for(address, by_email)
            boxes[address] = {"kind": kind, "person_id": pid, "connection": connection}
        elif connection == "personal":
            boxes[address]["connection"] = "personal"
        return address

    def give_(address, kind, ident, level):
        if not address or not ident:
            return
        found = boxes[address]
        if kind == "person" and found["kind"] == "personal" and found["person_id"] == ident:
            return                                      # their own mailbox: no row needed
        key = (address, kind, ident)
        grants[key] = _best(grants.get(key, "none"), level)

    operator_of = {}
    if _table(c, "bot_config") and "owner_id" in _columns(c, "bot_config"):
        operator_of = {row["bot"]: row["owner_id"] for row in c.execute("SELECT bot, owner_id FROM bot_config")}

    # What each person's message bot handles (routines.message_bot_mailboxes, without dropping a mailbox two bots
    # share): the bot by what its gmail entry asked; the person, when it is not their own address, so they keep it.
    declared = {}
    for person in live_people.values():
        bot = person.get("inbox_bot")
        if not bot or bot not in configs:
            continue
        config = configs[bot]
        address = box(routines.declared_mailbox(config) or person.get("email"))
        if not address:
            continue
        declared.setdefault(person["id"], []).append(address)
        entry = next((e for e in _gmail_entries(config) if _addr(e.get("identity")) == address), None)
        give_(address, "bot", bot, manifest_level(entry))
        if person["id"] in stand_ins:
            # A mailbox placeholder: the person it reports to keeps it (else the bot's operator), Read and write.
            keeper = H.actor_id(person.get("reports_to") or "")
            if keeper not in live_people:
                keeper = H.actor_id(operator_of.get(bot) or "")
            if keeper in live_people and keeper != person["id"]:
                give_(address, "person", keeper, "read_write")
        elif address != _addr(person.get("email")):
            give_(address, "person", person["id"], "read_write" if boxes[address]["kind"] == "shared" else "read")

    # Every bot's gmail entries (bot.yaml `tools:`), and `org_read: true`: read on the mailboxes below its person.
    for bot, config in configs.items():
        who = P.inbox_person(bot, roster)
        own = routines.declared_mailbox(config) or _addr((who or {}).get("email"))
        for entry in _gmail_entries(config):
            address = box(entry.get("identity"))
            if not address:
                continue
            give_(address, "bot", bot, manifest_level(entry))
            if entry.get("org_read") is True:
                person = P.person_by_email(address, roster) or (who if who and address == own else None)
                if not person:
                    continue
                for below in P.below(person["id"], roster)[1:]:
                    if below.get("hidden"):
                        continue
                    for other in declared.get(below["id"]) or [_addr(below.get("email"))]:
                        other = box(other)
                        if other:
                            give_(other, "bot", bot, "read")

    # Live Connect Google grants: the mailbox its person connected.
    if _table(c, "google_connects"):
        have = _columns(c, "google_connects")
        live = "grant_hash IS NOT NULL AND " if "grant_hash" in have else ""
        for row in c.execute(f"SELECT person_id, mailbox FROM google_connects WHERE {live}status<>'disconnected'"):
            address = box(row["mailbox"], "personal")
            if address and row["person_id"] in live_people:
                give_(address, "person", row["person_id"], "read_write")

    # The mailboxes the server already keeps a copy of.
    if _table(c, "mail_mailboxes"):
        for row in c.execute("SELECT address FROM mail_mailboxes"):
            box(row["address"])

    # Mail automations: their owner keeps hearing the mailbox they hear today (automations.may_read_inbox before
    # this change: a person's own email and their message bot's mailboxes; a bot's, its operator's).
    if _table(c, "automations"):
        operators = operator_of

        def heard(person_id):
            person = live_people.get(person_id)
            if not person:
                return []
            email = _addr(person.get("email"))
            return list(dict.fromkeys((declared.get(person_id) or []) + ([email] if "@" in email else [])))

        for row in c.execute("SELECT owner_id, flow FROM automations WHERE deleted_at IS NULL"):
            try:
                flow = json.loads(row["flow"] or "{}")
            except ValueError:
                continue
            owner = str(row["owner_id"] or flow.get("owner") or "")
            kind, _, ident = (("person:" + owner[6:]) if owner.startswith("human:") else owner).partition(":")
            if kind == "bot":
                person_id = H.actor_id(operators.get(ident) or "")
                if ident not in configs:
                    continue
            elif kind == "person":
                person_id = ident
            else:
                continue
            old = heard(person_id)
            trigger = flow.get("trigger") or {}
            wanted = []
            if trigger.get("type") == "message" and "@" in str(trigger.get("inbox") or ""):
                wanted.append(_addr(trigger["inbox"]))
            if any(step.get("type") == "find" and step.get("source") == "mail" for step in flow.get("steps") or []):
                wanted.extend(old)
            for address in wanted:
                if address not in old:
                    continue                            # not heard today either
                address = box(address)
                if address:
                    give_(address, "bot" if kind == "bot" else "person", ident, "read_write")
    return boxes, grants


def backfill(c):
    """Write `plan(c)`: a row per mailbox and the explicit grants, never changing a row or a grant already there, so a
    second run (or a run after people changed access) writes nothing. Returns {"mailboxes": n, "accesses": n} made."""
    if not (_table(c, "mailboxes") and _table(c, "mailbox_accesses")):
        return {"mailboxes": 0, "accesses": 0}
    boxes, grants = plan(c)
    now, made = H.now(), {"mailboxes": 0, "accesses": 0}
    for address, found in sorted(boxes.items()):
        cur = c.execute(
            "INSERT OR IGNORE INTO mailboxes(id, address, provider, person_id, kind, connection, status, shared_by, "
            "shared_at, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?,?,?)",
            (H.new_id(), address, PROVIDER, found["person_id"], found["kind"], found["connection"],
             MIGRATION if found["kind"] == "shared" else None, now if found["kind"] == "shared" else None,
             MIGRATION, now, now))
        made["mailboxes"] += cur.rowcount
    ids = {row["address"]: row["id"] for row in c.execute("SELECT id, address FROM mailboxes")}
    for (address, kind, ident), lvl in sorted(grants.items()):
        if address not in ids:
            continue
        cur = c.execute("INSERT OR IGNORE INTO mailbox_accesses(id, mailbox_id, grantee_type, grantee_id, level, "
                        "granted_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (H.new_id(), ids[address], kind, ident, lvl, MIGRATION, now, now))
        made["accesses"] += cur.rowcount
    return made
