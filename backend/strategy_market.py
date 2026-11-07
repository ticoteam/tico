"""The Strategy page's market cards (docs/strategy.md#segments): the segments the company plays in and the competitors,
written by the owner in one click each. They are the market graph's own rows (backend/market.py, backend/market_spend.py),
nothing new:

- a segment is one of the company's (**ours**) while `company/self` has a current `operates_in` edge to it. There is no
  second mark: a segment the company meant to enter next is one of ours now (backend/company_import.py, and the
  migration that folded the old `next` property). Only people set it: a bot is refused here;
- merging a segment into another moves its teammate tags and the strategy bets that name it to the one it merged into
  (`follow_merge`);
- removing a segment retires it; its sub-segments move up to its parent. Its teammate tags stay: they show again if the
  segment is added back, and nothing reads them while it is not one of the company's (backend/segments.py);
- a competitor is a company or product with a current `competes_with` edge to `company/self`, either way round.
  Removing one ends those edges (the company stays in the graph); adding one by name or site finds the company the
  graph already has, or makes it, and adds the edge.

An edge ends the day before (`until` is the last day it holds, and today it should already be gone), and a new one
starts today, so a competitor removed and added again is a new edge beside the ended one. Each write cites one
evidence row: the owner's say-so on this page. Only the owner and the Librarian write here
(`market.require_writer`).
"""

import re
from datetime import date, timedelta
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


def owner_evidence(c, actor):
    """The evidence an owner's click on the Strategy page cites: one row, its say-so."""
    row, _events = MK.create_evidence(
        c,
        actor,
        evidence_key=MK.evidence_id(EVIDENCE),
        source_kind="other",
        our_read="Set by the owner on the Strategy page.",
    )
    return [row["id"]]


def _days():
    today = date.fromisoformat(H.now()[:10])  # the hub clock (UTC): a demo build dates its rows by its own now
    return today.isoformat(), (today - timedelta(days=1)).isoformat()


def _current(c, where, args):
    today, _ = _days()
    return H._rows(
        c.execute(
            "SELECT * FROM market_edges WHERE (valid_from_on IS NULL OR substr(valid_from_on,1,10)<=?) AND (valid_until_on IS NULL OR "
            f"substr(valid_until_on,1,10)>=?) AND ({where})",
            (today, today, *args),
        )
    )


def _end(c, actor, edge, ev):
    """The edge holds no longer: its last day was yesterday. One that started today starts yesterday too."""
    _, yesterday = _days()
    since = edge["valid_from_on"] if edge["valid_from_on"] and str(edge["valid_from_on"])[:10] <= yesterday else None
    MK.update_edge(
        c,
        actor,
        edge["id"],
        until=yesterday,
        evidence_ids=ev,
        **({"since": yesterday} if edge["valid_from_on"] and not since else {}),
    )


def own_company(c, actor, name, ev):
    """company/self, made from the company's name when the graph has none yet."""
    if not MK.entity(c, MK.OWN_COMPANY_ID):
        MK.create_entity(
            c,
            actor,
            type="company",
            name=name or "Our company",
            summary=(name or "Our company") + ".",
            evidence_ids=ev,
            force=True,
            entity_id=MK.OWN_COMPANY_ID,
        )
    return MK.OWN_COMPANY_ID


def _segment(c, segment_id):
    row = MK.entity(c, segment_id)
    if not row or row["type"] != "segment" or row["status"] != "active":
        raise Problem("not_found", f"{segment_id} is not a segment of the market", 404)
    return row


def set_ours(c, actor, segment_id, on, company=""):
    """Make the segment one of the company's (`on`) or stop it being one: the company's current `operates_in` edge
    says which, and only a change is written."""
    _segment(c, segment_id)
    edges = _current(c, "rel='operates_in' AND src=? AND dst=?", (MK.OWN_COMPANY_ID, segment_id))
    if on and not edges:
        ev = owner_evidence(c, actor)
        today, _ = _days()
        MK.create_edge(
            c,
            actor,
            src=own_company(c, actor, company, ev),
            rel="operates_in",
            dst=segment_id,
            since=today,
            evidence_ids=ev,
        )
    elif not on and edges:
        ev = owner_evidence(c, actor)
        for edge in edges:
            _end(c, actor, edge, ev)


def target(c, actor, segment_id, to, company=""):
    """Make a segment one of the company's (`ours`, and `next`, which is the same now) or stop it (`none`). Answers
    {id, ours}."""
    _segment(c, segment_id)
    on = to in ("ours", "next")
    set_ours(c, actor, segment_id, on, company)
    return {"id": segment_id, "ours": on}


def follow_merge(c, actor, segment_id, into_id):
    """Segment `segment_id` was merged into `into_id` (backend/market.py merge_entities). Its Us edge moved there with
    the graph's other edges; its teammate tags and the strategy bets that name it move there too."""
    c.execute(
        "INSERT OR IGNORE INTO segment_members (segment_id, member, created_at, created_by) "
        "SELECT ?, member, created_at, created_by FROM segment_members WHERE segment_id=?",
        (into_id, segment_id),
    )
    c.execute("DELETE FROM segment_members WHERE segment_id=?", (segment_id,))
    c.execute("UPDATE strategy_bets SET segment_id=? WHERE segment_id=?", (into_id, segment_id))


def remove_segment(c, actor, segment_id):
    """Retire a segment; its sub-segments move up to its parent (the root for a top one). Its edges end, the
    company's operates_in one included, so it is no longer one of the company's."""
    _segment(c, segment_id)
    root = (MS.definition(c) or {}).get("root")
    if segment_id == root:
        raise Problem("tree", "The market's root is not removed here", 422)
    ev = owner_evidence(c, actor)
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
    ev = owner_evidence(c, actor)
    found = [
        r
        for r in MK._resolve_existing(c, name, [], {"domain": host} if host else {})
        if r["status"] == "active" and r["type"] in ("company", "product") and r["id"] != MK.OWN_COMPANY_ID
    ]
    if found:
        entity = found[0]
    else:
        proof, _ = MK.create_evidence(
            c,
            actor,
            source_url=url,
            source_kind="site" if url else "other",
            our_read="A competitor the owner named on the Strategy page.",
        )
        entity, _ = MK.create_entity(
            c,
            actor,
            type="company",
            name=name,
            external_ids={"domain": host} if host else None,
            evidence_ids=[proof["id"]],
            force=True,
        )
    if entity["id"] not in {x["id"] for x in competitors(c)}:
        today, _ = _days()
        MK.create_edge(
            c,
            actor,
            src=entity["id"],
            rel="competes_with",
            dst=own_company(c, actor, company, ev),
            since=today,
            evidence_ids=ev,
        )
    return {"id": entity["id"], "name": entity["name"], "url": _site(entity)}


def remove_competitor(c, actor, entity_id):
    edges = _current(
        c,
        "rel='competes_with' AND ((src=? AND dst=?) OR (src=? AND dst=?))",
        (entity_id, MK.OWN_COMPANY_ID, MK.OWN_COMPANY_ID, entity_id),
    )
    if not edges:
        raise Problem("not_found", f"{entity_id} is not a competitor", 404)
    ev = owner_evidence(c, actor)
    for edge in edges:
        _end(c, actor, edge, ev)
    return {"id": entity_id, "removed": True}


class SegmentTarget(M.Contract):
    id: str = Field(min_length=1, max_length=200)
    target: Literal["ours", "next", "none"]  # next is ours too: a segment is one of the company's or it is not


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
                raise Problem(
                    "forbidden",
                    "Only a person makes a segment one of the company's; propose it to the owner instead",
                    403,
                )
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
