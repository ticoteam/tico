"""The Strategy page's market cards (docs/strategy.md#segments): the segments the company plays in and the competitors,
written by the owner in one click each. They are the market graph's own rows (backend/market.py, backend/market_spend.py),
nothing new:

- a segment is one of the company's (**ours**) while `company/self` has a current `operates_in` edge to it. There is no
  second mark: a segment the company meant to enter next is one of ours now (backend/company_import.py, and the
  migration that folded the old `next` property). Only people set it: a bot is refused here;
- a segment that becomes one of ours lands last in the list, in the first color no other of ours has (its `rank` and
  `color` in its properties, read by backend/segments.py);
- taking a segment out of the company (`remove_segment`) ends its `operates_in` edge and takes its teammate tags off
  (`untag`). The segment stays in the market: its entity, its member_of edges, its competitors and its sizes. A
  segment that is not the company's is retired by the market's own cleanup (`retire_segment`): its sub-segments move up
  to its parent. The Librarian may retire a segment that is not the company's, never one that is;
- merging a segment into another moves its teammate tags, its strategy bets, its text in the draft and the rows keyed
  by segment (`follow_merge`) to the one it merged into;
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
COLORS = ("blue", "green", "amber", "red", "purple", "teal", "pink", "gray")
# The rows the other modules key by segment: they move to the segment a merge keeps (follow_merge).
MOVED_BY_SEGMENT = (
    "goals",
    "tasks",
    "fin_budgets",
    "fin_contacts",
    "market_briefings",
    "market_sizes",
    "market_figures",
)


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


def ours_ids(c):
    """The ids of the company's segments: active `segment` entities with a current `operates_in` edge from
    `company/self`."""
    today, _ = _days()
    rows = c.execute(
        "SELECT e.dst FROM market_edges e JOIN market_entities s ON s.id=e.dst WHERE e.rel='operates_in' AND e.src=? "
        "AND s.type='segment' AND s.status='active' AND (e.valid_from_on IS NULL OR substr(e.valid_from_on,1,10)<=?) "
        "AND (e.valid_until_on IS NULL OR substr(e.valid_until_on,1,10)>=?)",
        (MK.OWN_COMPANY_ID, today, today),
    )
    return {r[0] for r in rows}


def props_of(row):
    return dict(row.get("properties") or {})


def rank_of(row):
    value = props_of(row).get("rank")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def ordered_rows(c):
    """The company's segments in list order: their rank, then their name. A segment with no rank comes after them."""
    rows = [
        r
        for r in (MK.entity(c, sid) for sid in ours_ids(c))
        if r and r["type"] == "segment" and r["status"] == "active"
    ]
    rows.sort(key=lambda r: (rank_of(r) is None, rank_of(r) or 0, r["name"].lower(), r["id"]))
    return rows


def set_ranks(c, actor, ids, ev=None):
    """The list order: `ids` names every segment the company plays in, in order, and gets ranks 0, 1, ..."""
    for index, sid in enumerate(ids):
        row = MK.entity(c, sid)
        if row and rank_of(row) != index:
            ev = ev or owner_evidence(c, actor)
            MK.update_entity(c, actor, sid, properties={**props_of(row), "rank": index}, evidence_ids=ev)
    return ev


def _stamp(c, actor, segment_id, ev):
    """A segment that became one of the company's lands last, in the first color no other of ours has (or the one it
    kept, when that is valid and free). The others are ranked in their list order first when their ranks are missing
    or have gaps, so the new one is last."""
    others = [r for r in ordered_rows(c) if r["id"] != segment_id]
    if [rank_of(r) for r in others] != list(range(len(others))):
        set_ranks(c, actor, [r["id"] for r in others], ev)
    row = MK.entity(c, segment_id)
    if not row:
        return
    used = {props_of(r).get("color") for r in others}
    kept = props_of(row).get("color")
    color = (
        kept
        if kept in COLORS and kept not in used
        else next((x for x in COLORS if x not in used), COLORS[len(others) % len(COLORS)])
    )
    MK.update_entity(
        c, actor, segment_id, properties={**props_of(row), "rank": len(others), "color": color}, evidence_ids=ev
    )


def _segment(c, segment_id):
    row = MK.entity(c, segment_id)
    if not row or row["type"] != "segment" or row["status"] != "active":
        raise Problem("not_found", f"{segment_id} is not a segment of the market", 404)
    return row


def untag(c, actor, segment_id):
    """A segment that is not one of the company's has no teammate tagged with it: its tags go, and the event names them.
    A teammate's tags on the company's segments are unchanged."""
    members = [
        r[0] for r in c.execute("SELECT member FROM segment_members WHERE segment_id=? ORDER BY member", (segment_id,))
    ]
    if members:
        c.execute("DELETE FROM segment_members WHERE segment_id=?", (segment_id,))
        H.event(c, actor, "segment.untagged", segment_id, {"members": members})


def set_ours(c, actor, segment_id, on, company="", evidence_ids=None):
    """Make the segment one of the company's (`on`) or stop it being one: the company's current `operates_in` edge
    says which, and only a change is written. A segment that becomes one of the company's lands last, in its color
    (`_stamp`). One that stops ends its edge and loses its teammate tags (`untag`); the entity, its member_of edges and
    its competitors stay. `evidence_ids` are what a new edge cites (the owner's click by default)."""
    _segment(c, segment_id)
    edges = _current(c, "rel='operates_in' AND src=? AND dst=?", (MK.OWN_COMPANY_ID, segment_id))
    if on:
        if not edges:
            ev = list(evidence_ids) if evidence_ids else owner_evidence(c, actor)
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
            _stamp(c, actor, segment_id, ev)
        return
    if edges:
        ev = owner_evidence(c, actor)
        for edge in edges:
            _end(c, actor, edge, ev)
    untag(c, actor, segment_id)


def target(c, actor, segment_id, to, company=""):
    """Make a segment one of the company's (`ours`, and `next`, which is the same now) or stop it (`none`). Answers
    {id, ours}."""
    _segment(c, segment_id)
    on = to in ("ours", "next")
    set_ours(c, actor, segment_id, on, company)
    return {"id": segment_id, "ours": on}


def _move_draft_text(c, actor, old, new):
    """The draft's text for a segment that merged away is the one it merged into's, where that one has no text of its
    own for a section (its own text wins). The draft's revision moves on, so a page that read it sees the change."""
    row = c.execute("SELECT id, sections FROM strategy_versions WHERE status='draft'").fetchone()
    if not row:
        return
    raw = MK.loads(row[1], {})
    texts = raw.get("segments") if isinstance(raw.get("segments"), dict) else {}
    if old not in texts:
        return
    moved = texts.pop(old)
    texts[new] = {**moved, **(texts.get(new) or {})}
    raw["segments"] = texts
    c.execute(
        "UPDATE strategy_versions SET sections=?, revision=revision+1, updated_at=?, updated_by=? WHERE id=?",
        (MK.dumps(raw), H.now(), actor, row[0]),
    )


def follow_merge(c, actor, segment_id, into_id):
    """Segment `segment_id` was merged into `into_id` (backend/market.py merge_entities). Its Us edge moved there with
    the graph's other edges. Its teammate tags move there when it is one of the company's (else they go), and so do the
    strategy bets that name it, its text in the draft, and the rows the other modules key by segment."""
    if into_id in ours_ids(c):
        c.execute(
            "INSERT OR IGNORE INTO segment_members (segment_id, member, created_at, created_by) "
            "SELECT ?, member, created_at, created_by FROM segment_members WHERE segment_id=?",
            (into_id, segment_id),
        )
    c.execute("DELETE FROM segment_members WHERE segment_id=?", (segment_id,))
    c.execute("UPDATE strategy_bets SET segment_id=? WHERE segment_id=?", (into_id, segment_id))
    for table in MOVED_BY_SEGMENT:
        if "segment_id" in _columns(c, table):
            c.execute(f"UPDATE {table} SET segment_id=? WHERE segment_id=?", (into_id, segment_id))
    _move_draft_text(c, actor, segment_id, into_id)


def _columns(c, table):
    return {row[1] for row in c.execute(f"PRAGMA table_info({table})")}


def remove_segment(c, actor, segment_id):
    """Take a segment out of the company's: `set_ours` off. The market keeps it: the entity stays active, with its
    member_of edges, its competitors and its sizes. Its teammate tags go (`untag`)."""
    if segment_id not in ours_ids(c):
        raise Problem("not_found", f"{segment_id} is not one of the company's segments", 404)
    set_ours(c, actor, segment_id, False)
    return {"id": segment_id, "removed": True}


def retire_segment(c, actor, segment_id):
    """Retire a segment that is not the company's (the market's own cleanup): its sub-segments move up to its parent
    (the root for a top one) and its edges end. The company's segments are taken out with `remove_segment`."""
    _segment(c, segment_id)
    if segment_id in ours_ids(c):
        raise Problem(
            "forbidden", f"{segment_id} is one of the company's segments; take it out of the company first", 403
        )
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
    untag(c, actor, segment_id)
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
            if body.id not in ours_ids(c):
                return retire_segment(c, who.actor, body.id)  # the Librarian's cleanup of the market
            from . import goal_routes  # a segment the company plays in leaves it on the owner's click only

            if not goal_routes.company_editor(who, tap=True):
                raise Problem("forbidden", "Only the owner takes a segment out of the company's", 403)
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
