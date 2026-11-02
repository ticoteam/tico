"""Who an edition is for. Board, Investors and Public are built in and start disabled; an owner adds any others.

A due rule is {"kind": "business_day", "n": 5} (the nth Monday-to-Friday after period end) or
{"kind": "days_after_period_end", "n": 30}. schedule.py reads them."""
import json

from .. import hubdb as H
from ..store import Problem
from . import settings as S

CADENCES = ("monthly", "quarterly")
DUE_KINDS = {"business_day": 23, "days_after_period_end": 365}     # the most of each
COMPANY_AUTHOR = "company"       # an audience_sources author: the company's own update, written by no one bot or person
# id, name, due rule and lag, from the plan's timeline table.
BUILT_IN = {"board": ("Board", {"kind": "business_day", "n": 5}, 0),
            "investors": ("Investors", {"kind": "business_day", "n": 10}, 0),
            "public": ("Public", {"kind": "days_after_period_end", "n": 30}, 30)}
CUSTOM_DUE = {"kind": "business_day", "n": 10}
LIST_MAX = 200
NAME_MAX = 80


def seed(c):
    """The built-ins, disabled. Safe on every call: a row an owner changed stays as they left it."""
    ts = H.now()
    for kind, (name, due, lag) in BUILT_IN.items():
        c.execute("INSERT OR IGNORE INTO audiences (id, name, kind, cadence, due_rule, lag_days, enabled, created_at, "
                  "updated_at) VALUES (?,?,?,?,?,?,0,?,?)", (kind, name, kind, "monthly", json.dumps(due), lag, ts, ts))


def _view(row):
    try:
        due = json.loads(row.get("due_rule") or "{}")
    except ValueError:
        due = {}
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "cadence": row["cadence"], "due_rule": due,
            "lag_days": row["lag_days"], "approver": row.get("approver"), "enabled": bool(row["enabled"]),
            "built_in": row["kind"] != "custom", "created_at": row["created_at"], "updated_at": row["updated_at"]}


def list_all(c, limit=LIST_MAX, offset=0):
    return [_view(r) for r in H._rows(c.execute(
        "SELECT * FROM audiences ORDER BY kind='custom', created_at, id LIMIT ? OFFSET ?", (limit, offset)))]


def get(c, audience_id):
    row = H._one(c, "SELECT * FROM audiences WHERE id=?", (audience_id,))
    return _view(row) if row else None


def need(c, audience_id):
    found = get(c, audience_id)
    if not found:
        raise Problem("not_found", "No such audience", 404)
    return found


def _name(value):
    name = " ".join(str(value or "").split())
    if not name or len(name) > NAME_MAX:
        raise Problem("name", f"Name the audience, in {NAME_MAX} characters or fewer", 422)
    return name


def _due(value):
    kind, n = (value.get("kind"), value.get("n")) if isinstance(value, dict) else (None, None)
    if kind not in DUE_KINDS or type(n) is not int or not 1 <= n <= DUE_KINDS[kind] or len(value) != 2:
        raise Problem("due_rule", "A due rule is a business day after period end, or a number of days after it", 422)
    return json.dumps({"kind": kind, "n": n})


def _approver(c, value):
    """A person on the roster. An approval here is that person's own click, so a bot can never be the one to ask."""
    if value in (None, ""):
        return None
    actor = str(value)
    if not H.is_human(actor) or not H.human(c, H.actor_id(actor)):
        raise Problem("approver", "The approver is a person on the team", 422)
    return actor


def _clean(c, fields):
    out = {}
    for name, value in fields.items():
        if name == "name":
            out[name] = _name(value)
        elif name == "cadence":
            if value not in CADENCES:
                raise Problem("cadence", f"Cadence is {', '.join(CADENCES)}", 422)
            out[name] = value
        elif name == "due_rule":
            out[name] = _due(value)
        elif name == "lag_days":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 365:
                raise Problem("lag_days", "Lag is 0 to 365 days", 422)
            out[name] = value
        elif name == "approver":
            out[name] = _approver(c, value)
        elif name == "enabled":
            out[name] = 1 if value else 0
        else:
            raise Problem("fields", f"Unknown: {name}", 422)
    return out


def create(c, actor, name, **fields):
    S.require_person(c, actor)
    values = _clean(c, {"name": name, **fields})
    ts, audience_id = H.now(), H.new_id()
    row = {"cadence": "monthly", "due_rule": json.dumps(CUSTOM_DUE), "lag_days": 0, "approver": None, "enabled": 0, **values}
    c.execute("INSERT INTO audiences (id, name, kind, cadence, due_rule, lag_days, approver, enabled, created_at, "
              "updated_at) VALUES (?,?,'custom',?,?,?,?,?,?,?)",
              (audience_id, row["name"], row["cadence"], row["due_rule"], row["lag_days"], row["approver"],
               row["enabled"], ts, ts))
    H.event(c, actor, "company_page.audience_create", audience_id, {"name": row["name"]})
    return get(c, audience_id)


def update(c, actor, audience_id, **fields):
    S.require_person(c, actor)
    before = need(c, audience_id)
    values = _clean(c, fields)
    if values:
        names = list(values)
        c.execute(f"UPDATE audiences SET {', '.join(f'{n}=?' for n in names)}, updated_at=? WHERE id=?",
                  (*values.values(), H.now(), audience_id))
        H.event(c, actor, "company_page.audience_update", audience_id, {"fields": sorted(names)})
    after = get(c, audience_id)
    # Turning Public off is a way out: it stops being served in this step. Private audiences are checked per request.
    if before["kind"] == "public" and after["enabled"] != before["enabled"]:
        S.rebuild_if_live(c, "public audience " + ("enabled" if after["enabled"] else "disabled"))
    return after


def delete(c, actor, audience_id):
    S.require_person(c, actor)
    found = need(c, audience_id)
    if found["built_in"]:
        raise Problem("built_in", f"{found['name']} is built in. Disable it instead", 409)
    # Editions outlive an audience in their own history, and a published one must be retracted, not orphaned.
    if c.execute("SELECT 1 FROM editions WHERE audience_id=? LIMIT 1", (audience_id,)).fetchone():
        raise Problem("has_editions", "This audience has editions. Disable it instead", 409)
    c.execute("DELETE FROM audiences WHERE id=?", (audience_id,))
    H.event(c, actor, "company_page.audience_delete", audience_id, {"name": found["name"]})
    return {"deleted": audience_id}


def sources(c, audience_id, limit=LIST_MAX, offset=0):
    return H._rows(c.execute("SELECT id, audience_id, author, created_at FROM audience_sources WHERE audience_id=? "
                             "ORDER BY created_at, id LIMIT ? OFFSET ?", (audience_id, limit, offset)))


def _author(c, value):
    """The form the updates table uses: a bot's slug or `human:<id>`, or `company`. A source that is a bot appears on
    the page under its name; the bot itself stays out of reach."""
    author = str(value or "").strip()
    if author == COMPANY_AUTHOR:
        return author
    if H.is_human(author):
        if H.human(c, H.actor_id(author)):
            return author
    else:
        slug = H.actor_id(author) if H.is_bot(author) else author
        if slug and H.bot(c, slug):
            return slug
    raise Problem("author", "A source is the company, a bot or a person on the team", 422)


def add_source(c, actor, audience_id, author):
    S.require_person(c, actor)
    need(c, audience_id)
    who = _author(c, author)
    source_id = H.new_id()
    c.execute("INSERT INTO audience_sources (id, audience_id, author, created_at) VALUES (?,?,?,?) "
              "ON CONFLICT(audience_id, author) DO NOTHING", (source_id, audience_id, who, H.now()))
    H.event(c, actor, "company_page.source_add", audience_id, {"author": who})
    return H._one(c, "SELECT id, audience_id, author, created_at FROM audience_sources WHERE audience_id=? AND author=?",
                  (audience_id, who))


def remove_source(c, actor, audience_id, source_id):
    S.require_person(c, actor)
    need(c, audience_id)
    gone = c.execute("DELETE FROM audience_sources WHERE id=? AND audience_id=?", (source_id, audience_id)).rowcount
    if not gone:
        raise Problem("not_found", "No such source", 404)
    H.event(c, actor, "company_page.source_remove", audience_id, {"source": source_id})
    return {"deleted": source_id}


def members(c, audience_id, limit=LIST_MAX, offset=0):
    """Current readers only: a removed reader is in no audience."""
    return H._rows(c.execute(
        "SELECT r.id, r.email, r.name FROM audience_readers m JOIN readers r ON r.id=m.reader_id "
        "WHERE m.audience_id=? AND r.removed_at IS NULL ORDER BY lower(r.email), r.id LIMIT ? OFFSET ?",
        (audience_id, limit, offset)))
