# pyright: strict
"""Business segments (docs/strategy.md#segments): the groups of customers the company wins differently, each with its own
strategy, the teammates dedicated to it, its goals, budget and P&L line, under the one company strategy.

A segment is a market graph `segment` entity (backend/market.py) the company plays in: `company/self` has a current
`operates_in` edge to it (backend/strategy_market.py). There is no second segment concept. This module adds:

- its fields: the entity's name, one line of who it is for (its summary), a color and a rank (its properties);
- the teammates tagged with it (`segment_members`): a teammate tagged with a segment is dedicated to it, and `all` tags
  one who works across every segment. Its leads are the dedicated teammates whose manager is not dedicated to it too:
  they see its P&L line (backend/finance);
- the segment's own strategy, in the company's draft and versions (`sections.segments`, backend/strategy.py), read
  field by field against the company's text;
- the segment text a dedicated bot's run is handed (`run_text`, backend/learned.py).

Writes are the owner's, on their own click (goal_routes.company_editor), except teammate tags, which an owner or a bot
admin sets as they set groups. Reads are for everyone signed in. A segment that stops being one of the company's keeps its
tags, which show again when it is one of them once more.
"""

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from fastapi import Request

from . import hubdb as H
from . import market as MK
from . import market_spend as MS
from . import models as M
from . import people as P
from . import strategy_market as SM
from .store import Problem

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from fastapi import FastAPI

    from .auth import Auth, Identity
    from .store import Store

ALL = "all"  # a teammate tag: works across every segment
COLORS = ("blue", "green", "amber", "red", "purple", "teal", "pink", "gray")
NAME_MAX = 120
WHO_MAX = 200  # one line
FACES_MAX = 24  # the faces a segment shows; the count of its dedicated teammates says how many there are
SEGMENTS_MAX = 50
MEMBERS_MAX = 500
RUN_MAX = 12_000  # characters of segment text a run is handed

Row = dict[str, Any]  # a market entity, a strategy version or a bet, as the modules that keep them hand them over
Directory = dict[str, Any]  # {"names", "depth", "hidden"}: who a viewer sees, by teammate ref


# ----------------------------------------------------------------------------- the modules this one reads
# They are not strict yet, so each call goes through a typed view here (the way ask_refusals.py does it).
def _entity(c: sqlite3.Connection, sid: str) -> Row | None:
    return cast(Callable[[sqlite3.Connection, str], Row | None], MK.entity)(c, sid)


def _slug(text: str) -> str:
    return cast(Callable[[str], str], MK.slug)(text)


def _create_entity(c: sqlite3.Connection, actor: str, **fields: Any) -> None:
    cast(Callable[..., object], MK.create_entity)(c, actor, **fields)


def _update_entity(c: sqlite3.Connection, actor: str, sid: str, **fields: Any) -> None:
    cast(Callable[..., object], MK.update_entity)(c, actor, sid, **fields)


def _create_edge(c: sqlite3.Connection, actor: str, **fields: Any) -> None:
    cast(Callable[..., object], MK.create_edge)(c, actor, **fields)


def _definition(c: sqlite3.Connection) -> Row | None:
    return cast(Callable[[sqlite3.Connection], Row | None], MS.definition)(c)


def _owner_evidence(c: sqlite3.Connection, actor: str) -> list[str]:
    return cast(Callable[[sqlite3.Connection, str], list[str]], SM.owner_evidence)(c, actor)


def _set_ours(c: sqlite3.Connection, actor: str, sid: str, on: bool, company: str) -> None:
    cast(Callable[[sqlite3.Connection, str, str, bool, str], object], SM.set_ours)(c, actor, sid, on, company)


def _remove_segment(c: sqlite3.Connection, actor: str, sid: str) -> Row:
    return cast(Callable[[sqlite3.Connection, str, str], Row], SM.remove_segment)(c, actor, sid)


def _event(c: sqlite3.Connection, actor: str, action: str, target: str, detail: Row) -> None:
    cast(Callable[[sqlite3.Connection, str, str, str, Row], object], H.event)(c, actor, action, target, detail)


def _resolve_bot(c: sqlite3.Connection, ref: str) -> Row | None:
    return cast(Callable[[sqlite3.Connection, str], Row | None], H.resolve_bot)(c, ref)


def _roster(c: sqlite3.Connection) -> Row | None:
    from . import views

    return cast(Callable[[sqlite3.Connection], Row | None], views.roster)(c)


def _entries(c: sqlite3.Connection) -> Row:
    from . import views

    return cast(Callable[[sqlite3.Connection], Row], views.entries)(c)


def _person(pid: str, roster: Row | None) -> Row | None:
    return cast(Callable[[str, Row | None], Row | None], P.person)(pid, roster)


def _org_view(roster: Row | None, employees: Row, archived: set[str]) -> Row:
    return cast(Callable[..., Row], P.org_view)(roster, employees, archived, bare_people=True)


def _unseen(c: sqlite3.Connection, who: "Identity", auth: "Auth | None") -> tuple[set[str], set[str]]:
    from . import teammate_access

    return cast(Callable[..., tuple[set[str], set[str]]], teammate_access.unseen)(c, who, auth)


def _company_editor(who: "Identity") -> bool:
    from . import goal_routes

    return cast(Callable[..., bool], goal_routes.company_editor)(who)


def _domain(auth: "Auth", who: "Identity") -> None:
    cast(Callable[..., object], auth.domain)(who)


def _bot_admin(auth: "Auth", who: "Identity") -> bool:
    return cast(Callable[..., bool], auth.bot_admin)(who)


def _draft(c: sqlite3.Connection) -> Row | None:
    from . import strategy as ST

    return cast(Callable[[sqlite3.Connection], Row | None], ST.draft)(c)


def _current(c: sqlite3.Connection) -> Row | None:
    from . import strategy as ST

    return cast(Callable[[sqlite3.Connection], Row | None], ST.current)(c)


def _bets(c: sqlite3.Connection) -> list[Row]:
    from . import strategy as ST

    return [b for b in cast(Callable[[sqlite3.Connection], list[Row | None]], ST.bets)(c) if b]


def _update_draft(c: sqlite3.Connection, actor: str, revision: int | None, sections: Row) -> None:
    from . import strategy as ST

    cast(Callable[..., object], ST.update_draft)(c, actor, revision, sections=sections)


def _read_conn(store: "Store") -> "AbstractContextManager[sqlite3.Connection]":
    return cast(Callable[[], "AbstractContextManager[sqlite3.Connection]"], store.read)()


def _company_name(store: "Store") -> str:
    settings = cast(Any, store.settings)
    return str(getattr(settings, "company_name", "") or "").strip()


# ----------------------------------------------------------------------------- the segments
def ours_ids(c: sqlite3.Connection) -> set[str]:
    """The ids of the company's segments: active `segment` entities with a current `operates_in` edge from
    `company/self`."""
    today = H.now()[:10]
    rows = c.execute(
        "SELECT e.dst FROM market_edges e JOIN market_entities s ON s.id=e.dst WHERE e.rel='operates_in' AND e.src=? "
        "AND s.type='segment' AND s.status='active' AND (e.valid_from_on IS NULL OR substr(e.valid_from_on,1,10)<=?) "
        "AND (e.valid_until_on IS NULL OR substr(e.valid_until_on,1,10)>=?)",
        (MK.OWN_COMPANY_ID, today, today),
    )
    return {r[0] for r in rows}


def require_ours(c: sqlite3.Connection, segment_id: str | None) -> str:
    """`segment_id` normalised: "" (no segment) stays "", anything else must be one of the company's segments."""
    sid = str(segment_id or "").strip()
    if sid and sid not in ours_ids(c):
        raise Problem("not_found", f"{sid} is not one of the company's segments", 404)
    return sid


# ----------------------------------------------------------------------------- the segments
def _props(row: Row) -> Row:
    return dict(row.get("properties") or {})


def _rank(row: Row) -> int | None:
    value = _props(row).get("rank")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _color(row: Row, index: int) -> str:
    """The segment's color, or the one its place in the list gets while it has none."""
    stored = _props(row).get("color")
    return stored if stored in COLORS else COLORS[index % len(COLORS)]


def _ordered(c: sqlite3.Connection) -> list[Row]:
    """The company's segments in list order: their rank, then their name."""
    rows = [
        r for r in (_entity(c, sid) for sid in ours_ids(c)) if r and r["type"] == "segment" and r["status"] == "active"
    ]
    rows.sort(key=lambda r: (_rank(r) is None, _rank(r) or 0, r["name"].lower(), r["id"]))
    return rows


def _row_of(c: sqlite3.Connection, sid: str) -> Row:
    """The entity row of one of the company's segments. `require_ours` has checked it is one, so the row is there."""
    row = _entity(c, sid)
    if row is None:
        raise Problem("not_found", f"{sid} is not one of the company's segments", 404)
    return row


def _ours_row(c: sqlite3.Connection, segment_id: str | None) -> Row:
    sid = require_ours(c, segment_id)
    if not sid:
        raise Problem("not_found", "There is no segment with an empty id", 404)
    return _row_of(c, sid)


def _text(text: object, limit: int, label: str, required: bool = False) -> str:
    text = " ".join(str(text or "").split())
    if required and not text:
        raise Problem("validation", f"Give the segment {label}", 422)
    if len(text) > limit:
        raise Problem("validation", f"Keep the segment {label} under {limit} characters", 422)
    return text


def _segment_view(row: Row, index: int, faces: list[Row], teammates: int) -> Row:
    return {
        "id": row["id"],
        "name": row["name"],
        "who": row["summary"],
        "color": _color(row, index),
        "rank": index,
        "faces": faces[:FACES_MAX],
        "teammates": teammates,
    }


def _view_of(c: sqlite3.Connection, segment_id: str) -> Row:
    """One segment as the list shows it, read with no viewer's hiding (the owner's own writes)."""
    ordered = [r["id"] for r in _ordered(c)]
    directory = _directory(c)
    tagged = _dedicated(c, segment_id)
    live = _faces_of(tagged, directory)
    row = _row_of(c, segment_id)
    return _segment_view(row, ordered.index(segment_id), [_face(r, directory) for r in live], len(live))


def listing(c: sqlite3.Connection, auth: "Auth", who: "Identity") -> list[Row]:
    """GET /api/v2/segments: every segment the company plays in, in list order, each with its dedicated teammates
    (faces) highest on the org chart first. A teammate the viewer may not see is left out, and counted out."""
    directory = _directory(c, who, auth)
    tagged = _tagged(c)
    out: list[Row] = []
    for index, row in enumerate(_ordered(c)[:SEGMENTS_MAX]):
        live = _faces_of(tagged.get(row["id"], []), directory)
        out.append(_segment_view(row, index, [_face(r, directory) for r in live], len(live)))
    return out


def create(
    c: sqlite3.Connection,
    actor: str,
    name: str,
    who: str = "",
    color: str | None = None,
    company: str = "",
) -> Row:
    """A segment the company plays in: a new market segment under the market's root, or an existing one of that name
    joins the company's, or a removed one comes back with its id and its history. Its color is the first one no
    other segment has."""
    name = _text(name, NAME_MAX, "name", required=True)
    who = _text(who, WHO_MAX, "one line of who it is for")
    seg_id = "segment/" + _slug(name)
    if seg_id == "segment/":
        raise Problem("validation", "Give the segment a name with letters or digits", 422)
    ours = _ordered(c)
    used = {_color(r, i) for i, r in enumerate(ours)}
    chosen = color or next((x for x in COLORS if x not in used), COLORS[len(ours) % len(COLORS)])
    root = (_definition(c) or {}).get("root")
    today = H.now()[:10]
    ev = _owner_evidence(c, actor)
    existing = _entity(c, seg_id)
    if existing and existing["type"] != "segment":
        raise Problem("exists", f"{seg_id} is not a segment", 409, extra={"id": seg_id})
    if existing and existing["status"] == "active" and seg_id in ours_ids(c):
        raise Problem("exists", f"{name} is already one of the company's segments", 409, extra={"id": seg_id})
    if existing:
        kept = _props(existing).get("color")
        _update_entity(
            c,
            actor,
            seg_id,
            status="active",
            summary=who or existing["summary"],
            properties={**_props(existing), "color": kept if kept in COLORS else chosen, "rank": len(ours)},
            evidence_ids=ev,
        )
    else:
        _create_entity(
            c,
            actor,
            type="segment",
            name=name,
            summary=who,
            properties={"color": chosen, "rank": len(ours)},
            evidence_ids=ev,
            entity_id=seg_id,
        )
    if (
        root
        and not c.execute(
            "SELECT 1 FROM market_edges WHERE src=? AND rel='member_of' AND (valid_from_on IS NULL OR "
            "substr(valid_from_on,1,10)<=?) AND (valid_until_on IS NULL OR substr(valid_until_on,1,10)>=?)",
            (seg_id, today, today),
        ).fetchone()
    ):
        _create_edge(c, actor, src=seg_id, rel="member_of", dst=root, since=today, evidence_ids=ev)
    _set_ours(c, actor, seg_id, True, company)
    return _view_of(c, seg_id)


def update(
    c: sqlite3.Connection,
    actor: str,
    segment_id: str,
    name: str | None = None,
    who: str | None = None,
    color: str | None = None,
) -> Row:
    """Its name, who it is for (`""` clears it) and color: only what is sent changes."""
    row = _ours_row(c, segment_id)
    changes: Row = {}
    if name is not None:
        name = _text(name, NAME_MAX, "name", required=True)
        if name != row["name"]:
            changes["name"] = name
    if who is not None:
        who = _text(who, WHO_MAX, "one line of who it is for")
        if who != row["summary"]:
            changes["summary"] = who
    if color is not None and color != _props(row).get("color"):
        changes["properties"] = {**_props(row), "color": color}
    if changes:
        _update_entity(c, actor, segment_id, evidence_ids=_owner_evidence(c, actor), **changes)
    return _view_of(c, segment_id)


def reorder(c: sqlite3.Connection, actor: str, ids: list[str]) -> None:
    """The list order: `ids` names every segment the company plays in, once each."""
    current = [r["id"] for r in _ordered(c)]
    if len(set(ids)) != len(ids) or sorted(ids) != sorted(current):
        raise Problem("validation", "List every segment the company plays in, once each", 422)
    ev: list[str] | None = None
    for index, sid in enumerate(ids):
        row = _row_of(c, sid)
        if _rank(row) != index:
            ev = ev or _owner_evidence(c, actor)
            _update_entity(c, actor, sid, properties={**_props(row), "rank": index}, evidence_ids=ev)


def remove(c: sqlite3.Connection, actor: str, segment_id: str) -> Row:
    """Retire the segment (its sub-segments move up to its parent, backend/strategy_market.py). Its teammate tags stay."""
    _ours_row(c, segment_id)
    return _remove_segment(c, actor, segment_id)


# ----------------------------------------------------------------------------- teammates
def _tagged(c: sqlite3.Connection) -> dict[str, list[str]]:
    """{segment id: [teammate refs]} for the company's segments, `all` left out."""
    ours = ours_ids(c)
    out: dict[str, list[str]] = {}
    for sid, ref in c.execute("SELECT segment_id, member FROM segment_members ORDER BY segment_id, member"):
        if sid != ALL and sid in ours:
            out.setdefault(sid, []).append(ref)
    return out


def _dedicated(c: sqlite3.Connection, segment_id: str) -> list[str]:
    """The teammate refs tagged with this segment (not `all`), whether or not the viewer may see them."""
    return [
        r[0] for r in c.execute("SELECT member FROM segment_members WHERE segment_id=? ORDER BY member", (segment_id,))
    ]


def member_segments(c: sqlite3.Connection, ref: str) -> list[str]:
    """The segments a teammate (`bot:<id>` or `person:<id>`) is tagged with: the company's segment ids, and `all` as the
    string `all`. A tag on a segment that is not the company's is kept and not returned."""
    ours = ours_ids(c)
    return [
        r[0]
        for r in c.execute("SELECT segment_id FROM segment_members WHERE member=? ORDER BY segment_id", (ref,))
        if r[0] == ALL or r[0] in ours
    ]


def _member_ref(c: sqlite3.Connection, text: str) -> str:
    """A teammate as the tags name it: `bot:<id>` (a bot's id, however it was named) or `person:<id>`."""
    kind, _, ident = str(text or "").strip().partition(":")
    kind = "person" if kind == "human" else kind
    if kind == "bot":
        bot = _resolve_bot(c, ident)
        if not bot:
            raise Problem("not_found", f"There is no bot {ident}", 404)
        return "bot:" + bot["id"]
    if kind == "person":
        if not _person(ident, _roster(c)):
            raise Problem("not_found", f"There is no person {ident}", 404)
        return "person:" + ident
    raise Problem("validation", "A teammate is bot:<id> or person:<id>", 422)


def set_member(c: sqlite3.Connection, actor: str, member: str, wanted: list[str]) -> Row:
    """A teammate's segments, replaced by `wanted` (the company's segment ids and `all`; [] clears them). Answers
    {member, segments}."""
    ref = _member_ref(c, member)
    ids: list[str] = []
    for sid in wanted:
        sid = str(sid or "").strip()
        if not sid:
            continue
        if sid != ALL:
            require_ours(c, sid)
        if sid not in ids:
            ids.append(sid)
    now = H.now()
    c.execute("DELETE FROM segment_members WHERE member=?", (ref,))
    for sid in ids:
        c.execute(
            "INSERT INTO segment_members (segment_id, member, created_at, created_by) VALUES (?,?,?,?)",
            (sid, ref, now, actor),
        )
    _event(c, actor, "segments.member", ref, {"segments": ids})
    return {"member": ref, "segments": member_segments(c, ref)}


def _managers(c: sqlite3.Connection) -> dict[str, str]:
    """{teammate ref: the ref of its manager, "" for none}: a bot's from its config, a person's from the roster."""
    bots = {bot: boss for bot, boss in c.execute("SELECT bot, reports_to FROM bot_config")}

    def boss_ref(text: object) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if text.startswith("human:"):
            return "person:" + text[len("human:") :]
        return "bot:" + text if text in bots else "person:" + text

    out = {"bot:" + bot: boss_ref(boss) for bot, boss in bots.items()}
    roster: Row = _roster(c) or {}
    for person in cast(list[Row], roster.get("people") or []):
        out["person:" + person["id"]] = boss_ref(person.get("reports_to"))
    return out


def _levels(parent: dict[str, str], key: str) -> int:
    """How many people and bots sit above `key` on the org chart: a group on the way up is not a level."""
    levels, seen, at = 0, {key}, parent.get(key, "")
    while at and at not in seen:
        seen.add(at)
        if not at.startswith("g:"):
            levels += 1
        at = parent.get(at, "")
    return levels


def _directory(c: sqlite3.Connection, who: "Identity | None" = None, auth: "Auth | None" = None) -> Directory:
    """What faces and members are listed with: each teammate on the org chart by its name, its depth on the chart (the
    people and bots above it), and the people the viewer may not see."""
    roster = _roster(c)
    configs = _entries(c)
    archived = {r[0] for r in c.execute("SELECT id FROM bots WHERE state='archived'")}
    chart = _org_view(roster, configs, archived)
    names: Directory = {}
    parent: dict[str, str] = {}
    for row in chart["people"]:
        names["person:" + row["id"]] = row.get("name") or row["id"]
        parent["p:" + row["id"]] = row.get("org_parent") or ""
    for row in chart["bots"]:
        names["bot:" + row["id"]] = row.get("display_name") or row["id"]
        parent["b:" + row["id"]] = row.get("org_parent") or ""
    for row in chart["org_groups"]:
        parent["g:" + row["id"]] = row.get("org_parent") or ""
    depth: dict[str, int] = {}
    for ref in names:
        key = ("p:" if ref.startswith("person:") else "b:") + ref.split(":", 1)[1]
        depth[ref] = _levels(parent, key)
    hidden: set[str] = set()
    if who is not None and auth is not None:
        people_hidden, _groups = _unseen(c, who, auth)
        hidden = {"person:" + pid for pid in people_hidden}
    return {"names": names, "depth": depth, "hidden": hidden}


def _faces_of(refs: list[str], directory: Directory) -> list[str]:
    """The teammates of a list that are on the chart and that the viewer may see, highest on the chart first."""
    live = [r for r in refs if r in directory["names"] and r not in directory["hidden"]]
    live.sort(key=lambda r: (directory["depth"].get(r, 99), directory["names"][r].lower(), r))
    return live


def _face(ref: str, directory: Directory) -> Row:
    return {"ref": ref, "name": directory["names"][ref], "kind": "bot" if ref.startswith("bot:") else "person"}


def leads(c: sqlite3.Connection, segment_id: str) -> list[str]:
    """The segment's leads, highest on the chart first: its dedicated teammates whose manager is not dedicated to it
    too. They see the segment's P&L line; their reports see its budget and goals."""
    dedicated = set(_dedicated(c, segment_id))
    managers = _managers(c)
    return _faces_of([r for r in dedicated if managers.get(r, "") not in dedicated], _directory(c))


def members(c: sqlite3.Connection, who: "Identity | None" = None, auth: "Auth | None" = None) -> Row:
    """GET /api/v2/segments/members: every teammate tagged with one of the company's segments or `all`, highest on the
    chart first, the viewer's hidden people left out. `truncated` says the list was cut at MEMBERS_MAX."""
    ours = ours_ids(c)
    rows: dict[str, list[str]] = {}
    for ref, sid in c.execute("SELECT member, segment_id FROM segment_members ORDER BY member, segment_id"):
        if sid == ALL or sid in ours:
            rows.setdefault(ref, []).append(sid)
    directory = _directory(c, who, auth)
    live = _faces_of(list(rows), directory)
    return {
        "members": [{"member": ref, "segments": rows[ref]} for ref in live[:MEMBERS_MAX]],
        "truncated": len(live) > MEMBERS_MAX,
    }


# ----------------------------------------------------------------------------- the segment's strategy
def strategy_view(c: sqlite3.Connection, segment_id: str, editable: bool) -> Row:
    """The segment's strategy field by field: the summary and the seven fixed sections, each with the text the segment
    reads (its own where it changed one, else the company's), the company's text and whether it changed. `editable`
    (the owner) reads the draft; anyone else the published version."""
    from . import strategy as ST

    source = _draft(c) if editable else _current(c)
    if not source:
        return {"draft": editable, "revision": None, "version": None, "fields": []}
    own = source["sections"]["segments"].get(segment_id, {})
    company = {"summary": source["summary"], **{k: source["sections"][k] for k in ST.FIXED_KEYS}}
    fields = [
        {
            "key": key,
            "title": "Summary" if key == "summary" else ST.TITLES[key],
            "text": own[key] if key in own else company[key],
            "company": company[key],
            "changed": key in own,
        }
        for key in ST.SEGMENT_KEYS
    ]
    return {
        "draft": editable,
        "revision": source["revision"] if editable else None,
        "version": None if editable else source["n"],
        "fields": fields,
    }


def write_strategy(
    c: sqlite3.Connection,
    actor: str,
    segment_id: str,
    revision: int | None,
    changes: dict[str, str],
) -> Row:
    """The segment's own text for some sections in the draft (`""` gives the company's text back), against the draft
    revision the page read. Answers the strategy view."""
    _ours_row(c, segment_id)
    _update_draft(c, actor, revision, {"segments": {segment_id: dict(changes)}})
    return strategy_view(c, segment_id, True)


def _bets_of(c: sqlite3.Connection, segment_id: str, editable: bool) -> list[Row]:
    rows = _bets(c) if editable else list((_current(c) or {}).get("bets") or [])
    return [
        {"id": b["id"], "statement": b["statement"], "status": b["status"]}
        for b in rows
        if b.get("segment_id") == segment_id and b.get("status") != "retired"
    ]


def detail(c: sqlite3.Connection, auth: "Auth", who: "Identity", segment_id: str, editable: bool) -> Row:
    """GET /api/v2/segments/{id}: the segment (as the list shows it), its leads, its strategy and its live bets."""
    row = _ours_row(c, segment_id)
    directory = _directory(c, who, auth)
    dedicated = set(_dedicated(c, segment_id))
    managers = _managers(c)
    live = _faces_of(list(dedicated), directory)
    lead_refs = [r for r in live if managers.get(r, "") not in dedicated]
    index = [r["id"] for r in _ordered(c)].index(segment_id)
    return {
        **_segment_view(row, index, [_face(r, directory) for r in live], len(live)),
        "leads": [_face(r, directory) for r in lead_refs],
        "strategy": strategy_view(c, segment_id, editable),
        "bets": _bets_of(c, segment_id, editable),
    }


# ----------------------------------------------------------------------------- what a run is handed
def run_text(c: sqlite3.Connection, bot: str | None) -> str:
    """What a bot's run is handed about the segments it is dedicated to (backend/learned.py for_turn): each one's name,
    its one line, and the parts of its strategy the segment changed, from the published version. A bot tagged `all`
    is handed every segment. "" when there is no published version or no segment."""
    from . import strategy as ST

    published = _current(c)
    if not published:
        return ""
    mine = member_segments(c, "bot:" + str(bot or ""))
    if not mine:
        return ""
    own = published["sections"]["segments"]
    blocks: list[str] = []
    for row in _ordered(c):
        if ALL not in mine and row["id"] not in mine:
            continue
        lines = [f"Segment: {row['name']}" + (f" ({row['summary']})" if row["summary"] else "")]
        for key in ST.SEGMENT_KEYS:
            text = own.get(row["id"], {}).get(key)
            if text:
                lines.append(f"{'Summary' if key == 'summary' else ST.TITLES[key]}: {text}")
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    return text if len(text) <= RUN_MAX else text[: RUN_MAX - 1].rstrip() + "…"


# ----------------------------------------------------------------------------- HTTP
def install(app: "FastAPI", store: "Store", auth: "Auth", mutate: Callable[..., Any]) -> None:
    def reader(who: "Identity") -> None:
        _domain(auth, who)

    def owner(who: "Identity") -> None:
        """The owner on their own click: a bot proposes a segment change and the owner confirms it."""
        _domain(auth, who)
        if _company_editor(who):
            return
        if who.role == "bot":
            raise Problem("forbidden", "A bot proposes a change to the segments and the owner confirms it", 403)
        raise Problem("forbidden", "Only the owner changes the segments, on their own click", 403)

    def tagger(who: "Identity") -> None:
        _domain(auth, who)
        if who.role == "owner" or _bot_admin(auth, who):
            return
        raise Problem("forbidden", "Only an owner or an admin tags a teammate with a segment", 403)

    def company() -> str:
        return _company_name(store)

    # The literal routes first: a path parameter below would take them for a segment id.
    @app.get("/api/v2/segments/members")
    def segment_members_list(request: Request):
        """Every teammate tagged with a segment (`segment_id` the company's or `all`), highest on the chart first."""
        who: "Identity" = request.state.identity
        reader(who)
        with _read_conn(store) as c:
            return members(c, who, auth)

    @app.post("/api/v2/segments/members")
    def segment_members_set(request: Request, body: M.SegmentMembers):
        """A teammate's segments, replaced by the ones sent ([] clears them): the company's segment ids and `all`."""
        who: "Identity" = request.state.identity

        def work(c: sqlite3.Connection):
            tagger(who)
            return set_member(c, who.actor, body.member, body.segments)

        return mutate(request, body, work)

    @app.post("/api/v2/segments/order")
    def segment_order(request: Request, body: M.SegmentOrder):
        """The list order: every segment the company plays in, once each. Answers the list."""
        who: "Identity" = request.state.identity

        def work(c: sqlite3.Connection):
            owner(who)
            reorder(c, who.actor, body.ids)
            return {"segments": listing(c, auth, who)}

        return mutate(request, body, work)

    @app.get("/api/v2/segments")
    def segment_list(request: Request):
        """The segments the company plays in, in list order: id, name, who, color, rank, faces (the dedicated teammates,
        highest on the chart first, at most FACES_MAX) and teammates (how many are dedicated)."""
        who: "Identity" = request.state.identity
        reader(who)
        with _read_conn(store) as c:
            return {"segments": listing(c, auth, who)}

    @app.post("/api/v2/segments")
    def segment_create(request: Request, body: M.SegmentCreate):
        """A segment the company plays in. Answers {segment}."""
        who: "Identity" = request.state.identity

        def work(c: sqlite3.Connection):
            owner(who)
            return {"segment": create(c, who.actor, body.name, body.who, body.color, company())}

        return mutate(request, body, work)

    @app.post("/api/v2/segments/{segment_id:path}/remove")
    def segment_remove(request: Request, segment_id: str, body: M.Empty):
        """Retire the segment: it is no longer one of the company's. Answers {id, removed}."""
        who: "Identity" = request.state.identity

        def work(c: sqlite3.Connection):
            owner(who)
            return remove(c, who.actor, segment_id)

        return mutate(request, body, work)

    @app.put("/api/v2/segments/{segment_id:path}/strategy")
    def segment_strategy_write(request: Request, segment_id: str, body: M.SegmentStrategy):
        """The segment's own text for the sections sent ("" gives the company's back), in the draft. Answers {strategy}."""
        who: "Identity" = request.state.identity

        def work(c: sqlite3.Connection):
            owner(who)
            return {"strategy": write_strategy(c, who.actor, segment_id, body.revision, body.sections)}

        return mutate(request, body, work)

    @app.post("/api/v2/segments/{segment_id:path}")
    def segment_update(request: Request, segment_id: str, body: M.SegmentUpdate):
        """Its name, who it is for and its color: only what is sent changes. Answers {segment}."""
        who: "Identity" = request.state.identity

        def work(c: sqlite3.Connection):
            owner(who)
            return {"segment": update(c, who.actor, segment_id, body.name, body.who, body.color)}

        return mutate(request, body, work)

    @app.get("/api/v2/segments/{segment_id:path}")
    def segment_show(request: Request, segment_id: str):
        """One segment: as the list shows it, its leads, its strategy (the draft for the owner, the published text
        for everyone else) and its live bets."""
        who: "Identity" = request.state.identity
        reader(who)
        with _read_conn(store) as c:
            return detail(c, auth, who, segment_id, editable=_company_editor(who))
