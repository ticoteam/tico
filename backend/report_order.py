"""The order of a manager's direct reports on the team chart (docs/org-chart.md).

There is no table of its own: one document in `registry_metadata` (key `report_order`) holds, for each human or bot
someone placed by hand, the manager it was placed under and its place there. A place counts only while the report
still reports to that manager. A report that joins a manager someone ordered goes last there, after the ones before
it, whatever its name: every write of `reports_to` calls `arrive` (`access.save_roster` for humans, the bot
definition writes in `settings_admin` and `shared_bots`). Reports with no place, under a manager nobody ordered,
follow the chart's own order (`fallback`): humans first, then bots, temporary ones last and a bot's `order` lowest
first, then by name. ui/app/avatars.js `chartFallback` is the same rule in the app.

Whoever may put a report under a manager may order that manager's reports: the owner, a person the manager is or
reports up to, and the person who runs a bot manager (the `reports_to` rules in `app.person_update` and
`SettingsAdmin.update_bot`). A bot orders its own reports. Who reports to whom is never changed here.

  manager_key(c, auth, who, name)  `bot:<slug>` or `human:<id>` for a name, refused in plain words when nobody has it
  view(c, auth, who, manager) the manager's reports the caller may see, in the chart's order
  save(c, auth, who, manager, names)  put those reports in that order; the ones left out keep their places
  positions(c)                {report key: place} for every place that still counts (the UI's `report_position`)
  placed(c)                   the same with the manager each place is under (the Updates tree)
  rank(c)                     a sort key for (report, manager) in the chart's order, for any list of reports
  arrive(c, report, reports_to)  a report's `reports_to` changed or it joined the chart: it goes last there
  roster_saved(c, roster)     `arrive` for each person whose manager the new roster changes
"""
import json
import math
import re
import unicodedata

from . import hubdb as H
from . import people as P
from . import read_cache
from .store import Problem, encode

KEY = "report_order"


def _load(c):
    row = read_cache.metadata(c, KEY)
    doc = json.loads(row[0]) if row else {}
    return doc if isinstance(doc, dict) else {}


def _store(c, doc):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, encode(doc)))


def _chart(c):
    from . import views
    archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
    return views.roster(c), views.entries(c), archived


def boss_key(kind, reports_to):
    """A `reports_to` value as a manager key: a human's is a person id, a bot's a slug or `human:<id>`; '' at the top."""
    boss = str(reports_to or "").strip()
    if not boss:
        return ""
    if kind == "human":
        return "human:" + boss
    return boss if boss.startswith("human:") else "bot:" + boss


def _boss(kind, ident, roster, configs):
    """Whom a report reports to, as a manager key; '' at the top."""
    if kind == "human":
        return boss_key(kind, (P.person(ident, roster) or {}).get("reports_to"))
    return boss_key(kind, (configs.get(ident) or {}).get("reports_to"))


TEMP = re.compile(r"^Temp\s+")


def _name_key(name):
    """A name as the chart compares it: accents and case aside (ui/app/avatars.js chartNameKey)."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _bot_order(config):
    """A bot's `order` (its definition), lowest first; none sorts after every number (ui/app/avatars.js botOrder)."""
    value = config.get("order")
    if value is None or value == "" or isinstance(value, bool):
        return math.inf
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.inf
    return number if math.isfinite(number) else math.inf


def fallback(kind, ident, name, config=None):
    """The chart's own order for a report with no place: humans first; then bots, temporary ones last and a bot's
    `order` lowest first; then the name; the id breaks a tie."""
    if kind == "human":
        return (0, 0, math.inf, _name_key(name), ident)
    config = config or {}
    temp = bool(config.get("temp")) or bool(TEMP.match(str(name or "")))
    return (1, int(temp), _bot_order(config), _name_key(TEMP.sub("", str(name or ""))), ident)


def rank(c):
    """key(report, manager) sorts any list of a manager's reports in the chart's order: the placed ones by place, then
    the rest by `fallback`. Keys are `bot:<slug>` or `human:<id>`."""
    places = placed(c)
    roster, configs, _ = _chart(c)
    names = {r["slug"]: r["display_name"] or r["slug"] for r in H.bots(c)}

    def key(report, manager):
        at = places.get(report)
        if at and manager and at[0] == manager:
            return (0, at[1], ())
        kind, _, ident = report.partition(":")
        if kind == "human":
            return (1, 0, fallback(kind, ident, (P.person(ident, roster) or {}).get("name") or ident))
        return (1, 0, fallback(kind, ident, names.get(ident) or ident, configs.get(ident)))
    return key


def placed(c):
    """{report key: (manager key, place)} for each place that still counts: the report still reports to the manager it
    was placed under."""
    doc = _load(c)
    if not doc:
        return {}
    roster, configs, _ = _chart(c)
    out = {}
    for report, row in doc.items():
        kind, _, ident = report.partition(":")
        if isinstance(row, dict) and row.get("manager") and _boss(kind, ident, roster, configs) == row["manager"]:
            out[report] = (row["manager"], int(row.get("position") or 0))
    return out


def positions(c):
    """{report key: place} for each place that still counts."""
    return {report: place for report, (_, place) in placed(c).items()}


def arrive(c, report, reports_to):
    """A report joins a manager (`reports_to` as stored for it; '' at the top): its old place is dropped and, when
    someone ordered that manager's reports, it goes last there. Under a manager nobody ordered it keeps no place, so
    the chart's own order holds. `report` is `bot:<slug>` or `human:<id>`."""
    doc = _load(c)
    if not doc:
        return
    manager = boss_key(report.partition(":")[0], reports_to)
    doc.pop(report, None)
    taken = [int(v.get("position") or 0) for v in doc.values() if isinstance(v, dict) and v.get("manager") == manager]
    if manager and taken:
        doc[report] = {"manager": manager, "position": max(taken) + 1}
    _store(c, doc)


def roster_saved(c, roster):
    """Before a roster is stored: each person it adds, or whose `reports_to` it changes, arrives there."""
    if not _load(c):
        return
    row = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    old = json.loads(row[0]) if row else {}
    before = {p.get("id"): str(p.get("reports_to") or "").strip()
              for p in (old.get("people") or []) if isinstance(p, dict)}
    for person in (roster or {}).get("people") or []:
        if not isinstance(person, dict) or not person.get("id"):
            continue
        now = str(person.get("reports_to") or "").strip()
        if person["id"] not in before or before[person["id"]] != now:
            arrive(c, "human:" + person["id"], now)


def manager_key(c, auth, who, name):
    """`bot:<slug>` or `human:<id>` for a bot's slug or a human's id (`bot:` or `human:` in front picks one). A bot the
    caller may not see is nobody they can name."""
    name = str(name or "").strip()
    roster, _, archived = _chart(c)
    kind, _, ident = name.partition(":")
    if kind not in ("bot", "human"):
        kind, ident = "", name
    if (kind in ("", "bot") and ident and H.bot(c, ident) and ident not in archived
            and auth.bot_accesses(c, who).get(ident, auth.FULL)["see"]):
        return "bot:" + ident
    person = P.person(ident, roster) if kind in ("", "human") and ident else None
    if person and not person.get("hidden"):
        return "human:" + person["id"]
    raise Problem("not_found", f"There is no bot or human called {name or '(nobody)'} on the team chart", 404)


def _name(c, key, roster):
    kind, _, ident = key.partition(":")
    if kind == "human":
        return (P.person(ident, roster) or {}).get("name") or ident
    return (H.bot(c, ident) or {}).get("display_name") or ident


def _reports(c, auth, who, manager):
    """All the manager's direct reports in the chart's order, each with its key and whether the caller may see it."""
    roster, configs, archived = _chart(c)
    kind, _, ident = manager.partition(":")
    doc, rows = _load(c), []
    for person in P.visible_people(roster):
        if kind == "human" and str(person.get("reports_to") or "").strip() == ident:
            rows.append({"key": "human:" + person["id"], "id": person["id"], "kind": "human",
                         "name": person.get("name") or person["id"], "seen": True})
    access = auth.bot_accesses(c, who)
    target = manager if kind == "human" else ident
    for bot in H.bots(c):
        slug = bot["slug"]
        if (slug in archived or auth.system_bot(slug)
                or str((configs.get(slug) or {}).get("reports_to") or "").strip() != target):
            continue
        rows.append({"key": "bot:" + slug, "id": slug, "kind": "bot", "name": bot.get("display_name") or slug,
                     "seen": access.get(slug, auth.FULL)["see"]})

    def rank(row):
        placed = doc.get(row["key"])
        if isinstance(placed, dict) and placed.get("manager") == manager:
            return (0, int(placed.get("position") or 0), ())
        return (1, 0, fallback(row["kind"], row["id"], row["name"], configs.get(row["id"]) if row["kind"] == "bot" else None))
    for row in rows:
        row["saved"] = rank(row)[0] == 0
    return sorted(rows, key=rank), roster


def may_order(c, auth, who, manager):
    """Whether the caller may put reports under this manager, and so order them."""
    if who.role == "owner":
        return True
    kind, _, ident = manager.partition(":")
    if who.role == "bot":
        return who.actor == manager
    if who.role != "human":
        return False
    if kind == "human":
        return auth.manages(c, who, "person", ident)
    row = c.execute("SELECT operator FROM bot_config WHERE bot=?", (ident,)).fetchone()
    return bool(row and row["operator"] == H.actor_id(who.actor)) or auth.manages(c, who, "bot", ident)


def view(c, auth, who, manager):
    rows, roster = _reports(c, auth, who, manager)
    kind, _, ident = manager.partition(":")
    return {"manager": ident, "kind": kind, "name": _name(c, manager, roster),
            "can_order": may_order(c, auth, who, manager),
            "reports": [{k: row[k] for k in ("id", "kind", "name", "saved")} for row in rows if row["seen"]]}


def save(c, auth, who, manager, names):
    """Put the named reports in this order. Reports left out keep their places, so naming two swaps just those two."""
    if not may_order(c, auth, who, manager):
        raise Problem("forbidden", "You can't change the order of these reports: only someone who may change who "
                      "reports to " + _name(c, manager, _chart(c)[0]) + " can", 403)
    rows, roster = _reports(c, auth, who, manager)
    by_name = {}
    for row in (r for r in rows if r["seen"]):
        by_name.setdefault(row["key"], row)
        by_name.setdefault(row["id"], row)
    picked, seen = [], set()
    for name in names:
        row = by_name.get(str(name).strip())
        if not row:
            raise Problem("not_report", f"{name} is not a direct report of {_name(c, manager, roster)}. Its reports are: "
                          + (", ".join(r["id"] for r in rows if r["seen"]) or "none"), 422)
        if row["key"] in seen:
            raise Problem("duplicate", f"{name} is named twice", 422)
        seen.add(row["key"])
        picked.append(row)
    # The named reports take the places the named reports had, in the order given; every other report stays put,
    # the ones the caller may not see included.
    slots = iter(picked)
    ordered = [next(slots) if row["key"] in seen else row for row in rows]
    doc = _load(c)
    for key in [k for k, v in doc.items() if isinstance(v, dict) and v.get("manager") == manager]:
        doc.pop(key)
    for i, row in enumerate(ordered):
        doc[row["key"]] = {"manager": manager, "position": i}
    _store(c, doc)
    H.event(c, who.actor, "org.order_set", manager, {"reports": [row["key"] for row in picked]})
    return view(c, auth, who, manager)


# ----------------------------------------------------------------------------- routes
def install(app, store, auth, mutate):
    from fastapi import Request

    from . import models as M

    @app.get("/api/v2/org/order/{manager}")
    def report_order(request: Request, manager: str):
        """A manager's direct reports, humans and the bots the caller may see, in the team chart's order."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return view(c, auth, who, manager_key(c, auth, who, manager))

    @app.put("/api/v2/org/order/{manager}")
    def report_order_set(request: Request, manager: str, body: M.ReportOrder):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            return save(c, auth, who, manager_key(c, auth, who, manager), body.reports)
        return mutate(request, body, work)
