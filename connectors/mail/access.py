"""Who may act as which mailbox, and with which verbs.

The answer is `bot.yaml` (older: `employee.yaml`), nothing else. `tools:` entries (older: `access:`) with service `gmail` or
`google-calendar` name an identity (the mailbox) and the verbs allowed on it
(hub policies/access.md). Not listed means not allowed, and refusing is exit 2.

The owner is a special identity: `--as <owner handle>` holds every verb on every mailbox, because the owner is the
human using the same CLI interactively.

Two things beyond the declared verbs, both deliberate and documented in docs/mail.md:
  - `read` also allows labelling, archiving, marking read, and starring *inside* the mailbox,
    unless that gmail entry sets `read_only: true`.
    Moving a message between folders is not a send; nothing leaves the company.
  - `draft` and `send` are recorded here for stage 2; stage 1 never writes an outbound message.
"""

import os

from . import PROJECTS, Refused, bot_folders, declared_tools, hub_bot, load_yaml, manifest_file, owner_handle, repo_dir

OWNER = owner_handle()                    # the owner's roster handle; see connectors/mail/__init__.py
MAIL_SERVICES = ("gmail", "google-calendar")
VERBS = ("read", "draft", "send", "unsubscribe", "schedule")
CALENDAR_VERBS = ("read", "schedule")
CALENDAR_ACTIONS = {"calendar_read": "read", "calendar_schedule": "schedule"}
# Commands that only move a message around inside the mailbox. `read` is enough for these.
STATE_VERBS = {"label": "read", "archive": "read", "mark_read": "read", "star": "read",
               "triaged": "read", "rules": "read"}

ASK_OWNER = ("Changing `tools:` is the owner's call: open an Issue with owner:" + OWNER + " and "
             "type:decision naming the mailbox and why (hub policies/access.md).")


def manifest_path(slug):
    return manifest_file(repo_dir(PROJECTS, slug))


def where(slug):
    """`<repo folder>/<manifest>` as a message names it: bot-<slug>/bot.yaml, or emp-<slug>/employee.yaml for an older bot."""
    p = manifest_path(slug)
    return f"{p.parent.name}/{p.name}"


def forward_to(manifest):
    """The addresses the owner lists as this bot's forward targets: `forward_to:` in bot.yaml (a list, or one
    comma-separated string), lowercased, one of each. Mail to them needs no per-message approval once
    `outbound_send` is on (docs/mail.md, "Turning sending on")."""
    raw = (manifest or {}).get("forward_to")
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    out = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        addr = str(item or "").strip().lower()
        if "@" in addr and " " not in addr and addr not in out:
            out.append(addr)
    return out


def employee(slug=None, env=None):
    """The employee this run is. --as wins, then HUB_BOT or HUB_EMPLOYEE (both set by the runner)."""
    env = os.environ if env is None else env
    s = (slug or hub_bot(env)).strip().lower()
    if not s:
        raise Refused("no employee: pass --as <slug>.",
                      "The runner sets HUB_BOT for every turn; outside a turn, name the "
                      "employee yourself. The owner uses --as " + OWNER + ".")
    return s


def load(slug):
    if slug == OWNER:
        return {"name": OWNER, "access": []}
    return load_yaml(manifest_path(slug), where(slug))


def entries(manifest):
    return [e for e in declared_tools(manifest)
            if isinstance(e, dict) and str(e.get("service", "")).lower() in MAIL_SERVICES]


def _roster():
    """The people roster, or an empty one when the file is missing in tests."""
    from backend.people import load as load_people
    from . import REGISTRY
    try:
        return load_people(load_yaml(REGISTRY / "people.yaml", "registry/people.yaml"))
    except Exception:
        return load_people({})


def inbox_bot_for(mailbox, roster=None):
    """The inbox bot that handles this mailbox: its person's `inbox_bot`, else the nearest manager's."""
    from backend import people as P
    roster = roster if roster is not None else _roster()
    who, seen = P.person_by_email(mailbox, roster), set()
    while who and who["id"] not in seen:
        if who.get("inbox_bot"):
            return who["inbox_bot"]
        seen.add(who["id"])
        who = P.person(who.get("reports_to"), roster)
    return None


def bot_repo(slug):
    """The bot's repository directory name: employees.yaml's `repo`, else the folder on disk (bot-<slug> or emp-<slug>)."""
    from . import REGISTRY
    try:
        rows = load_yaml(REGISTRY / "employees.yaml", "registry/employees.yaml").get("employees") or []
    except Exception:
        rows = []
    row = next((r for r in rows if isinstance(r, dict) and r.get("name") == slug), None)
    return str((row or {}).get("repo") or repo_dir(PROJECTS, slug).name)


def bot_rules_file(mailbox, roster=None):
    """`rules/mail-rules.yaml` in the repository of the mailbox's inbox bot, when there is one."""
    slug = inbox_bot_for(mailbox, roster)
    if not slug:
        return None
    path = PROJECTS / bot_repo(slug) / "rules" / "mail-rules.yaml"
    return path if path.is_file() else None


def roster_mailboxes(roster=None):
    """Every address on registry/people.yaml (company-wide sync targets)."""
    roster = roster if roster is not None else _roster()
    out, seen = [], set()
    for person in roster.get("people") or []:
        addr = str((person or {}).get("email") or "").strip().lower()
        if addr and "@" in addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def _declared_gmail(manifest):
    """The gmail identities the manifest declares, in order."""
    out = []
    for e in entries(manifest):
        addr = str(e.get("identity") or "").strip().lower()
        if str(e.get("service", "")).lower() == "gmail" and "@" in addr and addr not in out:
            out.append(addr)
    return out


def org_mailboxes(slug, manifest=None, roster=None):
    """Mailboxes this employee may read because of the human org tree.

    Only a gmail entry with `org_read: true` reads its mailbox's person plus everyone who
    reports to them; being a person's `inbox_bot` alone reads that person's mailbox only
    (see holdings). Draft and send stay on the declared identity only.
    """
    from backend import people as P
    roster = roster if roster is not None else _roster()
    seen, out = set(), []
    who = P.inbox_person(slug, roster)
    m = manifest if manifest is not None else load(slug)
    declared = _declared_gmail(m)
    for e in entries(m):
        if str(e.get("service", "")).lower() != "gmail" or e.get("org_read") is not True:
            continue
        addr = str(e.get("identity") or "").strip().lower()
        person = P.person_by_email(addr, roster) or (who if who and declared and addr == declared[0] else None)
        if not person:
            continue
        for box in P.mailboxes_below(person["id"], roster):
            if box not in seen:
                seen.add(box)
                out.append(box)
    return out


def holdings(slug, manifest=None, roster=None):
    """{mailbox: {"gmail": [verbs], "google-calendar": [verbs]}} from the manifest.

    Org-tree mailboxes are added as gmail `read` only.
    """
    m = manifest if manifest is not None else load(slug)
    out = {}
    for e in entries(m):
        addr = str(e.get("identity") or "").strip().lower()
        if not addr or "@" not in addr:
            continue
        service = str(e["service"]).lower()
        verbs = [str(v).lower() for v in (e.get("can") or [])]
        out.setdefault(addr, {}).setdefault(service, [])
        for v in verbs:
            if v not in out[addr][service]:
                out[addr][service].append(v)
    from backend import people as P
    roster = roster if roster is not None else _roster()
    # An inbox bot that declares no gmail identity reads its person's own mailbox, never the reports'.
    who = P.inbox_person(slug, roster)
    own = [str(who.get("email") or "").strip().lower()] if who and not _declared_gmail(m) else []
    for addr in [a for a in own if "@" in a] + org_mailboxes(slug, m, roster):
        out.setdefault(addr, {}).setdefault("gmail", [])
        if "read" not in out[addr]["gmail"]:
            out[addr]["gmail"].append("read")
    return out


def known_mailboxes(projects=None):
    """Every gmail identity declared by any employee. Used by --all-mailboxes and by the owner."""
    root = projects or PROJECTS
    found = []
    for slug, d in bot_folders(root):
        f = manifest_file(d)
        if not f.exists():
            continue
        try:
            m = load_yaml(f, str(f))
        except Exception:
            continue
        for addr, services in holdings(slug, m).items():
            if "gmail" in services and addr not in found:
                found.append(addr)
    return found


def readable_mailboxes(slug, verb="read", manifest=None, roster=None):
    """Every mailbox this employee may use for `verb`, declared plus org-tree reads."""
    if verb in CALENDAR_ACTIONS:
        m = manifest if manifest is not None else load(slug)
        need = CALENDAR_ACTIONS[verb]
        company = roster_mailboxes(roster if roster is not None else _roster())
        explicit = []
        for e in entries(m):
            if str(e.get("service", "")).lower() != "google-calendar":
                continue
            addr = str(e.get("identity") or "").strip().lower()
            if addr and need in [str(v).lower() for v in (e.get("can") or [])] and addr not in explicit:
                explicit.append(addr)
        return list(dict.fromkeys(company + explicit))
    held = holdings(slug, manifest, roster)
    need = STATE_VERBS.get(verb, verb)
    return [addr for addr, services in held.items() if need in (services.get("gmail") or [])]


def resolve_calendar(slug, mailbox=None, verb="calendar_read", manifest=None, roster=None):
    """Resolve company calendar access independently from Gmail permissions.

    Every employee may read and schedule events on a roster mailbox. An explicit calendar entry
    remains useful for an identity outside the company roster and keeps its narrower verbs.
    """
    need = CALENDAR_ACTIONS[verb]
    m = manifest if manifest is not None else load(slug)
    roster = roster if roster is not None else _roster()
    company = roster_mailboxes(roster)
    explicit = {}
    for e in entries(m):
        if str(e.get("service", "")).lower() != "google-calendar":
            continue
        addr = str(e.get("identity") or "").strip().lower()
        if addr:
            explicit[addr] = [str(v).lower() for v in (e.get("can") or [])]

    addr = str(mailbox or m.get("default_calendar") or "").strip().lower()
    if not addr:
        preferred = []
        default_mail = str(m.get("default_mailbox") or "").strip().lower()
        if default_mail and default_mail in company:
            preferred.append(default_mail)
        preferred.extend(a for a in explicit if a not in preferred)
        for e in entries(m):
            candidate = str(e.get("identity") or "").strip().lower()
            if str(e.get("service", "")).lower() == "gmail" and candidate in company and candidate not in preferred:
                preferred.append(candidate)
        if len(preferred) == 1:
            addr = preferred[0]
        elif len(company) == 1:
            addr = company[0]
        else:
            raise Refused(
                f"{slug} may use every company calendar, so --for is required.",
                "Company calendars: " + (", ".join(company) or "none are registered"))

    if slug == OWNER or addr in company:
        return addr, list(CALENDAR_VERBS)
    verbs = explicit.get(addr)
    if verbs is None:
        raise Refused(f"{addr} is not a company calendar.",
                      "Use an address in registry/people.yaml, or declare google-calendar access explicitly.")
    if need not in verbs:
        raise Refused(f"{slug} may {', '.join(verbs) or 'nothing'} on {addr} calendar, not {need}.", ASK_OWNER)
    return addr, verbs


def resolve(slug, mailbox=None, verb="read", manifest=None, roster=None):
    """(mailbox, verbs-on-it). Refuses anything the manifest does not declare."""
    if verb in CALENDAR_ACTIONS:
        return resolve_calendar(slug, mailbox, verb, manifest, roster)
    need = STATE_VERBS.get(verb, verb)
    if slug == OWNER:
        addr = (mailbox or "").strip().lower()
        if not addr:
            boxes = known_mailboxes()
            if len(boxes) != 1:
                raise Refused(
                    f"{OWNER} owns every mailbox, so --mailbox is required.",
                    "Mailboxes declared in the manifests: " + (", ".join(boxes) or "none yet"))
            addr = boxes[0]
        return addr, list(VERBS)

    manifest = manifest if manifest is not None else load(slug)
    held = holdings(slug, manifest, roster)
    if not held:
        raise Refused(f"{slug} does not declare any mail access.",
                      f"Add a `tools:` entry with service: gmail and an identity to "
                      f"{where(slug)}. {ASK_OWNER}")
    addr = (mailbox or manifest.get("default_mailbox") or "").strip().lower()
    if not addr:
        # The default is the identity the manifest declares, never an org-tree read: an inbox
        # bot that reads its person's reports still acts as its own mailbox unless told otherwise.
        declared = []
        for e in entries(manifest):
            a = str(e.get("identity") or "").strip().lower()
            if str(e.get("service", "")).lower() == "gmail" and a in held and a not in declared:
                declared.append(a)
        boxes = declared or [a for a, s in held.items() if "gmail" in s]
        if not boxes:
            raise Refused(f"{slug} declares {', '.join(sorted(held))} for calendar only, "
                          f"not gmail.", ASK_OWNER)
        if len(boxes) > 1:
            raise Refused(f"{slug} holds {len(boxes)} mailboxes, so --mailbox is required.",
                          "It declares: " + ", ".join(sorted(boxes)))
        addr = boxes[0]
    if addr not in held:
        raise Refused(f"{slug} does not declare {addr}.",
                      "It declares: " + ", ".join(sorted(held)) + ". " + ASK_OWNER)
    verbs = held[addr].get("gmail", [])
    if not verbs:
        raise Refused(f"{slug} declares {addr} for calendar only, not gmail.", ASK_OWNER)
    read_only = any(str(e.get("service", "")).lower() == "gmail"
                    and str(e.get("identity", "")).strip().lower() == addr
                    and e.get("read_only") is True for e in entries(manifest))
    if read_only and verb != "read":
        raise Refused(f"{slug} has read-only access to {addr}; {verb} is not allowed.",
                      "Reading messages and downloading attachments are allowed; mailbox filing, drafts, and sends are not.")
    if need not in verbs:
        extra = (f" ({verb} is a state change inside the mailbox, which needs `read`)"
                 if verb in STATE_VERBS and verb != need else "")
        raise Refused(
            f"{slug} may {', '.join(verbs)} on {addr}, not {need}{extra}.",
            f"Add `{need}` to that gmail entry's `can:` in {where(slug)}. {ASK_OWNER}")
    return addr, verbs


def describe(slug, manifest=None, roster=None):
    """What `whoami` prints."""
    company_calendars = roster_mailboxes(roster if roster is not None else _roster())
    if slug == OWNER:
        boxes = known_mailboxes()
        return {"employee": OWNER, "owner": True,
                "mailboxes": [{"mailbox": b, "gmail": list(VERBS),
                               "google-calendar": list(VERBS)} for b in boxes],
                "company_calendars": {"mailboxes": company_calendars,
                                      "can": list(CALENDAR_VERBS)},
                "note": "owner: every verb on every mailbox, including ones not listed here"}
    held = holdings(slug, manifest, roster)
    org = set(org_mailboxes(slug, manifest, roster))
    return {"employee": slug, "owner": False,
            "mailboxes": [{"mailbox": a,
                           "gmail": sorted(s.get("gmail", [])),
                           "google-calendar": sorted(s.get("google-calendar", [])),
                           "org_read": a in org and "send" not in (s.get("gmail") or [])
                           and "draft" not in (s.get("gmail") or [])}
                          for a, s in sorted(held.items())],
            "company_calendars": {"mailboxes": company_calendars,
                                  "can": list(CALENDAR_VERBS)},
            "note": "" if held else "no gmail or google-calendar access declared"}


def mailbox_holders(projects=None):
    """{mailbox: [slug, ...]} - which employees declare gmail on each address."""
    root = projects or PROJECTS
    out = {}
    for slug, d in bot_folders(root):
        f = manifest_file(d)
        if not f.exists():
            continue
        try:
            m = load_yaml(f, str(f))
        except Exception:
            continue
        for addr, services in holdings(slug, m).items():
            if "gmail" in services:
                out.setdefault(addr, []).append(slug)
    return out
