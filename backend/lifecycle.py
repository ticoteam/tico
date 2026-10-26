"""Every bot goes Recruiting -> Onboarding -> Active (docs/onboarding.md, "A bot's stages").

- **Recruiting** is a seat: a role on the org chart with its job description and reporting line, and no bot, repository
  or computer slot. It is not an actor, so it holds no work: nothing is assigned to it, nothing wakes it, it has no
  routines, and work for the role stays with its manager.
- **Onboarding** is a bot that learns only: it answers and asks in chat, reads, connects tools and talks with its
  manager. It takes no tasks, runs no routines and sends nothing to anyone else; each attempt is refused with a plain
  reason. It is stored as `bot_config.onboarding_state` `needs_setup` (backend/statuses.py), the value the old "Set up"
  stage used, so an upgrade shows those bots as Onboarding without rewriting a row, and runners of every version keep
  treating them as parked. Any number of bots may be onboarding at once.
- **Active** is everything else, as before (paused, planned and archived keep their meaning).

The bot may say it is ready, with a short summary of what it learned; only a person above it on the org chart (its
manager, their managers, the owner) marks onboarding complete, which makes it Active and starts its routines
(backend/onboarding.py `onboarded`).
"""
import json
import uuid

from . import hubdb as H
from . import statuses
from .store import Problem

RECRUITING, ONBOARDING, ACTIVE = "recruiting", "onboarding", "active"
SEAT_PREFIX = "seat:"
TITLE_MAX, DESCRIPTION_MAX, SUMMARY_MAX = 120, 8000, 2000

SCHEMA = ("CREATE TABLE IF NOT EXISTS seats(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
          "reports_to TEXT NOT NULL DEFAULT '', team TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, created_by TEXT NOT NULL, "
          "updated TEXT NOT NULL, updated_by TEXT NOT NULL, closed_at TEXT, closed_by TEXT)")


# Added for recruiting a seat (backend/recruiting.py): who last wrote the job description (a person's actor, `chart` for a
# chart model's text, the CEO Bot's actor for its draft), when the CEO Bot drafted it and the task that asked it to, and
# the bot that filled the seat.
COLUMNS = (("description_by", "TEXT NOT NULL DEFAULT ''"), ("drafted_at", "TEXT"),
           ("draft_task", "TEXT NOT NULL DEFAULT ''"), ("filled_by", "TEXT NOT NULL DEFAULT ''"))
# `description_by` for a job description written before authorship was recorded. Only people and BotOps acting for them
# wrote seats then, so it is kept like a person's words and the CEO Bot never drafts over it.
EARLIER = "earlier"


def ensure(c):
    """Unversioned and idempotent, like the task trash: an older release never reads the table or its new columns."""
    c.execute(SCHEMA)
    c.execute("CREATE INDEX IF NOT EXISTS seats_open ON seats(reports_to) WHERE closed_at IS NULL")
    have = {row[1] for row in c.execute("PRAGMA table_info(seats)")}
    for name, kind in COLUMNS:
        if name not in have:
            c.execute(f"ALTER TABLE seats ADD COLUMN {name} {kind}")
    if "description_by" not in have:
        c.execute("UPDATE seats SET description_by=? WHERE TRIM(description)<>''", (EARLIER,))


# ------------------------------------------------------------------ stages
def stage(onboarding_state):
    """A bot's stage from its stored `onboarding_state`: Onboarding while it is parked, else Active."""
    return ONBOARDING if statuses.is_parked(onboarding_state or "") else ACTIVE


def onboarding_state(c, slug):
    row = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (slug,)).fetchone()
    return (row["onboarding_state"] or "") if row else ""


def is_onboarding(c, slug):
    return stage(onboarding_state(c, slug)) == ONBOARDING


def _bot_slug(actor):
    actor = str(actor or "")
    return actor[4:] if actor.startswith("bot:") else ""


def _name(c, slug):
    return (H.bot(c, slug) or {}).get("display_name") or slug


def may_talk_to(c, slug, to, conversation=None):
    """Who an onboarding bot may send to: its manager, the people above it on the org chart, and a person it answers in
    a chat that person is in with it."""
    to = str(to or "")
    manager = _manager_actor(c, slug)
    if manager and to == manager:
        return True
    if not to.startswith("human:"):
        return False
    if conversation and conversation.get("kind") == "chat" and to in (conversation.get("participants") or []):
        return True
    return _person_above(c, to[6:], slug)


def refuse_send(c, who, to, conversation=None, what="a message"):
    """An onboarding bot sends nothing to anyone but its manager, the people above it and a person it answers in chat.
    `to` is None for a send with no person or bot on the other end (an email, a post, a channel): always refused."""
    slug = _bot_slug(who.actor) if who.role == "bot" else ""
    if not slug or not is_onboarding(c, slug):
        return
    if to is not None and may_talk_to(c, slug, to, conversation):
        return
    raise Problem("onboarding", f"You are still onboarding, so you can't send {what} to anyone but your manager yet. "
                  "Answer in chat or ask your manager; a person above you marks onboarding complete when you are ready",
                  409)


def _manager_actor(c, slug):
    row = c.execute("SELECT reports_to,config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
    if not row:
        return ""
    reports = str(row["reports_to"] or (json.loads(row["config_json"] or "{}") or {}).get("reports_to") or "")
    return reports if not reports or reports.startswith("human:") else H.bot_actor(reports)


def _person_above(c, pid, slug):
    from . import people as P, views
    archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
    return P.manages(pid, "bot", slug, views.roster(c), views.entries(c), archived)


def may_complete(auth, c, who, slug):
    """Whether this caller may mark the bot's onboarding complete: the owner, or a person above it on the org chart.
    Not the bot itself, another bot, a co-owner or an admin who is not above it."""
    return (who.role in ("owner", "human") and (not who.via or who.confirmed or who.via == "botops")
            and auth.manages(c, who, "bot", slug))


def ready(c, who, slug, summary):
    """The bot's own "I'm ready", with a short summary of what it learned: recorded on the bot and told to its manager.
    It stays Onboarding until a person above it marks onboarding complete."""
    if who.role != "bot" or H.actor_id(who.actor) != slug:
        raise Problem("forbidden", "Only the bot itself says it is ready", 403)
    if not is_onboarding(c, slug):
        raise Problem("onboarding", "This bot is not onboarding", 409)
    summary = str(summary or "").strip()[:SUMMARY_MAX]
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
    declared = json.loads(row["config_json"] or "{}") if row else {}
    declared["onboarding_ready"] = {"at": H.now(), "summary": summary}
    c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (json.dumps(declared), slug))
    H.event(c, who.actor, "bot.onboarding_ready", slug, {"summary": summary})
    return {"bot": slug, "stage": ONBOARDING, "ready": declared["onboarding_ready"]}


def ready_of(config_json):
    declared = json.loads(config_json or "{}") if isinstance(config_json, str) else (config_json or {})
    found = declared.get("onboarding_ready")
    return found if isinstance(found, dict) else None


# ------------------------------------------------------------------ seats
def _seat(row):
    return {"id": row["id"], "kind": "seat", "stage": RECRUITING, "title": row["title"],
            "description": row["description"], "reports_to": row["reports_to"], "team": row["team"],
            "created": row["created"], "created_by": row["created_by"], "updated": row["updated"]}


def seats(c):
    return [_seat(r) for r in c.execute("SELECT * FROM seats WHERE closed_at IS NULL ORDER BY created,id")]


def seat(c, seat_id):
    row = c.execute("SELECT * FROM seats WHERE id=? AND closed_at IS NULL", (seat_id,)).fetchone()
    if not row:
        raise Problem("not_found", "Seat not found", 404)
    return row


def _reports_to(c, value, seat_id=""):
    """A seat reports to a person (`human:<id>`), a bot that is not archived, or another open seat (`seat:<id>`: a chart
    laid out from setup has managers that are still Recruiting); never to nobody, itself or a seat under it."""
    value = str(value or "").strip()
    if value.startswith(SEAT_PREFIX):
        parent = value[len(SEAT_PREFIX):]
        seat(c, parent)
        if seat_id and seat_id in _seat_chain(c, parent):
            raise Problem("hierarchy", "A seat cannot report to itself or to a seat under it", 422)
        return value
    if value.startswith("human:"):
        if not H.human(c, value[6:]):
            raise Problem("not_found", "Reports-to person was not found", 404)
        return value
    slug = _bot_slug(value) or value
    bot = H.bot(c, slug) if slug else None
    if not bot or bot.get("state") == "archived":
        raise Problem("not_found", "Reports-to bot was not found", 404)
    return slug


def _seat_chain(c, seat_id):
    """This seat and the seats above it, nearest first. Closed seats count: the chart hangs a seat under a closed
    seat's own manager (`org_parent`), so a line through one can still close a circle. A missing seat ends it."""
    chain = []
    while seat_id and seat_id not in chain:
        row = c.execute("SELECT reports_to FROM seats WHERE id=?", (seat_id,)).fetchone()
        if not row:
            break
        chain.append(seat_id)
        reports = str(row["reports_to"] or "")
        seat_id = reports[len(SEAT_PREFIX):] if reports.startswith(SEAT_PREFIX) else ""
    return chain


def _anchor(c, reports_to):
    """The person or bot a seat answers to in the end: up through any seats above it. A line that goes round answers
    to nobody, so only the owner may place under it."""
    seen = set()
    while reports_to.startswith(SEAT_PREFIX) and reports_to not in seen:
        seen.add(reports_to)
        row = c.execute("SELECT reports_to FROM seats WHERE id=?", (reports_to[len(SEAT_PREFIX):],)).fetchone()
        reports_to = str(row["reports_to"] or "") if row else ""
    return "" if reports_to.startswith(SEAT_PREFIX) else reports_to


def _may_place(auth, c, who, reports_to):
    """The owner places a seat anywhere; a person places one under themselves or anyone below them, a seat under a seat
    counting as under the person or bot that seat answers to."""
    if who.role == "owner":
        return
    reports_to = _anchor(c, reports_to)
    # BotOps acting on the person's own request counts as them (its lent identity is never confirmed: only their tap
    # on a card is); the Assistant's own turn does not.
    if who.role == "human" and (not who.via or who.confirmed or who.via == "botops"):
        if reports_to == who.actor:
            return
        if reports_to.startswith("human:") and auth.manages(c, who, "person", reports_to[6:]):
            return
        if not reports_to.startswith("human:") and auth.manages(c, who, "bot", reports_to):
            return
    raise Problem("forbidden", "You may add or change seats only under yourself or someone below you", 403)


def _clean_title(value):
    title = " ".join(str(value or "").split())[:TITLE_MAX]
    if not title:
        raise Problem("title", "A seat needs a role title", 422)
    return title


def create_seat(auth, c, who, title, description="", reports_to="", team="", description_by=None):
    """`description_by` says whose words the job description is: the caller's by default when they wrote one, `chart`
    for a chart model's text. Only a description no person wrote is replaced by the CEO Bot's draft (recruiting.py)."""
    reports_to = _reports_to(c, reports_to or who.actor)
    _may_place(auth, c, who, reports_to)
    return _seat(seat(c, _insert(c, who.actor, title, description, reports_to, team, description_by)))


def _insert(c, actor, title, description, reports_to, team, description_by=None):
    now, seat_id = H.now(), uuid.uuid4().hex
    description = str(description or "")[:DESCRIPTION_MAX]
    by = (actor if description.strip() else "") if description_by is None else str(description_by)
    c.execute("INSERT INTO seats(id,title,description,reports_to,team,created,created_by,updated,updated_by,description_by) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)", (seat_id, _clean_title(title), description, reports_to,
                                              str(team or "").strip(), now, actor, now, actor, by))
    H.event(c, actor, "seat.created", seat_id, {"reports_to": reports_to})
    return seat_id


def lay_out(c, actor, roles):
    """Setup's chart, laid out exactly (docs/onboarding.md, "Setup picks the chart"): every role a Recruiting seat with
    its reporting line, and no bot, repository, computer slot or BotOps task for any of them. Only setup calls this, as
    the owner, so no placement rule applies.

    `roles` is {key: {title, description, reports_to, team}}; a `reports_to` naming another key reports to that seat,
    anything else is a person (`human:<id>`) or a bot. Managers are laid out before their reports, so every line holds
    when it is written. Returns {key: seat id}."""
    made = {}

    def place(key, path=()):
        if key in made:
            return made[key]
        if key in path:
            raise Problem("hierarchy", "The chart's reporting lines go round in a circle", 422)
        role = roles[key]
        parent = str(role.get("reports_to") or "")
        reports_to = (SEAT_PREFIX + place(parent, (*path, key))) if parent in roles else _reports_to(c, parent)
        made[key] = _insert(c, actor, role["title"], role.get("description") or "", reports_to,
                            role.get("team") or "", role.get("description_by", "chart"))
        return made[key]

    for key in roles:
        place(key)
    return made


def create_seats(auth, c, who, seats):
    """Add one confirmed group of Recruiting seats; the caller's chain rule applies to every seat."""
    if not seats or len(seats) > 50:
        raise Problem("seats", "Add between 1 and 50 seats at a time", 422)
    return {"seats": [create_seat(auth, c, who, item.title, item.description, item.reports_to, item.team)
                       for item in seats]}


def update_seat(auth, c, who, seat_id, fields):
    row = seat(c, seat_id)
    _may_place(auth, c, who, row["reports_to"])
    values = {}
    if fields.get("title") is not None:
        values["title"] = _clean_title(fields["title"])
    if fields.get("description") is not None:
        values["description"] = str(fields["description"])[:DESCRIPTION_MAX]
        values["description_by"] = who.actor          # a person's words now: the CEO Bot's draft never replaces them
    if fields.get("team") is not None:
        values["team"] = str(fields["team"]).strip()
    if fields.get("reports_to") is not None:
        values["reports_to"] = _reports_to(c, fields["reports_to"], seat_id)
        _may_place(auth, c, who, values["reports_to"])
    if values:
        values.update(updated=H.now(), updated_by=who.actor)
        c.execute("UPDATE seats SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", (*values.values(), seat_id))
        if "description" in values:
            from .recruiting import end_draft
            end_draft(c, row, "Someone wrote the job description themselves, so theirs stays")
        H.event(c, who.actor, "seat.updated", seat_id,
                {"fields": sorted(k for k in values if k not in ("updated", "updated_by", "description_by"))})
    return _seat(seat(c, seat_id))


def close_seat(auth, c, who, seat_id):
    row = seat(c, seat_id)
    _may_place(auth, c, who, row["reports_to"])
    c.execute("UPDATE seats SET closed_at=?,closed_by=? WHERE id=?", (H.now(), who.actor, seat_id))
    from .recruiting import end_draft
    end_draft(c, row, "The seat was taken off the org chart")
    H.event(c, who.actor, "seat.closed", seat_id, {})
    return {"id": seat_id, "closed": True}


def org_parent(seat_row, roster, employees, archived=(), all_seats=None):
    """Where a seat hangs on the chart: under the open seat it reports to (`s:<id>`), else under its bot while that bot
    is on the chart, else under its person, as a bot reporting there would (backend/people.py `org_parent`). A closed
    seat above it is passed over for whatever that seat answered to. `all_seats` is {id: row}, closed seats too."""
    from . import people as P
    all_seats = all_seats or {}
    reports, seen = str(seat_row["reports_to"] or ""), {seat_row["id"]}
    while reports.startswith(SEAT_PREFIX):
        parent = all_seats.get(reports[len(SEAT_PREFIX):])
        if not parent or parent["id"] in seen:
            reports = ""
            break
        if not parent["closed_at"]:
            return "s:" + parent["id"]
        seen.add(parent["id"])
        reports = str(parent["reports_to"] or "")
    probe = "seat:" + seat_row["id"]
    return P.org_parent("bot", probe, roster, {**(employees or {}), probe: {"reports_to": reports,
                                                                           "team": seat_row["team"] or ""}}, archived)


def all_seats(c):
    return {row["id"]: row for row in c.execute("SELECT * FROM seats")}


def chart_seats(c, roster, employees, archived=(), may_see=lambda slug: True):
    """The open seats as the sidebar's team chart draws them (GET /api/humans): each with its title, job description and
    where it hangs. One under a bot the caller may not see hangs from the nearest thing above that bot they may, as
    that bot's own reports do on the chart."""
    from . import people as P
    every, out = all_seats(c), []
    for row in every.values():
        if row["closed_at"]:
            continue
        parent, seen = org_parent(row, roster, employees, archived, every), set()
        while parent.startswith("b:") and parent not in seen and not may_see(parent[2:]):
            seen.add(parent)
            parent = P.org_parent("bot", parent[2:], roster, employees, archived)
        out.append({"id": row["id"], "kind": "seat", "stage": RECRUITING, "title": row["title"],
                    "description": row["description"], "team": row["team"], "reports_to": row["reports_to"],
                    "created": row["created"], "org_parent": parent})
    return sorted(out, key=lambda seat: (seat["created"], seat["id"]))
