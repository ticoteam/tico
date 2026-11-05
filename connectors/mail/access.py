"""Who may act as which mailbox, and with which verbs: Tico's answer, nothing the run says itself.

Every mail command asks the Tico server (GET /api/v2/mail/access, backend/mailbox_access.py), as whoever this run's
credential is: a bot's turn token, a person's own token (their Assistant is them). A level is Read or Read and write
(the server gives Read only for an address outside the company's domains); every write is also held by the company's
What Tico may do (oauth.company_allows). Read gives `read`; Read and write gives:
  - `read`: read messages and download attachments, and
  - `organize`: label, archive, mark read, star, triage, run mail rules, unsubscribe;
  - `draft`, `send`: write a draft, queue one (Tico's sending policy decides every send, connectors/mail/policy.py);
  - `schedule`: change calendar events.
No handle holds every verb: the owner has no bypass. `bot.yaml` (`tools:` gmail entries) can only narrow a bot's answer:
`read_only: true` keeps it to reading, and `draft`/`send` count only when its `can:` lists them.

The runner's own connector job runs as `--as connector`, over the computer's registration: read on the mailboxes this
computer may act for (GET /api/v2/connectors/mail/targets), and writes only for an action the server leased it
(`mail-action`, `connector-event` set LEASED), which the server checked against the automation owner's level.
A company calendar is readable by every run (free/busy stays company-wide).
"""

import os

from . import PROJECTS, Refused, bot_folders, declared_tools, hub_bot, load_yaml, manifest_file, owner_handle, repo_dir

OWNER = owner_handle()                    # the owner's roster handle: approvals name it; it holds no mail rights
CONNECTOR = "connector"                   # the runner's connector job (runner_support/connectors.py)
MAIL_SERVICES = ("gmail", "google-calendar")
VERBS = ("read", "organize", "draft", "send", "unsubscribe", "schedule")
CALENDAR_VERBS = ("read", "schedule")
CALENDAR_ACTIONS = {"calendar_read": "read", "calendar_schedule": "schedule"}
# Commands that only move a message around inside the mailbox: they need `organize`.
STATE_VERBS = {"label": "organize", "archive": "organize", "mark_read": "organize", "star": "organize",
               "triaged": "organize", "rules": "organize"}
ASK = ("Who may use a mailbox is set in Tico: Settings > Inboxes > Who can use it (its person, or an admin "
       "for a shared inbox, decides).")

# Set by the commands that carry out what the server leased this computer (mail-action, connector-event): only then
# does the connector principal write.
LEASED = False
# Test seams: SERVER_ACCESS(slug) -> the /mail/access answer, RUNNER_ACCESS() -> the /connectors/mail/targets answer.
SERVER_ACCESS = None
RUNNER_ACCESS = None
_ANSWERS = {}


def manifest_path(slug):
    return manifest_file(repo_dir(PROJECTS, slug))


def where(slug):
    """`<repo folder>/<manifest>` as a message names it: bot-<slug>/bot.yaml, or emp-<slug>/employee.yaml for an older bot."""
    p = manifest_path(slug)
    return f"{p.parent.name}/{p.name}"


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
    if slug in (OWNER, CONNECTOR):
        return {"name": slug, "access": []}
    return load_yaml(manifest_path(slug), where(slug))


def _manifest(slug, manifest=None):
    """The bot's manifest when this computer has its repository, else nothing (a person's handle, a fresh computer)."""
    if manifest is not None:
        return manifest
    try:
        return load(slug)
    except Refused:
        return {}


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


def holdings(slug, manifest=None, roster=None):
    """{mailbox: {"gmail": [verbs], "google-calendar": [verbs]}} as the manifest declares them (no rights of its own:
    the doctor lists them)."""
    m = manifest if manifest is not None else load(slug)
    out = {}
    for e in entries(m):
        addr = str(e.get("identity") or "").strip().lower()
        if not addr or "@" not in addr:
            continue
        service = str(e["service"]).lower()
        out.setdefault(addr, {}).setdefault(service, [])
        for v in [str(v).lower() for v in (e.get("can") or [])]:
            if v not in out[addr][service]:
                out[addr][service].append(v)
    return out


# ---------------------------------------------------------------- the server's answer

def forget():
    """Drop the answers read so far (tests; a long-running caller)."""
    _ANSWERS.clear()


def _connector_answer():
    if RUNNER_ACCESS is None and os.environ.get("HUB_TOKEN"):
        # The connector job runs without a Tico token (runner_support/connectors.py mail_cli_env); a turn has one.
        raise Refused("--as connector is the runner's connector job, not a turn.",
                      "A turn asks Tico as itself: drop --as connector.")
    if RUNNER_ACCESS is not None:
        targets = RUNNER_ACCESS()
    else:
        from . import oauth                             # noqa: PLC0415
        try:
            targets = oauth.broker().get("connectors/mail/targets")
        except Exception as e:                          # noqa: BLE001 - any failure is "Tico could not be asked"
            raise Refused("this connector run could not ask Tico which mailboxes this computer may act for.",
                          f"{e}. The runner's connector job runs it with the computer's registration.") from None
    if not isinstance(targets, dict):
        raise Refused("Tico gave this connector run no list of mailboxes.")
    level = "read_write" if LEASED else "read"
    boxes = []
    for target in targets.get("mailboxes") or []:
        addr = str((target or {}).get("address") or "").strip().lower()
        if "@" in addr:
            boxes.append({"address": addr, "level": level, "person_id": target.get("person_id")})
    return {"principal": CONNECTOR, "mailboxes": boxes, "company": targets.get("company")}


def _hub_answer():
    url, token = os.environ.get("HUB_API_URL"), os.environ.get("HUB_TOKEN")
    if not url or not token:
        raise Refused("this run cannot ask Tico which mailboxes it may use: no Tico credential.",
                      "A bot's turn has one; a person runs mail with their own Tico token "
                      "(HUB_API_URL and HUB_TOKEN). There is no owner bypass.")
    from clients.tico import Client                     # noqa: PLC0415
    try:
        return Client(url, token).get("mail/access")
    except Exception as e:                              # noqa: BLE001 - any failure is "Tico could not be asked"
        raise Refused("this run could not ask Tico which mailboxes it may use.",
                      f"{e}. Try again once Tico answers.") from None


def answer(slug):
    """Tico's answer for this run: {principal, mailboxes: [{address, level, may}], company}."""
    if slug == CONNECTOR:
        return _connector_answer()                      # read every time: LEASED may change within one process
    if SERVER_ACCESS is not None:
        got = SERVER_ACCESS(slug)
    else:
        if slug not in _ANSWERS:
            _ANSWERS[slug] = _hub_answer()
        got = _ANSWERS[slug]
    if not isinstance(got, dict):
        raise Refused("Tico gave no answer about this run's mailboxes.")
    return got


def _narrowed(slug, addr, level, manifest=None):
    """The verbs a level gives on `addr`, narrowed by the bot's own gmail entry for `addr` when it has one. The company's
    What Tico may do is held on every write itself (oauth.company_allows), with its own words."""
    verbs = list(VERBS) if level == "read_write" else ["read"]
    entry = next((e for e in entries(_manifest(slug, manifest))
                  if str(e.get("service", "")).lower() == "gmail"
                  and str(e.get("identity") or "").strip().lower() == addr), None)
    if entry is None:
        return verbs
    if entry.get("read_only") is True:
        return [v for v in verbs if v == "read"]
    can = {str(v).lower() for v in (entry.get("can") or [])}
    return [v for v in verbs if v not in ("draft", "send") or v in can]


def levels(slug, manifest=None):
    """{mailbox: {"level", "verbs"}} for every mailbox this run may use."""
    out = {}
    for box in answer(slug).get("mailboxes") or []:
        addr = str(box.get("address") or "").strip().lower()
        level = box.get("level")
        if "@" in addr and level in ("read", "read_write"):
            out[addr] = {"level": level, "verbs": _narrowed(slug, addr, level, manifest)}
    return out


def readable_mailboxes(slug, verb="read", manifest=None, roster=None):
    """Every mailbox this run may use for `verb`."""
    if verb in CALENDAR_ACTIONS:
        if CALENDAR_ACTIONS[verb] == "read":
            return roster_mailboxes(roster if roster is not None else _roster())
        verb = "schedule"
    need = STATE_VERBS.get(verb, verb)
    return [addr for addr, held in levels(slug, manifest).items() if need in held["verbs"]]


def resolve_calendar(slug, mailbox=None, verb="calendar_read", manifest=None, roster=None):
    """A calendar to read (any company calendar, or one the bot declares outside the roster) or to change (one this
    run may change: Read and write on its mailbox and the company's Change calendar)."""
    need = CALENDAR_ACTIONS[verb]
    m = _manifest(slug, manifest)
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
                f"{slug} may read every company calendar, so --for is required.",
                "Company calendars: " + (", ".join(company) or "none are registered"))
    if addr not in company and addr not in explicit:
        raise Refused(f"{addr} is not a company calendar.",
                      "Use an address in registry/people.yaml, or declare google-calendar access explicitly.")
    verbs = ["read"]
    if need == "schedule" and "schedule" in (levels(slug, m).get(addr) or {}).get("verbs", []):
        verbs.append("schedule")
    if need not in verbs:
        raise Refused(f"{slug} may read {addr}'s calendar, not change it: that needs Read and write on {addr} "
                      "and the company's Change calendar.", ASK)
    return addr, verbs


def resolve(slug, mailbox=None, verb="read", manifest=None, roster=None):
    """(mailbox, verbs-on-it), as Tico answers for this run. Refuses a mailbox it holds nothing on, and a verb its level
    or the company's What Tico may do does not allow."""
    if verb in CALENDAR_ACTIONS:
        return resolve_calendar(slug, mailbox, verb, manifest, roster)
    need = STATE_VERBS.get(verb, verb)
    m = _manifest(slug, manifest)
    held = levels(slug, m)
    if not held:
        raise Refused(f"{slug} may not use any mailbox.", ASK)
    addr = (mailbox or m.get("default_mailbox") or "").strip().lower()
    if not addr:
        declared = [a for a in _declared_gmail(m) if a in held]
        boxes = declared or sorted(held)
        if len(boxes) > 1:
            raise Refused(f"{slug} may use {len(boxes)} mailboxes, so --mailbox is required.",
                          "It may use: " + ", ".join(sorted(boxes)))
        addr = boxes[0]
    if addr not in held:
        raise Refused(f"{slug} may not use {addr}.", "It may use: " + ", ".join(sorted(held)) + ". " + ASK)
    verbs = held[addr]["verbs"]
    if need not in verbs:
        level = "Read" if held[addr]["level"] == "read" else "Read and write"
        raise Refused(f"{slug} may {', '.join(verbs) or 'nothing'} on {addr} ({level}), not {need}.",
                      "Organizing, drafts and sends need Read and write on the mailbox and the company's What Tico "
                      "may do (Settings > Inboxes > Settings). " + ASK)
    return addr, verbs


def describe(slug, manifest=None, roster=None):
    """What `whoami` prints."""
    company_calendars = roster_mailboxes(roster if roster is not None else _roster())
    held = levels(slug, manifest)
    return {"employee": slug, "owner": False, "connector": slug == CONNECTOR,
            "mailboxes": [{"mailbox": a, "level": h["level"], "gmail": [v for v in h["verbs"] if v != "schedule"],
                           "google-calendar": ["read"] + (["schedule"] if "schedule" in h["verbs"] else [])}
                          for a, h in sorted(held.items())],
            "company_calendars": {"mailboxes": company_calendars, "can": ["read"]},
            "note": "" if held else "no mailbox in Tico: " + ASK}


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
