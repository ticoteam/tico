"""The company itself, at the top of the Goals page: its Vision and Description, and each goal's "Why" line
(docs/goals-and-kpis.md, "The company"; spec: Head of Product, Strategy Bot). Shown only where the Strategy Bot switch
is on (backend/strategy_bot.py).

- Vision (one or two sentences: where the company is going) and Description (a short paragraph: what it does, for whom)
  are one row, `company_profile`. Every change leaves a `company_events` row, as `goal_events` does for a goal, so the
  history says who changed which and when. Only a person who is the owner or an admin writes them, on their own click:
  never a bot, the Strategy Bot included, and never the Assistant acting for someone (backend/goal_routes.py).
- A goal's Why is one line tying it to the vision (`goals.why`, with who set it and when). The Strategy Bot proposes one
  (`goal_proposals` kind `goal_why`); a person who may edit the goal accepts it as it is or edited, or writes one
  themselves. Either way it is a `goal_events` row. A goal with no Why is "Not tied to the vision", which is not an
  error. Writing a Why settles the goal's other pending Why proposals, so the Strategy Bot's next ask is about what is
  still open.

Unversioned and idempotent (`ensure`), like the task trash: an older release never reads the tables or the columns."""
from . import hubdb as H

VISION_MAX = 400          # one or two sentences
DESCRIPTION_MAX = 2000    # a short paragraph
WHY_MAX = 200             # one line
FIELDS = ("vision", "description")

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS company_profile (id TEXT PRIMARY KEY CHECK (id = 'company'), "
    "vision TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', updated_at TEXT, updated_by TEXT)",
    "CREATE TABLE IF NOT EXISTS company_events (id TEXT PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL, "
    "field TEXT NOT NULL, old TEXT NOT NULL DEFAULT '', new TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS company_events_ts ON company_events(ts)",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)
    H.add_column(c, "goals", "why", "TEXT NOT NULL DEFAULT ''")
    H.add_column(c, "goals", "why_by", "TEXT")
    H.add_column(c, "goals", "why_at", "TEXT")


def _clean(value, limit, what, conn, actor, one_line=False):
    text = " ".join(str(value or "").split()) if one_line else str(value or "").strip()
    if len(text) > limit:
        H.refuse(conn, actor, "lint", f"keep the {what} under {limit} characters")
    return text


# ----------------------------------------------------------------------------- the company
def profile(c):
    row = H._one(c, "SELECT * FROM company_profile WHERE id='company'")
    return {"vision": (row or {}).get("vision") or "", "description": (row or {}).get("description") or "",
            "updated_at": (row or {}).get("updated_at"), "updated_by": (row or {}).get("updated_by")}


def update(c, actor, vision=None, description=None):
    """Save what changed; each changed field is one history row. The same words again change nothing."""
    H._writer(c, actor)
    before = profile(c)
    new = {"vision": None if vision is None else _clean(vision, VISION_MAX, "vision", c, actor, one_line=True),
           "description": None if description is None else _clean(description, DESCRIPTION_MAX, "description", c, actor)}
    changed = {k: v for k, v in new.items() if v is not None and v != before[k]}
    if not changed:
        return before
    ts = H.now()
    after = {**before, **changed}
    c.execute("INSERT INTO company_profile (id, vision, description, updated_at, updated_by) VALUES ('company',?,?,?,?) "
              "ON CONFLICT(id) DO UPDATE SET vision=excluded.vision, description=excluded.description, "
              "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
              (after["vision"], after["description"], ts, actor))
    for field in FIELDS:
        if field in changed:
            c.execute("INSERT INTO company_events (id, ts, actor, field, old, new) VALUES (?,?,?,?,?,?)",
                      (H.new_id(), ts, actor, field, before[field], changed[field]))
    H.event(c, actor, "company.update", "company", {"fields": sorted(changed)})
    return profile(c)


def history(c, limit=200):
    """Every change to the vision and the description, newest first."""
    return H._rows(c.execute("SELECT * FROM company_events ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)))


# ----------------------------------------------------------------------------- a goal's Why
def why_payload_error(payload):
    why = " ".join(str((payload or {}).get("why") or "").split())
    if not why:
        return "propose the Why: one line tying the goal to the company's vision"
    if len(why) > WHY_MAX:
        return f"keep the Why to one line, under {WHY_MAX} characters"
    return None


def set_why(c, actor, goal_id, why, settled_by=None):
    """Set (or, with an empty line, clear) a goal's Why, as the person who accepted or wrote it. The goal's other
    pending Why proposals are settled: rejected, saying the Why was set. `settled_by` is the proposal being accepted,
    which `goals.decide` marks itself."""
    from . import goals as G
    H._writer(c, actor)
    row = G.goal(c, goal_id)
    if not row:
        H.refuse(c, actor, "not-found", f"no goal {goal_id}")
    why = _clean(why, WHY_MAX, "Why", c, actor, one_line=True)
    ts = H.now()
    if why != (row.get("why") or ""):
        c.execute("UPDATE goals SET why=?, why_by=?, why_at=?, updated_at=? WHERE id=?",
                  (why, actor if why else None, ts if why else None, ts, goal_id))
        G._event(c, goal_id, actor, "why", row.get("why") or "", why)
        H.event(c, actor, "goal.why", goal_id, {"cleared": not why})
    for p in G.proposals(c, "pending", goal_id=goal_id) if why else ():
        if p["kind"] == "goal_why" and p["id"] != settled_by:
            c.execute("UPDATE goal_proposals SET status='rejected', decided_by=?, decided_at=?, decision_note=? "
                      "WHERE id=? AND status='pending'", (actor, ts, "The Why was set", p["id"]))
    return G.goal(c, goal_id)
