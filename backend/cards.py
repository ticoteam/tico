"""Built-in cards and pins (docs/overview.md, Dashboard and team pages).

A card is one of Tico's own templates (TEMPLATES) with checked settings, never a query. Its numbers are worked out
when it is read, for the reader, from what the reader may already see: the same card on the Dashboard shows each person
their own Needs you and only the tasks they could open. A card the reader may see nothing of reads as `hidden`.

A pin puts a page (`page:<file id>`) or a card (`card:<id>`) at a place, in order and at a size:
- `dashboard`, Overview › Dashboard: everyone sees it; the owner and admins arrange it.
- `team:<group>`, the team page: its audience (Everyone unless narrowed) sees it; the owner, admins and the people who
  lead the group or a group above it arrange it.
- `bot:<slug>`, the bot's sidebar: its rows follow the page and card widgets saved with the sidebar
  (backend/bot_task_tabs.py), which keeps their order.
- `task:<id>`, the top of a task, for whoever may read the task.
- `person:<id>`, a person's My dashboard (the landing page): theirs alone. Only they read and arrange it, BotOps and
  the Assistant only when acting for them; an admin has no way in, and a card pinned there is known to no one else.
  `person:me` is the caller's own. Its cards are cards they made.

A Dashboard, team page or My dashboard nobody saved shows its default cards (DEFAULTS) as pins without ids; saving it
(PUT) makes them real, and every write saves it first.

The pinning rule keeps a page's data from spreading on its own: a bot pins only its own pages and cards, to its own
sidebar or a task it holds or asked for. A page a person pins to the Dashboard or a team page is readable by that place's
audience (`widened`, read by backend/files.py file_visible), so a private task's file is never pinned there, and every
pin and unpin of a page is in the file's activity. A pin on My dashboard widens nothing: its owner is its only reader,
and each card and page there is still read with their own rights.
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import Request
from pydantic import Field

from . import bot_access as A
from . import groups as G
from . import models as M
from . import people as P
from .store import H, Problem, encode

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  id TEXT PRIMARY KEY, template TEXT NOT NULL, settings_json TEXT NOT NULL DEFAULT '{}', title TEXT NOT NULL DEFAULT '',
  made_by TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pins (
  id TEXT PRIMARY KEY, place TEXT NOT NULL, item TEXT NOT NULL, position INTEGER NOT NULL,
  size TEXT NOT NULL DEFAULT 'medium', made_by TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(place, item));
CREATE INDEX IF NOT EXISTS pins_item ON pins(item);
CREATE TABLE IF NOT EXISTS pin_places (
  place TEXT PRIMARY KEY, audience_json TEXT, saved TEXT NOT NULL, saved_by TEXT NOT NULL);
"""

SIZES = ("small", "medium", "large")
PLACE_MAX = 60           # pins at one place: a page of cards, not an archive
CARDS_MAX = 2000
LIST_MAX = 100
IDS_MAX = 50
PAGE_SCAN = 200          # newest pages read to find the ones a reader may see

# template -> its name and settings: "team" ("" is the company), "kpi", or (low, high, default) for a number.
TEMPLATES = {
    "tasks_by_status": {"name": "Tasks by status", "fields": {"team": "team"}},
    "done_per_week": {"name": "Done per week", "fields": {"team": "team", "weeks": (4, 26, 8)}},
    "needs_you": {"name": "Needs you", "fields": {"team": "team"}},
    "spend": {"name": "Spend", "fields": {"team": "team"}},
    "kpi": {"name": "KPI", "fields": {"kpi": "kpi"}},
    "automation_runs": {"name": "Automation runs", "fields": {"team": "team"}},
    "latest_pages": {"name": "Latest pages", "fields": {"team": "team", "count": (1, 10, 5)}},
    "needs_attention": {"name": "Needs attention", "fields": {}},
    "unread_threads": {"name": "Unread threads", "fields": {"count": (1, 10, 5)}},
    "my_tasks": {"name": "My tasks", "fields": {"count": (1, 10, 5)}},
    "updates": {"name": "Updates", "fields": {"count": (1, 10, 5)}},
}
DEFAULTS = {"dashboard": [("tasks_by_status", "medium"), ("done_per_week", "medium"), ("needs_you", "medium"),
                          ("latest_pages", "medium")],
            "team": [("tasks_by_status", "medium"), ("done_per_week", "medium"), ("needs_you", "medium")],
            "person": [("needs_attention", "medium"), ("unread_threads", "medium"), ("my_tasks", "medium"),
                       ("updates", "medium"), ("latest_pages", "medium")]}
WIDENING = ("dashboard", "team")


class CardAdd(M.Contract):
    template: str = Field(max_length=40)
    title: str = Field(default="", max_length=80)
    settings: dict = Field(default_factory=dict)
    place: str = Field(default="", max_length=200)
    size: str = Field(default="medium", max_length=10)


class CardEdit(M.Contract):
    title: str | None = Field(default=None, max_length=80)
    settings: dict | None = None


class PinAdd(M.Contract):
    place: str = Field(max_length=200)
    item: str = Field(max_length=200)
    size: str = Field(default="medium", max_length=10)
    position: int | None = Field(default=None, ge=0, le=PLACE_MAX)


class PinEdit(M.Contract):
    size: str | None = Field(default=None, max_length=10)
    position: int | None = Field(default=None, ge=0, le=PLACE_MAX)


class PlaceAudience(M.Contract):
    everyone: bool = True
    people: list[str] = Field(default_factory=list, max_length=500)
    teams: list[str] = Field(default_factory=list, max_length=200)
    bots: list[str] = Field(default_factory=list, max_length=500)


class PlaceSave(M.Contract):
    audience: PlaceAudience | None = None


class PlaceOrder(M.Contract):
    ids: list[str] = Field(max_length=PLACE_MAX)


def bad(message):
    return Problem("validation", message, 422)


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S")


def monday(at=None):
    at = at or datetime.now(timezone.utc)
    return (at - timedelta(days=at.weekday())).strftime("%Y-%m-%d")


class Ctx:
    """One reader's request: the org chart and what they may read, each worked out once however many cards ask."""

    def __init__(self, c, auth, who, settings=None, task_view=None, pages=None):
        self.c, self.auth, self.who, self.settings, self.task_view = c, auth, who, settings, task_view
        self.pages = pages          # backend/pages.py Pages: a pinned page's full read (status line, src)
        self._memo = {}

    def memo(self, key, make):
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    @property
    def roster(self):
        return self.memo("roster", lambda: G._roster(self.c))

    @property
    def configs(self):
        return self.memo("configs", lambda: G._configs(self.c))

    @property
    def groups(self):
        return self.roster.get("org_groups") or {}

    @property
    def person(self):
        return self.who.role in ("owner", "human")

    @property
    def admin(self):
        return self.person and bool(self.auth.bot_admin(self.who))

    def visible(self):
        return self.memo("visible", lambda: self.auth.task_sql(self.c, self.who))

    def team_bots(self, gid):
        def make():
            below = P.group_and_below(gid, self.roster)
            return {s for s in self.configs if P.team_of(s, self.configs, self.roster) in below}
        return self.memo(("bots", gid), make)

    def team_actors(self, gid):
        """The bots and people in the group and the groups under it, as actors; None is the whole company."""
        if not gid:
            return None
        def make():
            below = P.group_and_below(gid, self.roster)
            people = {"human:" + p["id"] for p in P.visible_people(self.roster) if p.get("team") in below}
            return {"bot:" + s for s in self.team_bots(gid)} | people
        return self.memo(("actors", gid), make)

    def chain(self):
        """The groups the reader is in, their own and the ones it is nested in."""
        def make():
            if self.person:
                own = (P.person(H.actor_id(self.who.actor), self.roster) or {}).get("team") or ""
            elif self.who.role == "bot":
                own = P.team_of(H.actor_id(self.who.actor), self.configs, self.roster) or ""
            else:
                own = ""
            return P.group_chain(own, self.roster) or ([own] if own else [])
        return self.memo("chain", make)

    def led(self):
        return self.memo("led", lambda: G.led_groups(self.c, self.who.actor, self.roster) if self.person else set())


# ---- settings

def clean_settings(ctx, template, given):
    if template not in TEMPLATES:
        raise bad("Unknown card: " + str(template) + ". Cards are " + ", ".join(TEMPLATES))
    fields = TEMPLATES[template]["fields"]
    given = dict(given or {})
    if unknown := sorted(set(given) - set(fields)):
        raise bad(f"{TEMPLATES[template]['name']} has no setting " + ", ".join(unknown))
    out = {}
    for key, kind in fields.items():
        value = given.get(key)
        if kind == "team":
            value = str(value or "").strip()
            if value and value not in ctx.groups:
                raise bad("Unknown team: " + value)
        elif kind == "kpi":
            value = str(value or "").strip()
            if not value:
                raise bad("Pick a KPI")
            if not kpi_record(ctx, value):
                raise Problem("not_found", "Unknown KPI", 404)
        else:
            low, high, default = kind
            value = default if value is None else value
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise bad(f"{key.capitalize()} is a number from {low} to {high}")
        out[key] = value
    return out


def kpi_record(ctx, kid):
    from . import goal_routes, kpis as K
    record = K.kpi(ctx.c, kid)
    if not record or record.get("owner") in goal_routes._hidden_owners(ctx.c, ctx.auth, ctx.who):
        return None
    return record


# ---- what a card shows its reader

def owner_sql(actors):
    if actors is None:
        return "1", []
    return "owner IN (SELECT value FROM json_each(?))", [json.dumps(sorted(actors))]


def tasks_by_status(ctx, s):
    where, args = owner_sql(ctx.team_actors(s["team"]))
    visible = ctx.visible()
    counts = {"open": 0, "doing": 0, "waiting": 0, "done": 0}
    for status, n in ctx.c.execute(
            f"SELECT status, count(*) FROM tasks WHERE ({visible}) AND {where} AND status IN "
            "('open','ready','doing','review','waiting') GROUP BY status", args):
        counts["doing" if status in ("ready", "review") else status] += n
    counts["done"] = ctx.c.execute(
        f"SELECT count(*) FROM tasks WHERE ({visible}) AND {where} AND status='done' AND coalesce(done_at,'')>=?",
        (*args, monday())).fetchone()[0]
    return {"counts": counts}


def done_per_week(ctx, s):
    where, args = owner_sql(ctx.team_actors(s["team"]))
    weeks = [(datetime.now(timezone.utc) - timedelta(weeks=n)) for n in range(s["weeks"] - 1, -1, -1)]
    starts = [monday(w) for w in weeks]
    out = {start: 0 for start in starts}
    for day, n in ctx.c.execute(
            f"SELECT substr(done_at,1,10), count(*) FROM tasks WHERE ({ctx.visible()}) AND {where} AND status='done' "
            "AND coalesce(done_at,'')>=? GROUP BY substr(done_at,1,10)", (*args, starts[0])):
        week = monday(datetime.strptime(day, "%Y-%m-%d"))
        if week in out:
            out[week] += n
    return {"weeks": [{"week": w, "done": n} for w, n in out.items()]}


def needs_list(ctx):
    from . import views
    return ctx.memo("needs", lambda: views.needs_items(ctx.c, ctx.auth, ctx.who, ctx.task_view))


def needs_brief(items, n):
    return [{"id": it.get("id"), "kind": it.get("kind"), "title": it.get("title") or "",
             "task_id": it.get("task_id") or (it.get("id") if it.get("kind") in ("task", "question", "waiting") else None)}
            for it in items[:n]]


def needs_you(ctx, s):
    """The reader's own Needs you; with a team, the items its bots are behind."""
    if not ctx.person or not ctx.task_view:
        return None
    from . import views
    items = needs_list(ctx)
    if s["team"]:
        bots = {"bot:" + b for b in ctx.team_bots(s["team"])}
        items = [it for it in items if views.needs_actor(it, ctx.who.actor) in bots]
    return {"count": len(items), "items": needs_brief(items, 3)}


def needs_attention(ctx, s):
    """The reader's own Needs you list, its first five: what waits on them, wherever it came from."""
    if not ctx.person or not ctx.task_view:
        return None
    items = needs_list(ctx)
    return {"count": len(items), "items": needs_brief(items, 5)}


def thread_rows(ctx, limit):
    """The reader's unread threads, from core's sidebar (`threads.sidebar`, docs/dev/threads-contract.md §10), most
    recent first: [{id, title, unread, activity, updated}]. A root row speaks for its subtree, and other bots' posts
    (activity alone) are not unread. The sidebar lists only threads the reader may read."""
    from . import threads
    viewer = threads.viewer(ctx.auth, ctx.who)
    data = threads.sidebar(ctx.c, viewer, limit=50)
    rows = []
    for t in (data.get("attention") or []) + (data.get("threads") or []) + (data.get("dms") or []):
        me = t.get("me") or {}
        if t.get("id") and me.get("unread") and t["id"] not in {r["id"] for r in rows}:
            rows.append({"id": t["id"], "title": t.get("title") or "", "unread": int(me.get("unread") or 0),
                         "activity": bool(me.get("activity")), "updated": t.get("last_message_at"),
                         "seq": t.get("activity_seq") or t.get("last_seq") or 0})
    rows.sort(key=lambda r: r["seq"], reverse=True)
    return [{k: v for k, v in r.items() if k != "seq"} for r in rows[:limit]]


def unread_threads(ctx, s):
    """Threads with something for the reader: a human message or one addressed to them (bot activity is not unread)."""
    if ctx.who.role not in ("owner", "human", "bot"):
        return None
    rows = thread_rows(ctx, 50)
    return {"count": sum(1 for r in rows if r["unread"]), "threads": rows[:s["count"]]}


OPEN_STATUSES = H.ACTIVE_STATUSES


def my_tasks(ctx, s):
    """The open tasks the reader owns and may read, the soonest due first."""
    if ctx.who.role not in ("owner", "human", "bot"):
        return None
    marks = ",".join("?" * len(OPEN_STATUSES))
    where = f"({ctx.visible()}) AND owner=? AND status IN ({marks})"
    args = (ctx.who.actor, *OPEN_STATUSES)
    count = ctx.c.execute(f"SELECT count(*) FROM tasks WHERE {where}", args).fetchone()[0]
    rows = ctx.c.execute(f"SELECT id, title, status, due, updated FROM tasks WHERE {where} "
                         "ORDER BY due IS NULL, due, updated DESC, id LIMIT ?", (*args, s["count"])).fetchall()
    return {"count": count, "tasks": [{"id": r["id"], "title": r["title"] or "", "status": r["status"], "due": r["due"],
                                       "updated": r["updated"]} for r in rows]}


def updates_card(ctx, s):
    """The newest updates the reader may read (backend/updates.py visible_authors) and how many they have not read."""
    from . import updates as U
    if ctx.who.role not in ("owner", "human", "bot"):
        return None
    readable = U.visible_authors(ctx.c, ctx.auth, ctx.who)
    found = U.listing(ctx.c, ctx.who.actor, readable, limit=s["count"], may_read=readable,
                      meetings=U.meeting_reader(ctx.who))
    return {"unread": found["unread"],
            "updates": [{"id": u["id"], "author": u["bot"], "kind": u["kind"], "day": u["day"],
                         "headline": u["headline"], "created": u["created"], "read": u["read"]}
                        for u in found["updates"][:s["count"]]]}


def spend(ctx, s):
    """This month's spend against its budget: the owner and admins, or a lead for a team they lead."""
    if not ctx.person or not (ctx.admin or (s["team"] and s["team"] in ctx.led())):
        return None
    from . import usage_limits as U
    default = ctx.memo("spend_default", lambda: U.company(ctx.c))
    scope = "group:" + s["team"] if s["team"] else U.COMPANY
    _, start, end = U.bounds()["monthly"]
    spent = U.pool_spent(ctx.c, scope, start, end, default["count_subscription"], default)
    budget = (U.pools(ctx.c, default).get(scope) or {}).get("monthly_usd")
    return {"spent": round(spent, 2), "budget": budget, "month": start[:7]}


def kpi(ctx, s):
    from . import kpis as K
    record = kpi_record(ctx, s["kpi"])
    if not record:
        return None
    view = K.view(record, K.readings(ctx.c, record["id"], effective=True))
    latest = view.get("latest") or {}
    return {"kpi": record["id"], "name": view["name"], "unit": view.get("unit") or "", "value": latest.get("value"),
            "at": latest.get("period_end"), "spark": view.get("spark") or [], "status": view.get("status"),
            "freshness": view.get("freshness")}


def automation_runs(ctx, s):
    from . import automations as AU
    chart = ctx.memo("chart", lambda: AU.Chart(ctx.c))
    actors = ctx.team_actors(s["team"])
    groups = {"group:" + g for g in P.group_and_below(s["team"], ctx.roster)} if s["team"] else None
    keys = {AU._key(a) for a in actors or ()}
    names = {}
    for row in ctx.c.execute("SELECT id, name, owner, grp, flow_json FROM automations WHERE deleted_at IS NULL"):
        if groups is not None and row["grp"] not in groups and AU._key(row["owner"]) not in keys:
            continue
        if AU.may_change(ctx.c, ctx.who, {**AU.flow_of(row), "readonly": False}, chart):
            names[row["id"]] = row["name"]
    if not names:
        return {"total": 0, "failed": 0, "running": 0, "runs": []}
    runs = [dict(r) for r in ctx.c.execute(
        "SELECT id, automation_id, status, started, finished FROM automation_runs WHERE started>=? "
        "AND automation_id IN (SELECT value FROM json_each(?)) ORDER BY started DESC LIMIT 500",
        (ago(hours=24), json.dumps(sorted(names))))]
    failed = [r for r in runs if r["status"] == "failed"]
    shown = (failed + [r for r in runs if r["status"] != "failed"])[:5]
    return {"total": len(runs), "failed": len(failed),
            "running": sum(r["status"] in ("queued", "running") for r in runs),
            "runs": [{**r, "name": names[r["automation_id"]]} for r in shown]}


def pages_for(ctx, team="", limit=5):
    """The newest current pages (backend/pages.py) the reader may read, a team's bots' only when `team` is set: not
    archived, not deleted, not replaced by a newer page."""
    from . import files as F
    bots = ctx.team_bots(team) if team else None
    rows = ctx.c.execute("SELECT f.* FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.archived=0 "
                         "AND p.replaced_by IS NULL AND f.archived=0 AND f.state<>'deleted' "
                         "ORDER BY f.last_activity_at DESC, f.id DESC LIMIT ?", (PAGE_SCAN,)).fetchall()
    rows = [r for r in rows if bots is None or r["bot"] in bots]
    F.prime_visible(ctx.c, rows)
    cache = ctx.memo("file_cache", dict)
    out = []
    for row in rows:
        if F.file_visible(ctx.c, ctx.auth, ctx.who, row, cache):
            out.append(page_brief(row))
            if len(out) >= limit:
                break
    return out


def page_brief(row):
    return {"file_id": row["id"], "title": row["title"], "bot": row["bot"], "updated": row["last_activity_at"],
            "archived": bool(row["archived"])}


def page_read(ctx, row, files_cache):
    """A pinned page as pageCard draws it: the contract's page read (status line fields and a fresh src)."""
    pages = ctx.pages
    found = pages.rows(ctx.c, [row["id"]]).get(row["id"]) if pages is not None else None
    if not found:
        return page_brief(row)
    return {**page_brief(row), **pages.view(ctx.c, ctx.who, found, pages.page_of(found), files_cache)}


def page_row(c, fid):
    """A page's file, or None for anything that is not a page (or was deleted for good)."""
    return c.execute("SELECT f.* FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.file_id=? "
                     "AND f.state<>'deleted'", (fid,)).fetchone()


PLACE_WORDS = {"dashboard": "Dashboard"}


def pinned_places(ctx, fid):
    """Where a page is pinned, as the places the reader may open: [{place, label}], the Dashboard first."""
    from . import names
    out = []
    for (place,) in ctx.c.execute("SELECT DISTINCT place FROM pins WHERE item=? ORDER BY place", ("page:" + fid,)):
        try:
            kind, ident = parse(ctx, place)
        except Problem:
            continue
        if not may_view(ctx, kind, ident):
            continue
        if kind == "dashboard":
            label = "Dashboard"
        elif kind == "team":
            label = ctx.groups[ident].get("name") or ident
        elif kind == "bot":
            label = names.lookup(ctx.c, {"bot:" + ident}).get("bot:" + ident) or ident
        elif kind == "person":
            label = "My dashboard"
        else:
            label = (H.task(ctx.c, ident) or {}).get("title") or "Task"
        out.append({"place": place, "label": label})
    return sorted(out, key=lambda p: p["place"] != "dashboard")


def latest_pages(ctx, s):
    return {"pages": pages_for(ctx, s["team"], s["count"])}


DATA = {"tasks_by_status": tasks_by_status, "done_per_week": done_per_week, "needs_you": needs_you, "spend": spend,
        "kpi": kpi, "automation_runs": automation_runs, "latest_pages": latest_pages,
        "needs_attention": needs_attention, "unread_threads": unread_threads, "my_tasks": my_tasks,
        "updates": updates_card}


def card_view(ctx, row):
    """A card for its reader: its settings and what it shows them, or `hidden` when that is nothing."""
    settings = json.loads(row["settings_json"]) if isinstance(row.get("settings_json"), str) else row["settings"]
    template = row["template"]
    team = settings.get("team") or ""
    data = None
    if template in DATA and (not team or team in ctx.groups):
        try:
            data = DATA[template](ctx, {**{k: v[2] for k, v in TEMPLATES[template]["fields"].items()
                                           if isinstance(v, tuple)}, **settings})
        except Problem:
            data = None
    name = TEMPLATES.get(template, {}).get("name", template)
    return {"id": row.get("id"), "template": template, "title": row.get("title") or "", "settings": settings,
            "name": name, "heading": row.get("title") or name,
            "team_name": (ctx.groups.get(team) or {}).get("name") or team if team else "",
            "hidden": data is None, "data": data, "made_by": row.get("made_by")}


# ---- places

def parse(ctx, place):
    """(kind, ident) of a place that exists, else 404."""
    place = str(place or "").strip()
    kind, _, ident = place.partition(":")
    if place == "dashboard":
        return "dashboard", ""
    if kind == "team" and ident in ctx.groups:
        return kind, ident
    if kind == "bot" and ident and H.bot(ctx.c, ident):
        return kind, ident
    if kind == "task" and ident and H.task(ctx.c, ident):
        return kind, ident
    if kind == "person" and ident:
        if ident == "me" and ctx.person:
            ident = H.actor_id(ctx.who.actor)
        if ident != "me" and H.human(ctx.c, ident):
            return kind, ident
    raise Problem("not_found", "No such place: places are dashboard, team:<group>, bot:<slug>, task:<id> and "
                  "person:me", 404)


def place_row(c, place):
    return c.execute("SELECT * FROM pin_places WHERE place=?", (place,)).fetchone()


def audience_of(c, place):
    """Who sees a team page: its group's Read (teammate_access.migrate_team_pages moved the old page audiences there)."""
    from . import teammate_access
    if place.startswith("team:"):
        return teammate_access.document(c, "group:" + place[5:])["read"]
    row = place_row(c, place)
    return A.audience(json.loads(row["audience_json"]) if row and row["audience_json"] else {"everyone": True})


def own_person(ctx, ident):
    """Whether `person:<ident>` is the reader's own My dashboard: the person, or BotOps or the Assistant acting for
    them (both carry the person's actor)."""
    return ctx.person and ctx.who.actor == H.human_actor(ident)


def someone_elses(ctx, places):
    """Whether any of these places is another person's My dashboard, which no admin right reaches."""
    for place in places:
        kind, _, ident = place.partition(":")
        if kind == "person" and not own_person(ctx, ident):
            return True
    return False


def may_view(ctx, kind, ident):
    who = ctx.who
    if who.role not in ("owner", "human", "bot"):
        return False
    if kind == "dashboard":
        return True
    if kind == "person":
        return own_person(ctx, ident)
    if kind == "team":
        if may_manage(ctx, kind, ident):
            return True
        from . import teammate_access
        # A team page is read by whoever may read its group (backend/teammate_access.py): "Seen by" is the group's Read.
        return teammate_access.can(ctx.c, who, "read", "group:" + ident, ctx.auth)
    if kind == "bot":
        return bool(ctx.auth.bot_access(ctx.c, who, ident)["read"])
    try:
        ctx.auth.task(ctx.c, who, ident)
        return True
    except Problem:
        return False


def may_manage(ctx, kind, ident):
    """Who arranges a place (module docstring); a bot only its own sidebar and the tasks it holds or asked for."""
    who = ctx.who
    if kind == "dashboard":
        return ctx.admin
    if kind == "person":
        return own_person(ctx, ident)
    if kind == "team":
        return ctx.admin or (ctx.person and ident in ctx.led())
    if kind == "bot":
        from . import bot_task_tabs as T
        return not T.personal(ctx.settings, ident) and T.may_edit(ctx.c, ctx.auth, ctx.settings, who, ident)
    try:
        row = ctx.auth.task(ctx.c, who, ident)
    except Problem:
        return False
    if who.role == "bot":
        return not who.via and who.actor in (row["owner"], row["requester"])
    return ctx.person and (who.role == "owner" or who.actor in (row["owner"], row["requester"])
                           or H.can_move(ctx.c, who.actor))


def require_view(ctx, place):
    kind, ident = parse(ctx, place)
    if not may_view(ctx, kind, ident):
        if kind == "task":
            raise Problem("not_found", "Task not found", 404)
        if kind == "person":
            raise Problem("not_found", "No such place", 404)
        raise Problem("forbidden", "Not shared with you", 403)
    return kind, ident


def require_manage(ctx, place):
    kind, ident = require_view(ctx, place)
    if not may_manage(ctx, kind, ident):
        raise Problem("forbidden", {
            "dashboard": "The owner and admins arrange the Dashboard",
            "team": "The owner, admins and the team's leads arrange a team page",
            "bot": "The bot and the people who manage it arrange its sidebar",
            "task": "A task's people arrange what is pinned to it",
            "person": "Only its person arranges My dashboard"}[kind], 403)
    return kind, ident


def canonical(kind, ident):
    return kind if kind == "dashboard" else kind + ":" + ident


def pins_at(c, place):
    return [dict(r) for r in c.execute("SELECT * FROM pins WHERE place=? ORDER BY position, created, id", (place,))]


def card_row(c, cid):
    row = c.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def default_settings(kind, ident, template):
    """A default card's settings: a team page's cards are its team's; the rest are the company's or the reader's."""
    return {"team": ident if kind == "team" else ""} if "team" in TEMPLATES[template]["fields"] else {}


def default_pins(kind, ident):
    return [{"id": None, "place": canonical(kind, ident), "item": None, "position": i, "size": size, "made_by": None,
             "card": {"id": None, "template": t, "settings_json": json.dumps(default_settings(kind, ident, t)),
                      "title": "", "made_by": None}}
            for i, (t, size) in enumerate(DEFAULTS.get(kind, []))]


def pin_view(ctx, pin, files_cache):
    """A pin as its reader gets it, or None when it shows them nothing: a page they may not read leaves no trace."""
    from . import files as F
    out = {k: pin.get(k) for k in ("id", "item", "position", "size", "made_by")}
    if pin.get("card") is not None:
        return {**out, "card": card_view(ctx, pin["card"])}
    kind, _, ident = (pin["item"] or "").partition(":")
    if kind == "card":
        row = card_row(ctx.c, ident)
        return {**out, "card": card_view(ctx, row)} if row else None
    row = page_row(ctx.c, ident)
    if not row or not F.file_visible(ctx.c, ctx.auth, ctx.who, row, files_cache):
        return None
    return {**out, "page": page_read(ctx, row, files_cache)}


def place_view(ctx, place):
    kind, ident = require_view(ctx, place)
    place = canonical(kind, ident)
    saved = kind not in DEFAULTS or bool(place_row(ctx.c, place))
    pins = pins_at(ctx.c, place) if saved else default_pins(kind, ident)
    cache = ctx.memo("file_cache", dict)
    shown = [v for v in (pin_view(ctx, p, cache) for p in pins) if v]
    out = {"place": place, "kind": kind, "saved": saved, "can_edit": may_manage(ctx, kind, ident), "pins": shown}
    if kind == "team":
        out.update(group=ident, name=ctx.groups[ident].get("name") or ident,
                   audience=audience_of(ctx.c, place))
    return out


# ---- writes

def log(ctx, action, target, detail, private=False):
    """The event a write leaves. A write to someone's My dashboard names nothing: no card, page or place, since the
    event log is the company's (the owner reads it whole, `hub sql` included) and the dashboard is its person's alone."""
    if private:
        H.event(ctx.c, ctx.who.actor, action, "", {})
    else:
        H.event(ctx.c, ctx.who.actor, action, target, detail)


def on_person_place(places):
    return any(p.startswith("person:") for p in places)


def ensure(ctx, kind, ident):
    """Save a Dashboard or team page's defaults the first time it is changed, so the pins have ids."""
    place = canonical(kind, ident)
    if kind not in DEFAULTS or place_row(ctx.c, place):
        return
    now = H.now()
    ctx.c.execute("INSERT INTO pin_places(place, audience_json, saved, saved_by) VALUES(?,?,?,?)",
                  (place, None, now, ctx.who.actor))
    for pin in default_pins(kind, ident):
        cid = make_card(ctx, pin["card"]["template"], "", json.loads(pin["card"]["settings_json"]),
                        private=kind == "person")
        insert_pin(ctx, place, "card:" + cid, pin["size"], pin["position"])


def make_card(ctx, template, title, settings, private=False):
    if ctx.c.execute("SELECT count(*) FROM cards").fetchone()[0] >= CARDS_MAX:
        raise Problem("too_many", f"This team keeps {CARDS_MAX} cards: remove some first", 409)
    settings = clean_settings(ctx, template, settings)
    cid, now = H.new_id(), H.now()
    ctx.c.execute("INSERT INTO cards(id,template,settings_json,title,made_by,created,updated,updated_by) "
                  "VALUES(?,?,?,?,?,?,?,?)", (cid, template, json.dumps(settings), (title or "").strip(), ctx.who.actor,
                                              now, now, ctx.who.actor))
    log(ctx, "card.added", cid, {"template": template}, private)
    return cid


def size_of(value):
    if value not in SIZES:
        raise bad("A size is small, medium or large")
    return value


def file_activity(c, fid, actor, action, place):
    c.execute("INSERT INTO bot_file_activity(file_id,actor,action,created,detail_json) VALUES(?,?,?,?,?)",
              (fid, actor, action, H.now(), encode({"place": place})))


def check_item(ctx, kind, item):
    """The pinning rule (module docstring) for one item at a place of `kind`."""
    from . import files as F
    what, _, ident = str(item or "").partition(":")
    who = ctx.who
    if what == "card":
        row = card_row(ctx.c, ident)
        if not row:
            raise Problem("not_found", "No such card", 404)
        if who.role == "bot" and row["made_by"] != who.actor:
            raise Problem("forbidden", "A bot pins only cards it made", 403)
        if not card_readable(ctx, row):
            raise Problem("not_found", "No such card", 404)
        if kind == "person" and row["made_by"] != who.actor:
            raise Problem("forbidden", "My dashboard holds only cards you made", 403)
        return
    if what != "page":
        raise bad("An item is page:<file id> or card:<id>")
    row = page_row(ctx.c, ident)
    if not row or not F.file_visible(ctx.c, ctx.auth, who, row, ctx.memo("file_cache", dict)):
        raise Problem("not_found", "No such page", 404)
    if who.role == "bot" and row["bot"] != H.actor_id(who.actor):
        raise Problem("forbidden", "A bot pins only its own pages; a person pins another bot's", 403)
    if kind in WIDENING and row["task_id"] and H.task_private(ctx.c, H.task(ctx.c, row["task_id"])):
        raise Problem("privacy", "Private task files stay on their task", 403)


def insert_pin(ctx, place, item, size, position=None):
    count = ctx.c.execute("SELECT count(*) FROM pins WHERE place=?", (place,)).fetchone()[0]
    if count >= PLACE_MAX:
        raise Problem("too_many", f"A place holds {PLACE_MAX} pins", 409)
    if ctx.c.execute("SELECT 1 FROM pins WHERE place=? AND item=?", (place, item)).fetchone():
        raise Problem("conflict", "Already pinned here", 409)
    position = count if position is None else min(position, count)
    ctx.c.execute("UPDATE pins SET position=position+1 WHERE place=? AND position>=?", (place, position))
    pid = H.new_id()
    ctx.c.execute("INSERT INTO pins(id,place,item,position,size,made_by,created) VALUES(?,?,?,?,?,?,?)",
                  (pid, place, item, position, size_of(size), ctx.who.actor, H.now()))
    if item.startswith("page:") and not place.startswith("person:"):     # My dashboard is its person's alone
        file_activity(ctx.c, item[5:], ctx.who.actor, "pinned", place)
    return pid


def renumber(c, place):
    for at, pin in enumerate(pins_at(c, place)):
        c.execute("UPDATE pins SET position=? WHERE id=?", (at, pin["id"]))


def drop_pin(ctx, pin):
    ctx.c.execute("DELETE FROM pins WHERE id=?", (pin["id"],))
    if pin["item"].startswith("page:"):
        if not pin["place"].startswith("person:"):
            file_activity(ctx.c, pin["item"][5:], ctx.who.actor, "unpinned", pin["place"])
    elif pin["item"].startswith("card:"):
        cid = pin["item"][5:]
        if not ctx.c.execute("SELECT 1 FROM pins WHERE item=?", (pin["item"],)).fetchone():
            ctx.c.execute("DELETE FROM cards WHERE id=?", (cid,))        # pinned nowhere: it was only that pin
    renumber(ctx.c, pin["place"])


def add_pin(ctx, place, item, size="medium", position=None):
    kind, ident = require_manage(ctx, place)
    check_item(ctx, kind, item)
    place = canonical(kind, ident)
    if kind == "bot":
        from . import bot_task_tabs as T
        T.add_widget(ctx, ident, item, position)
        pin = ctx.c.execute("SELECT * FROM pins WHERE place=? AND item=?", (place, item)).fetchone()
        if size != "medium":
            ctx.c.execute("UPDATE pins SET size=? WHERE id=?", (size_of(size), pin["id"]))
        return pin["id"]
    ensure(ctx, kind, ident)
    pid = insert_pin(ctx, place, item, size, position)
    log(ctx, "pin.added", place, {"item": item}, kind == "person")
    return pid


def pin_or_404(ctx, pid):
    row = ctx.c.execute("SELECT * FROM pins WHERE id=?", (pid,)).fetchone()
    if not row:
        raise Problem("not_found", "No such pin", 404)
    kind, ident = parse(ctx, row["place"])
    if not may_view(ctx, kind, ident):
        raise Problem("not_found", "No such pin", 404)
    return dict(row), kind, ident


def edit_pin(ctx, pid, size=None, position=None):
    pin, kind, ident = pin_or_404(ctx, pid)
    require_manage(ctx, pin["place"])
    if size is not None:
        ctx.c.execute("UPDATE pins SET size=? WHERE id=?", (size_of(size), pid))
    if position is not None:
        order = [p["id"] for p in pins_at(ctx.c, pin["place"]) if p["id"] != pid]
        order.insert(min(position, len(order)), pid)
        reorder(ctx, pin["place"], order)
    return pid


def remove_pin(ctx, pid):
    pin, kind, ident = pin_or_404(ctx, pid)
    require_manage(ctx, pin["place"])
    if kind == "bot":
        from . import bot_task_tabs as T
        T.remove_widget(ctx, ident, pin["item"])
    else:
        drop_pin(ctx, pin)
    log(ctx, "pin.removed", pin["place"], {"item": pin["item"]}, kind == "person")
    return {"removed": pid}


def reorder(ctx, place, ids):
    kind, ident = require_manage(ctx, place)
    place = canonical(kind, ident)
    have = [p["id"] for p in pins_at(ctx.c, place)]
    if sorted(ids) != sorted(have):
        raise bad("Send every pin at the place once, in the new order")
    if kind == "bot":
        from . import bot_task_tabs as T
        items = {p["id"]: p["item"] for p in pins_at(ctx.c, place)}
        T.order_widgets(ctx, ident, [items[i] for i in ids])
    for at, pid in enumerate(ids):
        ctx.c.execute("UPDATE pins SET position=? WHERE id=?", (at, pid))


def sync_bot(ctx, slug, widgets):
    """Make the bot place's pins the page and card widgets of its saved sidebar, in their order."""
    place = "bot:" + slug
    wanted = [w["id"] for w in widgets if w["id"].startswith(("page:", "card:"))]
    for pin in pins_at(ctx.c, place):
        if pin["item"] not in wanted:
            drop_pin(ctx, pin)
    have = {p["item"]: p for p in pins_at(ctx.c, place)}
    for at, item in enumerate(wanted):
        if item in have:
            ctx.c.execute("UPDATE pins SET position=? WHERE id=?", (at, have[item]["id"]))
        else:
            check_item(ctx, "bot", item)
            ctx.c.execute("INSERT INTO pins(id,place,item,position,size,made_by,created) VALUES(?,?,?,?,?,?,?)",
                          (H.new_id(), place, item, at, "medium", ctx.who.actor, H.now()))
            if item.startswith("page:"):
                file_activity(ctx.c, item[5:], ctx.who.actor, "pinned", place)


def readable_item(ctx, item):
    """Whether the reader may know a sidebar widget's item is there: a page they may read, or any card."""
    from . import files as F
    if not item.startswith("page:"):
        return True
    row = page_row(ctx.c, item[5:])
    return bool(row) and F.file_visible(ctx.c, ctx.auth, ctx.who, row, ctx.memo("file_cache", dict))


def card_places(ctx, row):
    return [r[0] for r in ctx.c.execute("SELECT place FROM pins WHERE item=?", ("card:" + row["id"],))]


def may_edit_card(ctx, row):
    """The owner and admins, unless it is on another person's My dashboard; else whoever may arrange every place it is
    pinned to (its maker when it is nowhere)."""
    places = card_places(ctx, row)
    if ctx.admin and not someone_elses(ctx, places):
        return True
    if not places:
        return row["made_by"] == ctx.who.actor
    for place in places:
        try:
            kind, ident = parse(ctx, place)
        except Problem:
            continue
        if not may_manage(ctx, kind, ident):
            return False
    return True


def card_or_404(ctx, cid):
    row = card_row(ctx.c, cid)
    if not row or not card_readable(ctx, row):
        raise Problem("not_found", "No such card", 404)
    return row


def card_readable(ctx, row):
    """A card is known to whoever made it, may edit it, or sees a place it is pinned to. Another person's My dashboard
    is no way in, an admin's included."""
    places = card_places(ctx, row)
    if row["made_by"] == ctx.who.actor or (ctx.admin and not someone_elses(ctx, places)):
        return True
    for place in places:
        try:
            if may_view(ctx, *parse(ctx, place)):
                return True
        except Problem:
            continue
    return False


def edit_card(ctx, cid, title=None, settings=None):
    row = card_or_404(ctx, cid)
    if not may_edit_card(ctx, row):
        raise Problem("forbidden", "Whoever arranges the places it is pinned to changes a card", 403)
    fields = {}
    if title is not None:
        fields["title"] = title.strip()
    if settings is not None:
        merged = {**json.loads(row["settings_json"]), **settings}
        fields["settings_json"] = json.dumps(clean_settings(ctx, row["template"], merged))
    if fields:
        fields.update(updated=H.now(), updated_by=ctx.who.actor)
        ctx.c.execute("UPDATE cards SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), cid))
        log(ctx, "card.changed", cid, {"fields": sorted(k for k in fields if k != "updated")},
            on_person_place(card_places(ctx, row)))
    return card_view(ctx, card_row(ctx.c, cid))


def remove_card(ctx, cid):
    row = card_or_404(ctx, cid)
    if not may_edit_card(ctx, row):
        raise Problem("forbidden", "Whoever arranges the places it is pinned to removes a card", 403)
    private = on_person_place(card_places(ctx, row))
    for pin in [dict(r) for r in ctx.c.execute("SELECT * FROM pins WHERE item=?", ("card:" + cid,))]:
        kind, ident = parse(ctx, pin["place"])
        if kind == "bot":
            from . import bot_task_tabs as T
            T.remove_widget(ctx, ident, pin["item"])
        else:
            drop_pin(ctx, pin)
    ctx.c.execute("DELETE FROM cards WHERE id=?", (cid,))
    log(ctx, "card.removed", cid, {"template": row["template"]}, private)
    return {"removed": cid}


def save_place(ctx, place, audience=None):
    kind, ident = require_manage(ctx, place)
    if kind not in DEFAULTS:
        raise bad("Only the Dashboard, team pages and My dashboard are saved whole")
    ensure(ctx, kind, ident)
    place = canonical(kind, ident)
    if audience is not None:
        if kind == "person":
            raise bad("My dashboard is yours alone")
        if kind != "team":
            raise bad("Everyone sees the Dashboard")
        # The page's audience is its group's Read (See, being on the chart, is left as it is): set there, with the
        # group's Manage check and audit row.
        from . import teammate_access
        target = "group:" + ident
        level = A.audience(audience.model_dump())
        current = teammate_access.document(ctx.c, target)
        if current["read"] != level:
            teammate_access.save(ctx.c, ctx.who, target, {**current, "read": level,
                                                         "revision": teammate_access.revision(ctx.c, target)}, ctx.auth)
        ctx.c.execute("UPDATE pin_places SET saved=?, saved_by=? WHERE place=?", (H.now(), ctx.who.actor, place))
        H.event(ctx.c, ctx.who.actor, "place.audience", place, {"everyone": level["everyone"]})
    return place


def widened(c, auth, who, fid, cache):
    """Whether a person reads this file because a person pinned it to the Dashboard or a team page they see."""
    if who.role not in ("owner", "human"):
        return False
    if "pins" not in cache:
        found = {}
        for item, place in c.execute("SELECT item, place FROM pins WHERE item LIKE 'page:%' AND made_by LIKE 'human:%' "
                                     "AND (place='dashboard' OR place LIKE 'team:%')"):
            found.setdefault(item[5:], []).append(place)
        cache["pins"] = found
    places = cache["pins"].get(fid)
    if not places:
        return False
    if "dashboard" in places:
        return True
    ctx = Ctx(c, auth, who)
    return any(place[5:] in ctx.groups and may_view(ctx, "team", place[5:]) for place in places)


def choices(ctx, team=""):
    """What the card and pin forms offer the reader: templates, teams, the KPIs and pages they may read."""
    from . import kpis as K  # noqa: F401
    from . import goal_routes, teammate_access
    hidden = goal_routes._hidden_owners(ctx.c, ctx.auth, ctx.who)
    kpis = [{"id": r["id"], "name": r["name"]} for r in ctx.c.execute(
        "SELECT id, name, owner FROM kpis WHERE archived_at IS NULL ORDER BY name LIMIT 500") if r["owner"] not in hidden]
    return {"templates": [{"id": k, "name": v["name"], "fields": [f for f in v["fields"]]} for k, v in TEMPLATES.items()],
            "teams": teammate_access.group_rows(ctx.c, ctx.who, ctx.groups, ctx.auth),
            "kpis": kpis, "pages": pages_for(ctx, team if team in ctx.groups else "", LIST_MAX)}


def install(app, store, auth, mutate, settings, task_view):
    def context(c, request):
        who = request.state.identity
        auth.domain(who)
        return Ctx(c, auth, who, settings, task_view, getattr(app.state, "pages", None))

    @app.get("/api/v2/places/{place}")
    def place_show(request: Request, place: str):
        """A place's pins in order, each card with what it shows the caller; pages the caller may not read are left
        out. A Dashboard, team page or My dashboard nobody saved shows its default cards, pins without ids (`saved`
        false)."""
        with store.read_transaction() as c:
            return place_view(context(c, request), place)

    @app.put("/api/v2/places/{place}")
    def place_save(request: Request, place: str, body: PlaceSave):
        """Save a Dashboard, team page or My dashboard (its default cards become real pins) and, for a team page, who
        sees it."""
        def work(c):
            ctx = context(c, request)
            return place_view(ctx, save_place(ctx, place, body.audience))
        return mutate(request, body, work)

    @app.post("/api/v2/places/{place}/order")
    def place_order(request: Request, place: str, body: PlaceOrder):
        """Put every pin at the place in this order."""
        def work(c):
            ctx = context(c, request)
            kind, ident = require_manage(ctx, place)
            ensure(ctx, kind, ident)
            reorder(ctx, place, body.ids)
            return place_view(ctx, place)
        return mutate(request, body, work)

    @app.post("/api/v2/pins")
    def pin_add(request: Request, body: PinAdd):
        """Pin a page or a card at a place (the pinning rule: module docstring)."""
        def work(c):
            ctx = context(c, request)
            pid = add_pin(ctx, body.place, body.item, body.size, body.position)
            return {"id": pid, **place_view(ctx, body.place)}
        return mutate(request, body, work)

    @app.patch("/api/v2/pins/{pid}")
    def pin_edit(request: Request, pid: str, body: PinEdit):
        def work(c):
            ctx = context(c, request)
            edit_pin(ctx, pid, body.size, body.position)
            return place_view(ctx, c.execute("SELECT place FROM pins WHERE id=?", (pid,)).fetchone()[0])
        return mutate(request, body, work)

    @app.delete("/api/v2/pins/{pid}")
    def pin_remove(request: Request, pid: str):
        """Unpin. A card pinned nowhere else goes with its pin."""
        def work(c):
            return remove_pin(context(c, request), pid)
        return mutate(request, M.Empty(), work)

    @app.get("/api/v2/cards")
    def cards_list(request: Request, ids: str = "", limit: int = 50, cursor: str = ""):
        """Cards the caller made or sees pinned, newest first, each with what it shows them. `ids` reads up to 50."""
        with store.read_transaction() as c:
            ctx = context(c, request)
            if ids:
                wanted = [i for i in dict.fromkeys(ids.split(",")) if i][:IDS_MAX]
                rows = [r for r in (card_row(c, i) for i in wanted) if r and card_readable(ctx, r)]
                return {"cards": [card_view(ctx, r) for r in rows], "next": None}
            limit = max(1, min(limit, LIST_MAX))
            out, last = [], None
            sql, args = "SELECT * FROM cards", []
            if cursor:
                sql += " WHERE (created<? OR created=? AND id<?)"
                at, _, cid = cursor.partition("|")
                args += [at, at, cid]
            for row in c.execute(sql + " ORDER BY created DESC, id DESC", args):
                row = dict(row)
                if card_readable(ctx, row):
                    if len(out) == limit:
                        return {"cards": out, "next": f"{last['created']}|{last['id']}"}
                    out.append(card_view(ctx, row))
                    last = row
            return {"cards": out, "next": None}

    @app.get("/api/v2/cards/choices")
    def cards_choices(request: Request, team: str = ""):
        """The card templates and their settings, the teams, and the KPIs and pages the caller may pick."""
        with store.read_transaction() as c:
            return choices(context(c, request), team)

    @app.post("/api/v2/cards")
    def card_add(request: Request, body: CardAdd):
        """A new card; with `place` it is pinned there in the same write (who may arrange the place)."""
        def work(c):
            ctx = context(c, request)
            if body.place:
                kind, ident = require_manage(ctx, body.place)
                ensure(ctx, kind, ident)
            elif not ctx.person and ctx.who.role != "bot":
                raise Problem("forbidden", "A person or a bot makes cards", 403)
            private = bool(body.place) and parse(ctx, body.place)[0] == "person"
            cid = make_card(ctx, body.template, body.title, body.settings, private)
            out = {"card": card_view(ctx, card_row(c, cid))}
            if body.place:
                out["pin"] = add_pin(ctx, body.place, "card:" + cid, body.size)
            return out
        return mutate(request, body, work)

    @app.patch("/api/v2/cards/{cid}")
    def card_edit(request: Request, cid: str, body: CardEdit):
        def work(c):
            return {"card": edit_card(context(c, request), cid, body.title, body.settings)}
        return mutate(request, body, work)

    @app.delete("/api/v2/cards/{cid}")
    def card_remove(request: Request, cid: str):
        """Remove a card and every pin of it."""
        def work(c):
            return remove_card(context(c, request), cid)
        return mutate(request, M.Empty(), work)
