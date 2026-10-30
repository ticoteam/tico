"""The Strategy page's market cards (docs/strategy.md#the-page): Where we play and Competitors, written by the owner
in one click each. They are the market graph's own rows (backend/market.py, backend/market_spend.py), nothing new:

- the segment the team plays in (**Us**) is `company/self` `operates_in` it, and the one it means to enter next
  (**Next**) is `next: true` on the segment's properties, as the company file's import writes them
  (backend/company_import.py). One of each: marking a segment Us ends `company/self`'s other `operates_in` edges into
  segments, and marking one Next clears the flag on the others;
- removing a segment retires it; its sub-segments move up to its parent;
- a competitor is a company or product with a current `competes_with` edge to `company/self`, either way round.
  Removing one ends those edges (the company stays in the graph); adding one by name or site finds the company the
  graph already has, or makes it, and adds the edge.

An edge ends the day before (`until` is the last day it holds, and today it should already be gone), and a new one
starts today, so a competitor removed and added again is a new edge beside the ended one. Each write cites one
evidence row: the owner's say-so on this page. Only the owner and the Librarian write (`market.require_writer`).
"""
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
COMPETITORS_MAX = 40
SITE = re.compile(r"^(https?://)?([a-z0-9-]+(\.[a-z0-9-]+)+)(:\d+)?(/\S*)?$", re.I)


def _evidence(c, actor):
    row, _events = MK.create_evidence(c, actor, evidence_key=MK.evidence_id(EVIDENCE), source_kind="other",
                                      our_read="Set by the owner on the Strategy page.")
    return [row["id"]]


def _days():
    today = datetime.now(timezone.utc).date()            # the market's days are UTC (market_spend._today)
    return today.isoformat(), (today - timedelta(days=1)).isoformat()


def _current(c, where, args):
    today, _ = _days()
    return H._rows(c.execute(
        "SELECT * FROM market_edges WHERE (since IS NULL OR substr(since,1,10)<=?) AND (until IS NULL OR "
        f"substr(until,1,10)>=?) AND ({where})", (today, today, *args)))


def _end(c, actor, edge, ev):
    """The edge holds no longer: its last day was yesterday. One that started today starts yesterday too."""
    _, yesterday = _days()
    since = edge["since"] if edge["since"] and str(edge["since"])[:10] <= yesterday else None
    MK.update_edge(c, actor, edge["id"], until=yesterday, evidence_ids=ev,
                   **({"since": yesterday} if edge["since"] and not since else {}))


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


def target(c, actor, segment_id, to, company=""):
    """Mark a segment Us (`ours`), Next (`next`) or neither (`none`). Answers {id, ours, next}."""
    seg = _segment(c, segment_id)
    ev = _evidence(c, actor)
    segments = {r["id"] for r in c.execute("SELECT id FROM market_entities WHERE type='segment'")}
    mine = [e for e in _current(c, "rel='operates_in' AND src=?", (MK.OWN_COMPANY_ID,)) if e["dst"] in segments]
    for edge in mine:
        if (edge["dst"] != segment_id) == (to == "ours"):        # Us moves here, or leaves here
            _end(c, actor, edge, ev)
    if to == "ours" and not any(e["dst"] == segment_id for e in mine):
        today, _ = _days()
        MK.create_edge(c, actor, src=_own(c, actor, company, ev), rel="operates_in", dst=segment_id, since=today,
                       evidence_ids=ev)
    flagged = H._rows(c.execute("SELECT id, properties FROM market_entities WHERE type='segment' AND "
                                "json_valid(properties) AND json_extract(properties,'$.next')"))
    for row in flagged:
        if (row["id"] != segment_id) == (to == "next"):          # Next moves here, or leaves here
            props = {k: v for k, v in MK.loads(row["properties"], {}).items() if k != "next"}
            MK.update_entity(c, actor, row["id"], properties=props, evidence_ids=ev)
    if to == "next" and not seg["properties"].get("next"):
        MK.update_entity(c, actor, segment_id, properties={**seg["properties"], "next": True}, evidence_ids=ev)
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
    MK.retire_entity(c, actor, segment_id)
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
