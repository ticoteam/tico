"""The Strategy page's market cards (docs/strategy.md#the-page): Where we play and Competitors, written by the owner
in one click each. They are the market graph's own rows (backend/market.py, backend/market_spend.py), nothing new:

- a segment the team plays in (**Us**) is `company/self` `operates_in` it, and one it means to enter next (**Next**)
  is `next: true` on the segment's properties, as the company file's import writes them (backend/company_import.py).
  Several of each. They mirror the Growth segments (backend/growth.py): Us is a segment `serving`, Next one `next`.
  `set_status` is the one write that keeps both in step, for the Growth routes and for a click here. A write
  straight to the graph (the market routes, an import) moves the Growth row too (`follow_graph`): Us makes it
  serving, Next next, and losing both drops a serving or next one. Only people set these: a bot is refused here;
- merging one segment into another moves its Growth row there (`follow_merge`);
- removing a segment retires it and clears its Next (its Growth row, if any, is dropped); its sub-segments move up
  to its parent;
- a competitor is a company or product with a current `competes_with` edge to `company/self`, either way round.
  Removing one ends those edges (the company stays in the graph); adding one by name or site finds the company the
  graph already has, or makes it, and adds the edge.

An edge ends the day before (`until` is the last day it holds, and today it should already be gone), and a new one
starts today, so a competitor removed and added again is a new edge beside the ended one. Each write cites one
evidence row: the owner's say-so on this page (or on the Growth page). Only the owner and the Librarian write here
(`market.require_writer`); the Growth routes check their own rule.
"""
import contextvars
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Request
from pydantic import Field

from . import hubdb as H
from . import market as MK
from . import market_spend as MS
from . import models as M
from .store import Problem

EVIDENCE = "strategy.page"
GROWTH_EVIDENCE = "growth.segments"
STATUSES = ("serving", "testing", "next", "dropped")
COMPETITORS_MAX = 40
# Set while `mirror` writes the graph, so `follow_graph` leaves the Growth row to `set_status`.
_MIRRORING = contextvars.ContextVar("strategy_market_mirroring", default=False)
SITE = re.compile(r"^(https?://)?([a-z0-9-]+(\.[a-z0-9-]+)+)(:\d+)?(/\S*)?$", re.I)


def _evidence(c, actor, growth=False):
    row, _events = MK.create_evidence(
        c, actor, evidence_key=MK.evidence_id(GROWTH_EVIDENCE if growth else EVIDENCE), source_kind="other",
        our_read="Set on the Growth page." if growth else "Set by the owner on the Strategy page.")
    return [row["id"]]


def _days():
    today = datetime.now(timezone.utc).date()            # the market's days are UTC (market_spend._today)
    return today.isoformat(), (today - timedelta(days=1)).isoformat()


def _current(c, where, args):
    today, _ = _days()
    return H._rows(c.execute(
        "SELECT * FROM market_edges WHERE (valid_from_on IS NULL OR substr(valid_from_on,1,10)<=?) AND (valid_until_on IS NULL OR "
        f"substr(valid_until_on,1,10)>=?) AND ({where})", (today, today, *args)))


def _end(c, actor, edge, ev):
    """The edge holds no longer: its last day was yesterday. One that started today starts yesterday too."""
    _, yesterday = _days()
    since = edge["valid_from_on"] if edge["valid_from_on"] and str(edge["valid_from_on"])[:10] <= yesterday else None
    MK.update_edge(c, actor, edge["id"], until=yesterday, evidence_ids=ev,
                   **({"since": yesterday} if edge["valid_from_on"] and not since else {}))


def _own(c, actor, name, ev):
    if not MK.entity(c, MK.OWN_COMPANY_ID):
        MK.create_entity(c, actor, type="company", name=name or "Our company", summary=(name or "Our company") + ".",
                         evidence_ids=ev, force=True, entity_id=MK.OWN_COMPANY_ID)
    return MK.OWN_COMPANY_ID


def _segment(c, segment_id):
    row = MK.entity(c, segment_id)
    if not row or row["type"] != "segment" or row["status"] != "active":
        raise Problem("not_found", f"{segment_id} is not a segment of the market", 404)
    return row


def mirror(c, actor, segment_id, status, company="", growth=False):
    """Where we play as a Growth status says: `serving` is Us, `next` is Next, anything else (or None) neither. Only
    what differs is written."""
    token = _MIRRORING.set(True)
    try:
        _mirror(c, actor, segment_id, status, company, growth)
    finally:
        _MIRRORING.reset(token)


def _mirror(c, actor, segment_id, status, company, growth):
    seg = _segment(c, segment_id)
    ev = None

    def proof():
        nonlocal ev
        ev = ev or _evidence(c, actor, growth)
        return ev
    edges = _current(c, "rel='operates_in' AND src=? AND dst=?", (MK.OWN_COMPANY_ID, segment_id))
    if status == "serving" and not edges:
        today, _ = _days()
        MK.create_edge(c, actor, src=_own(c, actor, company, proof()), rel="operates_in", dst=segment_id, since=today,
                       evidence_ids=proof())
    elif status != "serving":
        for edge in edges:
            _end(c, actor, edge, proof())
    flagged = bool(seg["properties"].get("next"))
    if (status == "next") != flagged:
        props = {k: v for k, v in seg["properties"].items() if k != "next"}
        MK.update_entity(c, actor, segment_id, properties={**props, "next": True} if status == "next" else props,
                         evidence_ids=proof())


def placed(c, segment_id):
    """What Where we play says of the segment: `serving` (Us), `next`, or None."""
    if _current(c, "rel='operates_in' AND src=? AND dst=?", (MK.OWN_COMPANY_ID, segment_id)):
        return "serving"
    row = MK.entity(c, segment_id)
    return "next" if row and row["properties"].get("next") else None


def growth_row(c, segment_id):
    return H._one(c, "SELECT * FROM growth_segments WHERE segment_id=?", (segment_id,))


def set_status(c, actor, segment_id, status, company="", growth=False, create=True):
    """The segment's Growth status and its mirror in the market graph, in one write. Without a Growth row one is made
    (`create`), else only the graph moves. Answers the Growth row or None."""
    if status not in STATUSES:
        raise Problem("validation", f"status is {'|'.join(STATUSES)}", 422)
    mirror(c, actor, segment_id, status, company, growth)
    return _put(c, actor, segment_id, status, create)


def follow_graph(c, actor, segment_id):
    """After a write straight to the graph (backend/market.py) touched a segment's Us edge or Next flag: its Growth
    row follows. Us is serving and Next next (a row is made if none); neither drops a serving or next row and leaves
    a testing or dropped one."""
    if _MIRRORING.get():
        return
    seg = MK.entity(c, segment_id)
    if not seg or seg["type"] != "segment":
        return
    now = placed(c, segment_id) if seg["status"] == "active" else None
    row = growth_row(c, segment_id)
    if now:
        _put(c, actor, segment_id, now, True)
    elif row and row["status"] in ("serving", "next"):
        _put(c, actor, segment_id, "dropped", False)


def follow_merge(c, actor, segment_id, into_id):
    """Segment `segment_id` was merged into `into_id` (backend/market.py merge_entities): its Growth row becomes the
    other's, or folds into the one it has, which takes the stronger status (serving, next, testing, dropped) and any
    field it lacks; channels, ideas and cohorts follow (two cohorts for one channel and month add up into one). Then
    the graph and the row agree on that status."""
    from . import growth as G
    old, row = growth_row(c, segment_id), growth_row(c, into_id)
    if old and not row:
        c.execute("UPDATE growth_segments SET segment_id=?, updated_at=?, updated_by=? WHERE id=?",
                  (into_id, H.now(), actor, old["id"]))
        H.event(c, actor, "growth.segment.update", old["id"], {"segment_id": into_id, "merged_from": segment_id})
    elif old:
        fill = {k: old[k] for k in ("motion", "who_buys", "deal_size", "why", "owner_ref") if old[k] and not row[k]}
        if fill:
            c.execute(f"UPDATE growth_segments SET {', '.join(k + '=?' for k in fill)} WHERE id=?",
                      (*fill.values(), row["id"]))
        for table, key in (("growth_channel_segments", "channel_id"), ("growth_idea_segments", "task_id")):
            c.execute(f"INSERT OR IGNORE INTO {table} ({key}, segment_row_id) SELECT {key}, ? FROM {table} "
                      "WHERE segment_row_id=?", (row["id"], old["id"]))
            c.execute(f"DELETE FROM {table} WHERE segment_row_id=?", (old["id"],))
        for cohort in H._rows(c.execute("SELECT * FROM growth_cohorts WHERE segment_id=?", (old["id"],))):
            same = H._one(c, "SELECT * FROM growth_cohorts WHERE coalesce(channel_id,'')=coalesce(?,'') AND month=? "
                             "AND segment_id=?", (cohort["channel_id"], cohort["month"], row["id"]))
            if not same:
                c.execute("UPDATE growth_cohorts SET segment_id=? WHERE id=?", (row["id"], cohort["id"]))
                continue
            both = [{**r, "channel": r["channel_id"] or ""} for r in (same, cohort)]
            # Defaults fill a missing revenue or margin on one side; both missing stays missing (still the default).
            total = G.aggregate(both, G.settings(c))
            for k in ("revenue_month", "margin_pct"):
                if same[k] is None and cohort[k] is None:
                    total[k] = None
            sources = {**MK.loads(cohort["sources"], {}), **MK.loads(same["sources"], {})}
            keys = ("spend", "bot_cost", "new_customers", "kept_d30", "kept_d90", "kept_d180", "revenue_month",
                    "margin_pct", "quality")
            c.execute(f"UPDATE growth_cohorts SET {', '.join(k + '=?' for k in keys)}, sources=?, updated_by=?, "
                      "updated_at=? WHERE id=?", (*(total[k] for k in keys), MK.dumps(sources), actor, H.now(), same["id"]))
            c.execute("DELETE FROM growth_cohorts WHERE id=?", (cohort["id"],))
        c.execute("DELETE FROM growth_segments WHERE id=?", (old["id"],))
        H.event(c, actor, "growth.segment.merge", row["id"], {"segment_id": into_id, "merged": old["id"]})
    rank = ("serving", "next", "testing", "dropped")
    found = [x for x in ((old or {}).get("status"), (row or {}).get("status"), placed(c, into_id)) if x]
    if found:
        set_status(c, actor, into_id, min(found, key=rank.index))


def _put(c, actor, segment_id, status, create):
    row = growth_row(c, segment_id)
    now = H.now()
    if row and row["status"] != status:
        c.execute("UPDATE growth_segments SET status=?, updated_at=?, updated_by=? WHERE id=?",
                  (status, now, actor, row["id"]))
        H.event(c, actor, "growth.segment.update", row["id"], {"segment_id": segment_id, "status": status})
    elif not row and create:
        rid = H.new_id()
        c.execute("INSERT INTO growth_segments (id, segment_id, status, created_at, updated_at, updated_by) "
                  "VALUES (?,?,?,?,?,?)", (rid, segment_id, status, now, now, actor))
        H.event(c, actor, "growth.segment.create", rid, {"segment_id": segment_id, "status": status})
    return growth_row(c, segment_id)


def target(c, actor, segment_id, to, company=""):
    """Mark a segment Us (`ours`), Next (`next`) or neither (`none`), leaving the others as they are. The segment's
    Growth row follows: serving, next, or for neither dropped (a testing one stays testing; none is made). Answers
    {id, ours, next}."""
    _segment(c, segment_id)
    row = growth_row(c, segment_id)
    status = {"ours": "serving", "next": "next"}.get(to)
    if status is None and row:
        status = "testing" if row["status"] == "testing" else "dropped"
    if status:
        set_status(c, actor, segment_id, status, company)
    else:
        mirror(c, actor, segment_id, None, company)
    return {"id": segment_id, "ours": to == "ours", "next": to == "next"}


def remove_segment(c, actor, segment_id):
    """Retire a segment; its sub-segments move up to its parent (the root for a top one)."""
    _segment(c, segment_id)
    root = (MS.definition(c) or {}).get("root")
    if segment_id == root:
        raise Problem("tree", "The market's root is not removed here", 422)
    ev = _evidence(c, actor)
    up = _current(c, "rel='member_of' AND src=?", (segment_id,))
    parent = up[0]["dst"] if up else root
    today, _ = _days()
    for edge in _current(c, "rel='member_of' AND dst=?", (segment_id,)):
        _end(c, actor, edge, ev)
        if parent:
            MK.create_edge(c, actor, src=edge["src"], rel="member_of", dst=parent, since=today, evidence_ids=ev)
    for edge in _current(c, "src=? OR dst=?", (segment_id, segment_id)):
        _end(c, actor, edge, ev)
    props = MK.entity(c, segment_id)["properties"]
    if "next" in props:                                  # added again later, it is not Next until someone says so
        MK.update_entity(c, actor, segment_id, properties={k: v for k, v in props.items() if k != "next"},
                         evidence_ids=ev)
    MK.retire_entity(c, actor, segment_id)
    if growth_row(c, segment_id):
        _put(c, actor, segment_id, "dropped", False)
    return {"id": segment_id, "removed": True}


def _site(entity):
    domain = str((entity.get("external_ids") or {}).get("domain") or "").strip()
    return "https://" + domain if domain else ""


def competitors(c):
    """[{id, name, url}]: every company or product with a current competes_with edge to the company, by name."""
    ids = set()
    for edge in _current(c, "rel='competes_with' AND (src=? OR dst=?)", (MK.OWN_COMPANY_ID, MK.OWN_COMPANY_ID)):
        ids.add(edge["dst"] if edge["src"] == MK.OWN_COMPANY_ID else edge["src"])
    ids.discard(MK.OWN_COMPANY_ID)
    rows = [MK.entity(c, i) for i in ids]
    rows = [r for r in rows if r and r["status"] == "active" and r["type"] in ("company", "product")]
    rows.sort(key=lambda r: (r["name"].lower(), r["id"]))
    return [{"id": r["id"], "name": r["name"], "url": _site(r)} for r in rows[:COMPETITORS_MAX]]


def add_competitor(c, actor, text, company=""):
    """A competitor by name or site: the company the graph has by that name or domain, else a new one; then the edge."""
    text = " ".join(str(text or "").split())
    site = SITE.match(text)
    host = site.group(2).lower().removeprefix("www.") if site else ""
    url = (text if site.group(1) else "https://" + text) if site else ""
    name = host or text[:120]
    if not name:
        raise Problem("validation", "Give a name or a site", 422)
    ev = _evidence(c, actor)
    found = [r for r in MK._resolve_existing(c, name, [], {"domain": host} if host else {})
             if r["status"] == "active" and r["type"] in ("company", "product") and r["id"] != MK.OWN_COMPANY_ID]
    if found:
        entity = found[0]
    else:
        proof, _ = MK.create_evidence(c, actor, source_url=url, source_kind="site" if url else "other",
                                      our_read="A competitor the owner named on the Strategy page.")
        entity, _ = MK.create_entity(c, actor, type="company", name=name, external_ids={"domain": host} if host else None,
                                     evidence_ids=[proof["id"]], force=True)
    if entity["id"] not in {x["id"] for x in competitors(c)}:
        today, _ = _days()
        MK.create_edge(c, actor, src=entity["id"], rel="competes_with", dst=_own(c, actor, company, ev), since=today,
                       evidence_ids=ev)
    return {"id": entity["id"], "name": entity["name"], "url": _site(entity)}


def remove_competitor(c, actor, entity_id):
    edges = _current(c, "rel='competes_with' AND ((src=? AND dst=?) OR (src=? AND dst=?))",
                     (entity_id, MK.OWN_COMPANY_ID, MK.OWN_COMPANY_ID, entity_id))
    if not edges:
        raise Problem("not_found", f"{entity_id} is not a competitor", 404)
    ev = _evidence(c, actor)
    for edge in edges:
        _end(c, actor, edge, ev)
    return {"id": entity_id, "removed": True}


class SegmentTarget(M.Contract):
    id: str = Field(min_length=1, max_length=200)
    target: Literal["ours", "next", "none"]


class SegmentRemove(M.Contract):
    id: str = Field(min_length=1, max_length=200)


class CompetitorAdd(M.Contract):
    name: str = Field(min_length=1, max_length=300)


class CompetitorRemove(M.Contract):
    id: str = Field(min_length=1, max_length=200)


def install(app, store, auth, mutate):
    company = lambda: str(getattr(store.settings, "company_name", "") or "").strip()

    def writer(who):
        auth.domain(who)
        MK.require_writer(who)

    @app.post("/api/v2/market/segments/target")
    def segment_target(request: Request, body: SegmentTarget):
        who = request.state.identity

        def work(c):
            writer(who)
            if who.role == "bot":
                raise Problem("forbidden", "Only a person marks a segment Us or Next; propose it to the owner instead",
                              403)
            return target(c, who.actor, body.id, body.target, company())

        return mutate(request, body, work)

    @app.post("/api/v2/market/segments/remove")
    def segment_remove(request: Request, body: SegmentRemove):
        who = request.state.identity

        def work(c):
            writer(who)
            return remove_segment(c, who.actor, body.id)

        return mutate(request, body, work)

    @app.get("/api/v2/market/competitors")
    def competitor_list(request: Request):
        auth.domain(request.state.identity)
        with store.read() as c:
            return {"competitors": competitors(c)}

    @app.post("/api/v2/market/competitors")
    def competitor_add(request: Request, body: CompetitorAdd):
        who = request.state.identity

        def work(c):
            writer(who)
            return {"competitor": add_competitor(c, who.actor, body.name, company())}

        return mutate(request, body, work)

    @app.post("/api/v2/market/competitors/remove")
    def competitor_remove(request: Request, body: CompetitorRemove):
        who = request.state.identity

        def work(c):
            writer(who)
            return remove_competitor(c, who.actor, body.id)

        return mutate(request, body, work)
