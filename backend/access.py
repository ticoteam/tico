"""Who owns this environment, who may sign in, and who is on the roster: stored, not configured.

TICO_OWNER_EMAIL and registry/hub-access.yaml only seed the first boot. From then on the owner
and the access list are two revisioned records (like the provider choice in providers.py), so
the owner changes them in the app without editing a file or restarting:

  owner   {email}                                    who holds owner rights
  access  {allowed, allowed_domains, admins,         who may join, who the Admins are (`bot_admins` is
          member_bot_limit}                          the old name, still read and kept in step for
                                                     one release), and how many bots a member may have
                                                     (MEMBER_BOT_LIMIT when unset)

The roster stays the gate for sign-in: a person on it (and not marked as left) is in. An
address on `allowed`, or at an allowed domain, joins the roster on its first verified sign-in.
"""

import json
import logging
import re

import yaml

from . import hubdb as H
from . import people as P
from .store import Problem

OWNER = "owner"
ACCESS = "access"


def emails(values):
    """Lowercase, unique, in the order given; empties dropped."""
    seen = []
    for value in values or []:
        item = str(value or "").strip().lower()
        if item and item not in seen:
            seen.append(item)
    return seen


def domains(values):
    return [d.lstrip("@") for d in emails(values)]


def _load_json(c, key):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (key,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except ValueError:
        value = None
    return value if isinstance(value, dict) else None


def _file_access(settings):
    try:
        document = yaml.safe_load((settings.registry_dir / "hub-access.yaml").read_text())
    except (OSError, yaml.YAMLError):
        document = None
    return document if isinstance(document, dict) else {}


def _access_seed(settings):
    document = _file_access(settings)
    return {"allowed": emails(document.get("allowed")),
            "allowed_domains": domains(document.get("allowed_domains")),
            "bot_admins": emails(document.get("bot_admins"))}


MEMBER_BOT_LIMIT = 25
# The default before 0.2.16. A stored 5 is taken for that default, not a choice (raise_bot_limit).
OLD_BOT_LIMIT = 5
BOT_LIMIT_RAISED = "access_bot_limit"
# Addresses at these are anyone's: they never make someone a "coworker" of the owner.
PUBLIC_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
               "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
               "gmx.com", "gmx.net", "mail.com", "zoho.com", "fastmail.com", "hey.com", "qq.com", "163.com"}


def load_owner(c, settings):
    stored = _load_json(c, OWNER)
    if stored is None:
        return {"email": str(settings.owner_email or "").strip().lower(), "revision": 0,
                "updated": "", "updated_by": "", "source": "environment"}
    return {"email": str(stored.get("email") or "").strip().lower(),
            "revision": int(stored.get("revision") or 0), "updated": str(stored.get("updated") or ""),
            "updated_by": str(stored.get("updated_by") or ""), "source": str(stored.get("source") or "")}


def owner_and_admin_emails(c):
    """The owner's address, then the Admins', as stored at database initialization (`seed`) and revised since: for code
    with no settings at hand that must find someone who can act (an org rule hold's reviewer, backend/org_rule_check.py)."""
    owner = (_load_json(c, OWNER) or {}).get("email")
    stored = _load_json(c, ACCESS) or {}
    return emails([owner, *(stored["admins"] if "admins" in stored else stored.get("bot_admins") or [])])


def load_access(c, settings):
    stored = _load_json(c, ACCESS)
    source = "owner" if stored else "environment"
    stored = stored if stored is not None else _access_seed(settings)
    # `bot_admins` is what this list was called before Admin became a company role: read it when the
    # record has no `admins` yet.
    admins = emails(stored["admins"] if "admins" in stored else stored.get("bot_admins"))
    return {"allowed": emails(stored.get("allowed")), "allowed_domains": domains(stored.get("allowed_domains")),
            "admins": admins, "bot_admins": admins,
            "member_bot_limit": max(0, int(stored.get("member_bot_limit", MEMBER_BOT_LIMIT))),
            "revision": int(stored.get("revision") or 0),
            "updated": str(stored.get("updated") or ""), "updated_by": str(stored.get("updated_by") or ""),
            "source": str(stored.get("source") or source)}


def seed(c, settings, now):
    """Persist the environment's owner and hub-access file once; later changes are revisioned
    and the environment is never consulted again. Called at database initialization."""
    if not _load_json(c, OWNER) and settings.owner_email:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  (OWNER, json.dumps({"email": settings.owner_email.strip().lower(), "revision": 1,
                                      "updated": now, "updated_by": "environment", "source": "environment"},
                                     sort_keys=True)))
    if not _load_json(c, ACCESS):
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  (ACCESS, json.dumps({**_access_seed(settings), "revision": 1, "updated": now,
                                       "updated_by": "environment", "source": "environment"}, sort_keys=True)))


# ----------------------------------------------------------------------------- per-bot access
# Who may see, read and write to a bot is stored on the bot (backend/bot_access.py). The old
# `private_owners` and `routing_permissions` lists in hub-access.yaml are no longer read for
# enforcement: the first start after they stopped counting resets every bot to Open and leaves the
# owner one note on Health saying so.
BOT_ACCESS = "bot_access"
BOT_ACCESS_NOTICE = "bot_access_notice"
RETIRED_LISTS = ("private_owners", "routing_permissions", "dispatch_permissions")
RETIRED_NOTICE = ("hub-access.yaml private/routing lists are no longer used; bots are now Open; "
                  "set access in Settings > Bots")


def retire_bot_lists(c, settings, now):
    """Once per database: note that the file's private/routing lists are retired. Returns the
    names of the lists that were present (empty when there was nothing to warn about)."""
    if _load_json(c, BOT_ACCESS) is not None:
        return []
    document = _file_access(settings)
    present = [name for name in RETIRED_LISTS if document.get(name)]
    _store(c, BOT_ACCESS, {"migrated": now, "retired": present})
    if present:
        logging.getLogger("tico.access").warning("%s (found: %s)", RETIRED_NOTICE, ", ".join(present))
        _store(c, BOT_ACCESS_NOTICE, {"message": RETIRED_NOTICE, "lists": present, "created": now})
    return present


def raise_bot_limit(c, now):
    """Once per database: a company still on the old default of 5 bots per member gets the new default.

    The old page saved the limit with every allow-list save, so a stored 5 does not show that anyone chose it.
    It counts as chosen only when someone changed it to 5 from another number (an `access.limits_updated`
    event whose `before` is not 5); any other stored number is kept. Returns the limit in force afterwards."""
    if _load_json(c, BOT_LIMIT_RAISED) is not None:
        return None
    stored = _load_json(c, ACCESS)
    before = stored.get("member_bot_limit") if stored else None
    chosen = c.execute(
        "SELECT 1 FROM events WHERE action='access.limits_updated' AND json_extract(detail_json,'$.after')=? "
        "AND coalesce(json_extract(detail_json,'$.before'),-1)<>? LIMIT 1", (OLD_BOT_LIMIT, OLD_BOT_LIMIT)).fetchone()
    raised = stored is not None and before == OLD_BOT_LIMIT and not chosen
    if raised:
        _store(c, ACCESS, {**stored, "member_bot_limit": MEMBER_BOT_LIMIT})
    _store(c, BOT_LIMIT_RAISED, {"migrated": now, "before": before, "raised": raised})
    return MEMBER_BOT_LIMIT if raised or before is None else before


def bot_access_notice(c):
    """The note the owner still has to read on Health, or None."""
    return _load_json(c, BOT_ACCESS_NOTICE)


def clear_bot_access_notice(c):
    c.execute("DELETE FROM registry_metadata WHERE key=?", (BOT_ACCESS_NOTICE,))


def _store(c, key, record):
    if key == ACCESS:
        # The Admins list is kept under both names so a rollback to the previous release still finds it.
        admins = emails(record["admins"] if "admins" in record else record.get("bot_admins"))
        record = {**record, "admins": admins, "bot_admins": admins}
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (key, json.dumps(record, sort_keys=True)))
    if key in (ACCESS, OWNER):
        from . import people_mirror
        people_mirror.changed()                    # roles and sign-in lists changed (backend/people_mirror.py)


def admitted(access, email):
    """Whether an address is on the allowed list or at an allowed domain."""
    email = str(email or "").strip().lower()
    if not email or "@" not in email:
        return False
    return email in access["allowed"] or email.rsplit("@", 1)[1] in access["allowed_domains"]


def acl(c, settings):
    """The `hub-access.yaml` shape with the stored owner and lists, for the Slack gateway."""
    access = load_access(c, settings)
    return {**_file_access(settings), "owner": load_owner(c, settings)["email"],
            "allowed": access["allowed"], "allowed_domains": access["allowed_domains"],
            "bot_admins": access["admins"]}


# ----------------------------------------------------------------------------- roster
def _save_roster(c, roster):
    from .report_order import roster_saved
    roster_saved(c, roster)                        # a person who joins a manager goes last among its reports
    c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO UPDATE SET "
              "value_json=excluded.value_json", (json.dumps(roster, sort_keys=True),))
    from .finance import actions as FA
    FA.revalidate_org(c)                           # Finance's view grants follow the org chart at once (spec 7.5)
    from . import people_mirror
    people_mirror.changed()                        # the Tico account gets the sign-in list again (backend/people_mirror.py)


save_roster = _save_roster


def _sync_human(c, row):
    teams = sorted({t for t in [row.get("team"), *(row.get("primary_for") or [])] if t})
    values = {"id": row["id"], "name": row["name"], "email": row["email"],
              "slack_id": row.get("slack_id") or None, "teams_json": json.dumps(teams)}
    if H.human(c, row["id"]) is None:
        c.execute("INSERT INTO humans (id, name, email, slack_id, teams_json) "
                  "VALUES (:id, :name, :email, :slack_id, :teams_json)", values)
    else:
        c.execute("UPDATE humans SET name=:name, email=:email, teams_json=:teams_json WHERE id=:id", values)


def _email_taken(c, email, except_id=""):
    row = c.execute("SELECT id FROM humans WHERE lower(email)=? AND id<>?", (email, except_id)).fetchone()
    return row is not None


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(value):
    email = str(value or "").strip().lower()
    if not _EMAIL.match(email):
        raise Problem("email", "Enter a full email address, like name@company.com", 422)
    return email


def _new_id(c, email):
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0]).strip("-") or "person"
    pid, n = base, 1
    while H.human(c, pid):
        n += 1
        pid = f"{base}-{n}"
    return pid


def _allow(c, email):
    """Put an address on the sign-in allow list (no revision bump: the list only grows)."""
    stored = _load_json(c, ACCESS)
    if stored is None or email in emails(stored.get("allowed")):
        return
    _store(c, ACCESS, {**stored, "allowed": emails([*(stored.get("allowed") or []), email])})


def add_person(c, actor, roster, *, name, email, title="", team="", reports_to="", event="person.added"):
    """A new roster person, on the sign-in allow list too so they can actually sign in, and the
    people the org chart shows; returns (roster, row)."""
    email = _valid_email(email)
    if _email_taken(c, email):
        raise Problem("conflict", "Someone with that email is already on the roster", 409)
    if reports_to and not P.person(reports_to, roster):
        raise Problem("not_found", "Reports-to person was not found", 404)
    row = P._person({"id": _new_id(c, email), "name": name or "", "email": email,
                     "title": title, "team": team, "reports_to": reports_to})
    _save_roster(c, {**roster, "people": [*roster["people"], row]})
    _sync_human(c, row)
    _allow(c, email)
    H.event(c, actor, event, row["id"], {"email": email})
    return {**roster, "people": [*roster["people"], row]}, row


# ----------------------------------------------------------------------------- roles and what a member may do
def domain_of(email):
    email = str(email or "").strip().lower()
    return email.rsplit("@", 1)[1] if "@" in email else ""


def roster_domains(c):
    """The company email domains already on the roster (never gmail.com and its kind), the most common first."""
    stored = _load_json(c, "people")
    people = stored.get("people") if stored else H.humans(c)
    counts = {}
    for person in people or []:
        domain = domain_of((person or {}).get("email"))
        if domain and domain not in PUBLIC_MAIL and _DOMAIN.fullmatch(domain) and not (person or {}).get("hidden"):
            counts[domain] = counts.get(domain, 0) + 1
    return sorted(counts, key=lambda domain: (-counts[domain], domain))


def team_domain(value):
    """The team's email domain as first run takes it: blank, or a company domain (never gmail.com and its kind)."""
    domain = str(value or "").strip().lower().lstrip("@")
    if not domain:
        return ""
    if not _DOMAIN.fullmatch(domain):
        raise Problem("team_domain", f"{domain!r} is not a domain like company.com", 422)
    if domain in PUBLIC_MAIL:
        raise Problem("team_domain", f"{domain} is a public mail domain, not a company's", 422)
    return domain


def company_domains(c, settings, owner_email=""):
    """The email domains that make someone a coworker: the ones the owner allows to sign in, else the
    owner's own when that is a company address (never gmail.com and its kind). A team whose owner uses
    public mail has the domain the owner named at first run, then the company domains already on the roster."""
    listed = load_access(c, settings)["allowed_domains"]
    if listed:
        return listed
    domain = domain_of(owner_email or load_owner(c, settings)["email"])
    if domain and domain not in PUBLIC_MAIL:
        return [domain]
    named = str((((_load_json(c, "onboarding") or {}).get("names") or {}).get("team_domain")) or "").strip().lower()
    return list(dict.fromkeys([*([named] if named else []), *roster_domains(c)]))


def role_of(access, owner_email, email):
    email = str(email or "").strip().lower()
    if not email:
        return "member"
    if email == owner_email:
        return "owner"
    return "admin" if email in access["admins"] else "member"


def can_create_bots(person, role):
    return role in ("owner", "admin") or (person or {}).get("create_bots") is not False


def can_add_people(person, role, domains_):
    """Owners and Admins always; a member by their own setting, else because they work in the company domain."""
    if role in ("owner", "admin"):
        return True
    explicit = (person or {}).get("add_people")
    if isinstance(explicit, bool):
        return explicit
    return bool(domains_) and domain_of((person or {}).get("email")) in domains_


def check_may_add(c, settings, who_person, role, email, domains_):
    """Refuse unless this person may put `email` on the roster: a member may add coworkers, an owner or
    Admin anyone."""
    if role in ("owner", "admin"):
        return
    if not can_add_people(who_person, role, domains_):
        raise Problem("forbidden", "You may not add people. Ask an owner or an admin to add them, or to let you", 403)
    if domain_of(email) not in domains_:
        raise Problem("forbidden", "Adding someone outside " + (", ".join(domains_) or "the company's email domain")
                      + " needs an owner or an admin", 403)


def leave(c, people, row):
    """Take one person off the chart: end their tokens and sessions and move their reports up.
    Mutates `people` (the caller's list) and returns the person's new row."""
    row = {**row, "hidden": True}
    # Their API tokens outlive a browser session by up to a year.
    c.execute("UPDATE human_tokens SET revoked_at=? WHERE human=? AND revoked_at IS NULL", (H.now(), row["id"]))
    c.execute("DELETE FROM oidc_sessions WHERE human=?", (row["id"],))
    # Their reports move up to their manager rather than falling off the chart.
    for j, other in enumerate(people):
        if other.get("reports_to") == row["id"]:
            people[j] = {**other, "reports_to": row.get("reports_to") or ""}
    return row


def join_on_sign_in(c, email):
    """Someone the allow list admits signs in for the first time: they join as a normal person."""
    from .views import roster as load_roster
    _, row = add_person(c, "keeper", load_roster(c), name="", email=email, event="person.joined")
    return row["id"]


def assign_message_bot(c, roster, row, body):
    """Name `row`'s message bot, and the address it reads for them when that is not their own email. This is the
    one thing that lets a bot's runs ask a Docker computer for a mail token (backend/routines.token_mailboxes), so
    it is an owner's or admin's word: a bot cannot claim a mailbox by describing itself in its own repository."""
    from . import inbox_isolation, routines
    bot = row.get("inbox_bot") if body.inbox_bot is None else P._clean(body.inbox_bot)
    if bot:
        found = H.bot(c, bot)
        if not found or found["state"] == "archived":
            raise Problem("not_found", "That bot was not found", 404)
        other = next((p for p in roster["people"] if p["id"] != row["id"] and p.get("inbox_bot") == bot), None)
        if other:
            raise Problem("conflict", f"{bot} is already {other['name'] or other['id']}'s message bot", 409)
        row = {**row, "inbox_bot": bot}
        placed = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()
        if placed:
            inbox_isolation.check(c, bot, placed["runner_id"], {**roster, "people": [
                row if p["id"] == row["id"] else p for p in roster["people"]]})
        if body.mailbox is not None:
            routines.set_declared_mailbox(c, bot, body.mailbox)
    elif body.mailbox is not None:
        raise Problem("mailbox", "Name the message bot that reads it (inbox_bot) too", 422)
    else:
        row = {**row, "inbox_bot": None}
    return row


def edit_person(c, actor, roster, pid, body, access, owner_email):
    """Owner edits of a roster person. Returns (roster, access_changes)."""
    person = P.person(pid, roster)
    if not person:
        raise Problem("not_found", "Person not found", 404)
    row, changed = dict(person), {}
    if body.name is not None and body.name.strip() != row["name"]:
        row["name"], changed["name"] = body.name.strip() or row["name"], True
    if body.title is not None:
        row["title"], changed["title"] = body.title.strip(), True
    if body.team is not None:
        row["team"], changed["team"] = body.team.strip(), True
    old_email = row["email"]
    if body.email is not None and body.email.strip().lower() != old_email:
        if old_email and old_email == owner_email:
            raise Problem("owner", "Transfer ownership before changing the owner's email", 409)
        row["email"] = _valid_email(body.email)
        if _email_taken(c, row["email"], pid):
            raise Problem("conflict", "Someone with that email is already on the roster", 409)
        changed["email"] = True
    admins = list(access["admins"])
    if row["email"] != old_email and old_email in admins:
        admins = [row["email"] if e == old_email else e for e in admins]
    if body.left is False and row.get("hidden"):
        row["hidden"], changed["restored"] = False, True
    if body.sign_in is not None:
        if row["email"] and row["email"] == owner_email:
            raise Problem("owner", "The owner can always sign in", 409)
        row["sign_in"], changed["sign_in"] = bool(body.sign_in), bool(body.sign_in)
    role = body.role if body.role is not None else (None if body.bot_admin is None else "admin" if body.bot_admin else "member")
    if role is not None:
        if row["email"] == owner_email:
            raise Problem("owner", "The owner already is an owner", 409)
        if role == "admin" and not row["email"]:
            raise Problem("email", "An admin needs an email address", 422)
        admins = [e for e in admins if e != row["email"]]
        if role == "admin":
            admins.append(row["email"])
        changed["role"] = role
    if body.inbox_bot is not None or body.mailbox is not None:
        row = assign_message_bot(c, roster, row, body)
        changed["inbox_bot"] = row.get("inbox_bot") or ""
    if body.create_bots is not None:
        row["create_bots"], changed["create_bots"] = bool(body.create_bots), bool(body.create_bots)
    if body.add_people is not None:
        row["add_people"] = None if body.add_people == "default" else bool(body.add_people)
        changed["add_people"] = body.add_people
    row = P._person(row)
    _save_roster(c, {**roster, "people": [row if p["id"] == pid else p for p in roster["people"]]})
    _sync_human(c, row)
    return row, admins, changed


def rename_person(c, roster, pid, name):
    """A person's name on the roster, as they wrote it. Returns whether it changed."""
    person = P.person(pid, roster)
    name = " ".join(str(name or "").split())
    if not person or not name or name == person["name"]:
        return False
    row = P._person({**person, "name": name})
    _save_roster(c, {**roster, "people": [row if p["id"] == pid else p for p in roster["people"]]})
    _sync_human(c, row)
    return True


_DOMAIN = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}")


def sort_allowed(*lists):
    """What a person typed into "who may join", sorted into the two lists that are stored.

    An address (`ana@company.com`) lets that person in. A domain, written `company.com`, `@company.com` or
    `*@company.com`, lets anyone at it in. Anything else is refused by name: a wildcard inside an address
    (`a*@company.com`) or a malformed one would otherwise be stored and silently never match, and a public mail
    domain would let anyone with such an account join. Returns (addresses, domains), lowercased and unique."""
    people, found = [], []
    for raw in (value for values in lists for value in values or []):
        item = str(raw or "").strip().lower()
        if not item:
            continue
        local, at, rest = item.rpartition("@")
        if at and local not in ("", "*"):
            if "*" in local or not _EMAIL.match(item):
                raise Problem("allow_entry", f"{item!r} is not an address like name@company.com, or a domain like company.com", 422)
            target = people
        else:
            rest = rest if at else item
            if not _DOMAIN.fullmatch(rest):
                raise Problem("allow_entry", f"{item!r} is not an address like name@company.com, or a domain like company.com", 422)
            if rest in PUBLIC_MAIL:
                raise Problem("allow_entry", f"{rest} is a public mail domain: that would let anyone with a "
                              f"{rest.split('.')[0].title()} account join. List the people's addresses instead", 422)
            item, target = rest, found
        if item not in target:
            target.append(item)
    return people, found


def save_access(c, actor, body, now, *, bot_admins=None, settings=None):
    """Replace the allow list (and optionally the Admins) at the revision read."""
    before = load_access(c, settings)
    if before["revision"] != int(body.get("expected_revision") or 0):
        raise Problem("conflict", "The access list changed since you opened it; reload and try again", 409)
    # Only what was submitted is sorted and checked: changing the Admins alone leaves who may join as it was.
    people, found = (sort_allowed(body.get("allowed"), body.get("allowed_domains"))
                     if "allowed" in body or "allowed_domains" in body else (before["allowed"], before["allowed_domains"]))
    after = {"allowed": people, "allowed_domains": found,
             "admins": before["admins"] if bot_admins is None else emails(bot_admins),
             "member_bot_limit": before["member_bot_limit"],
             "revision": before["revision"] + 1, "updated": now, "updated_by": actor, "source": "owner"}
    _store(c, ACCESS, after)
    return before, after


def transfer(c, actor, roster, owner, target_id, *, expected_revision, previous_bot_admin, now, settings):
    """Make another active person the owner, in the caller's transaction. Returns the new owner
    record; the previous owner becomes a normal person, optionally a bot administrator."""
    if owner["revision"] != expected_revision:
        raise Problem("conflict", "Ownership changed since you opened this page; reload and try again", 409)
    target = P.person(target_id, roster)
    if not target or target.get("hidden"):
        raise Problem("not_found", "Choose someone who is active on the roster", 404)
    if not target["email"]:
        raise Problem("email", "The new owner needs an email address to sign in with", 422)
    if not target.get("sign_in", True):
        raise Problem("sign_in", "Turn on sign-in for them first", 409)
    if target["email"] == owner["email"]:
        raise Problem("conflict", "That person is already the owner", 409)
    previous = P.person_by_email(owner["email"], roster)
    stored = _load_json(c, OWNER) or {}
    history = [*(stored.get("history") or []), {k: owner[k] for k in ("email", "revision", "updated", "updated_by", "source")}]
    after = {"email": target["email"], "revision": owner["revision"] + 1, "updated": now,
             "updated_by": actor, "source": "transfer", "previous": owner["email"], "history": history[-OWNER_HISTORY:],
             "retired": emails(stored.get("retired"))}
    _store(c, OWNER, after)
    # The owner's earlier Strategy Bot chat, history now, stays with the owner seat: the new owner reads it under
    # "Earlier chats" (backend/assistant.py). Everyone keeps their own Assistant.
    from .assistant import move_strategy_room
    move_strategy_room(c, actor, "human:" + target["id"])
    access = load_access(c, settings)
    if previous_bot_admin and owner["email"]:
        _store(c, ACCESS, {**access, "admins": emails([*access["admins"], owner["email"]]),
                           "revision": access["revision"] + 1, "updated": now, "updated_by": actor,
                           "source": "owner"})
    H.event(c, actor, "owner.transferred", target["id"],
            {"from": owner["email"], "from_person": (previous or {}).get("id", ""),
             "to": target["email"], "previous_owner_bot_admin": bool(previous_bot_admin)})
    return load_owner(c, settings)


OWNER_HISTORY = 20


def retired_emails(c):
    """Addresses the owner moved off with an email change: they never auto-join as a new person, even at an allowed
    domain, so a sign-in with the old address cannot split the owner into two people. The People tab can still add one."""
    return emails((_load_json(c, OWNER) or {}).get("retired"))


def _keep_mailbox(c, person, address):
    """A message bot that declares no mailbox reads its person's email (backend/routines.py message_bot_mailboxes). A
    sign-in change is not a mail change, so the address it reads now is written down before the email moves."""
    from . import routines
    bot = person.get("inbox_bot")
    found = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone() if bot else None
    if found and not routines.declared_mailbox(json.loads(found["config_json"] or "{}")):
        routines.set_declared_mailbox(c, bot, address)


def _follow_address(c, pid, old, new):
    """Meetings filed for a person by address (backend/meetings.py filed_for), a meeting delivery they asked for and a
    Granola sync, which acts as the address it was connected with: all follow the person when the address changes."""
    c.execute("UPDATE meetings SET owner=?, metadata_json=CASE WHEN lower(json_extract(metadata_json,'$.owner'))=? "
              "THEN json_set(metadata_json,'$.owner',?) ELSE metadata_json END WHERE lower(owner)=?", (new, old, new, old))
    c.execute("UPDATE meeting_deliveries SET requested_by=? WHERE lower(requested_by)=?", (new, old))
    c.execute("UPDATE granola_connections SET email=? WHERE actor=?", (new, "human:" + pid))


def change_email(c, actor, roster, pid, new_email, *, now, settings, via="app"):
    """A person's sign-in address changed in place, in the caller's transaction: the roster and `humans` (same id), the
    allow list and the Admins where the old address was on them, and what follows the address. The owner's address
    goes through change_owner_email. Used when a verified Tico account sign-in arrives under a new address."""
    person = P.person(pid, roster)
    if not person:
        raise Problem("not_found", "Person not found", 404)
    new, old = _valid_email(new_email), person["email"]
    if new == old:
        return person
    if _email_taken(c, new, pid):
        raise Problem("conflict", new + " already belongs to someone else on the roster", 409)
    # An address on the Admins, the owner's or the allow list is never a person's sign-in address: moving there would
    # hand the person that role (the Admins list) or leave someone else's entry in place.
    before = load_access(c, settings)
    if new in before["admins"] or new in before["allowed"] or new == load_owner(c, settings)["email"]:
        raise Problem("conflict", new + " is on the owner's, Admins or allow list, so it cannot be a sign-in address", 409)
    if old:
        _keep_mailbox(c, person, old)
    row = P._person({**person, "email": new})
    _save_roster(c, {**roster, "people": [row if p["id"] == pid else p for p in roster["people"]]})
    _sync_human(c, row)
    access = _load_json(c, ACCESS)
    if access is not None and old:
        def swap(values):
            return emails(new if e == old else e for e in values)
        allowed, admins = swap(before["allowed"]), swap(before["admins"])
        if allowed != before["allowed"] or admins != before["admins"]:
            _store(c, ACCESS, {**access, "allowed": allowed, "admins": admins,
                               "revision": before["revision"] + 1, "updated": now, "updated_by": actor, "source": "sign_in"})
    if old:
        _follow_address(c, pid, old, new)
    H.event(c, actor, "person.email_changed", pid, {"from": old, "to": new, "via": via})
    return row


def change_owner_email(c, actor, roster, owner, new_email, *, expected_revision, now, settings, via="app"):
    """The owner's own sign-in address, changed in place in the caller's transaction: the same person keeps every task,
    approval, grant and conversation, and every copy of the address moves at once (the roster, `humans`, the owner
    record with the old one kept in its history, the allow list and the Admins). Returns the new owner record."""
    if owner["revision"] != expected_revision:
        raise Problem("conflict", "Ownership changed since you opened this page; reload and try again", 409)
    new = _valid_email(new_email)
    old = owner["email"]
    if new == old:
        raise Problem("conflict", "That is already the owner's sign-in email", 409)
    person = P.person_by_email(old, roster) if old else None
    if not person:
        raise Problem("not_found", "The owner (" + (old or "unset") + ") is not on the roster", 404)
    # Checked again here, under the write lock: the caller's owner role was read when it signed in, and a transfer that
    # committed since would otherwise let it change the new owner's address. The host command has the host's word.
    if via != "host" and actor != "human:" + person["id"]:
        raise Problem("forbidden", "Only the owner changes their own sign-in email, and you are no longer the owner; "
                      "nothing was changed", 403)
    other = P.person_by_email(new, roster)
    if (other and other["id"] != person["id"]) or _email_taken(c, new, person["id"]):
        raise Problem("conflict", new + " already belongs to someone else on the roster. Change or remove their "
                      "email first; nothing was changed", 409)
    _keep_mailbox(c, person, old)
    row = P._person({**person, "email": new})
    _save_roster(c, {**roster, "people": [row if p["id"] == person["id"] else p for p in roster["people"]]})
    _sync_human(c, row)
    stored = _load_json(c, OWNER) or {}
    history = [*(stored.get("history") or []), {k: owner[k] for k in ("email", "revision", "updated", "updated_by", "source")}]
    after = {"email": new, "revision": owner["revision"] + 1, "updated": now, "updated_by": actor,
             "source": "email_change", "previous": old, "history": history[-OWNER_HISTORY:],
             "retired": [e for e in emails([*(stored.get("retired") or []), old]) if e != new]}
    _store(c, OWNER, after)
    # The old address leaves the lists rather than staying beside the new one: left on `allowed`, it would join a
    # second person on its next sign-in.
    access = _load_json(c, ACCESS)
    if access is not None:
        before = load_access(c, settings)

        def swap(values):
            return emails(new if e == old else e for e in values)
        allowed, admins = swap(before["allowed"]), swap(before["admins"])
        if allowed != before["allowed"] or admins != before["admins"]:
            _store(c, ACCESS, {**access, "allowed": allowed, "admins": admins,
                               "revision": before["revision"] + 1, "updated": now, "updated_by": actor, "source": "owner"})
    _follow_address(c, person["id"], old, new)
    H.event(c, actor, "owner.email_changed", person["id"],
            {"from": old, "to": new, "revision": after["revision"], "via": via})
    return load_owner(c, settings)


def home_domain(owner_email, allowed_domains):
    """The domain the People tab offers as "Anyone at <domain> can sign in": the owner's own when it is a
    company address, else the first allowed domain, else none."""
    domain = domain_of(owner_email)
    if domain and domain not in PUBLIC_MAIL:
        return domain
    return (allowed_domains or [""])[0]


def view(c, settings, roster, proxy_kind, owner_id):
    """What the People tab renders."""
    from . import directory, people_remove
    owner, access = load_owner(c, settings), load_access(c, settings)
    domains_ = company_domains(c, settings, owner["email"])
    home = home_domain(owner["email"], access["allowed_domains"])
    people = []
    for p in roster["people"]:
        left = bool(p.get("hidden"))
        role = role_of(access, owner["email"], p["email"])
        people.append({"id": p["id"], "name": p["name"], "email": p["email"], "title": p["title"],
                       "team": p["team"], "left": left, "owner": role == "owner",
                       "role": role, "bot_admin": role == "admin",
                       "create_bots": can_create_bots(p, role),
                       "add_people": can_add_people(p, role, domains_),
                       "add_people_default": p.get("add_people") is None,
                       "sign_in": bool(p.get("sign_in", True)),
                       "can_sign_in": bool(p["email"]) and not left and bool(p.get("sign_in", True)),
                       # Left and nothing to keep: the owner may remove them for good (backend/people_remove.py).
                       "removable": left and people_remove.removable(c, p)})
    return {"owner": {**owner, "person": owner_id}, "people": people, **{
        k: access[k] for k in ("allowed", "allowed_domains", "admins", "bot_admins", "member_bot_limit", "revision",
                               "updated", "updated_by")},
        "company_domains": domains_,
        "company_domain_source": ("allowed" if access["allowed_domains"] else "owner" if home_domain(owner["email"], []) else
                                  "team" if domains_ else "none"),
        # "Anyone at <home_domain> can sign in" is that domain on the allow list.
        "home_domain": home, "domain_sign_in": bool(home) and home in access["allowed_domains"],
        # How people get here: a directory source set means Sync, none means they are added by hand.
        "directory": directory.load(c)["source"],
        "proxy": proxy_kind}
