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

from fastapi import Request

from . import hubdb as H
from . import market as MK
from . import market_spend as MS
from . import models as M
from . import people as P
from . import strategy_market as SM
from .store import Problem

ALL = "all"  # a teammate tag: works across every segment
COLORS = ("blue", "green", "amber", "red", "purple", "teal", "pink", "gray")
NAME_MAX = 120
WHO_MAX = 200  # one line
FACES_MAX = 24  # the faces a segment shows; the count of its dedicated teammates says how many there are
SEGMENTS_MAX = 50
MEMBERS_MAX = 500
RUN_MAX = 12_000  # characters of segment text a run is handed


def ours_ids(c):
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


def require_ours(c, segment_id):
    """`segment_id` normalised: "" (no segment) stays "", anything else must be one of the company's segments."""
    sid = str(segment_id or "").strip()
    if sid and sid not in ours_ids(c):
        raise Problem("not_found", f"{sid} is not one of the company's segments", 404)
    return sid


# ----------------------------------------------------------------------------- the segments
def _props(row):
    return dict(row.get("properties") or {})


def _rank(row):
    value = _props(row).get("rank")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _color(row, index):
    """The segment's color, or the one its place in the list gets while it has none."""
    stored = _props(row).get("color")
    return stored if stored in COLORS else COLORS[index % len(COLORS)]


def _ordered(c):
    """The company's segments in list order: their rank, then their name."""
    rows = [MK.entity(c, sid) for sid in ours_ids(c)]
    rows = [r for r in rows if r and r["type"] == "segment" and r["status"] == "active"]
    rows.sort(key=lambda r: (_rank(r) is None, _rank(r) or 0, r["name"].lower(), r["id"]))
    return rows


def _ours_row(c, segment_id):
    sid = require_ours(c, segment_id)
    if not sid:
        raise Problem("not_found", "There is no segment with an empty id", 404)
    return MK.entity(c, sid)


def _text(text, limit, label, required=False):
    text = " ".join(str(text or "").split())
    if required and not text:
        raise Problem("validation", f"Give the segment {label}", 422)
    if len(text) > limit:
        raise Problem("validation", f"Keep the segment {label} under {limit} characters", 422)
    return text


def _segment_view(row, index, faces, teammates):
    return {
        "id": row["id"],
        "name": row["name"],
        "who": row["summary"],
        "color": _color(row, index),
        "rank": index,
        "faces": faces[:FACES_MAX],
        "teammates": teammates,
    }


def _view_of(c, segment_id):
    """One segment as the list shows it, read with no viewer's hiding (the owner's own writes)."""
    ordered = [r["id"] for r in _ordered(c)]
    directory = _directory(c)
    tagged = _dedicated(c, segment_id)
    live = _faces_of(tagged, directory)
    row = MK.entity(c, segment_id)
    return _segment_view(row, ordered.index(segment_id), [_face(r, directory) for r in live], len(live))


def listing(c, auth, who):
    """GET /api/v2/segments: every segment the company plays in, in list order, each with its dedicated teammates
    (faces) highest on the org chart first. A teammate the viewer may not see is left out, and counted out."""
    directory = _directory(c, who, auth)
    tagged = _tagged(c)
    out = []
    for index, row in enumerate(_ordered(c)[:SEGMENTS_MAX]):
        live = _faces_of(tagged.get(row["id"], []), directory)
        out.append(_segment_view(row, index, [_face(r, directory) for r in live], len(live)))
    return out


def create(c, actor, name, who="", color=None, company=""):
    """A segment the company plays in: a new market segment under the market's root, or an existing one of that name
    joins the company's, or a removed one comes back with its id and its history. Its color is the first one no
    other segment has."""
    name = _text(name, NAME_MAX, "name", required=True)
    who = _text(who, WHO_MAX, "one line of who it is for")
    seg_id = "segment/" + MK.slug(name)
    if seg_id == "segment/":
        raise Problem("validation", "Give the segment a name with letters or digits", 422)
    ours = _ordered(c)
    used = {_color(r, i) for i, r in enumerate(ours)}
    chosen = color or next((x for x in COLORS if x not in used), COLORS[len(ours) % len(COLORS)])
    root = (MS.definition(c) or {}).get("root")
    today = H.now()[:10]
    ev = SM.owner_evidence(c, actor)
    existing = MK.entity(c, seg_id)
    if existing and existing["type"] != "segment":
        raise Problem("exists", f"{seg_id} is not a segment", 409, extra={"id": seg_id})
    if existing and existing["status"] == "active" and seg_id in ours_ids(c):
        raise Problem("exists", f"{name} is already one of the company's segments", 409, extra={"id": seg_id})
    if existing:
        kept = _props(existing).get("color")
        MK.update_entity(
            c,
            actor,
            seg_id,
            status="active",
            summary=who or existing["summary"],
            properties={**_props(existing), "color": kept if kept in COLORS else chosen, "rank": len(ours)},
            evidence_ids=ev,
        )
    else:
        MK.create_entity(
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
        MK.create_edge(c, actor, src=seg_id, rel="member_of", dst=root, since=today, evidence_ids=ev)
    SM.set_ours(c, actor, seg_id, True, company)
    return _view_of(c, seg_id)


def update(c, actor, segment_id, name=None, who=None, color=None):
    """Its name, who it is for (`""` clears it) and color: only what is sent changes."""
    row = _ours_row(c, segment_id)
    changes = {}
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
        MK.update_entity(c, actor, segment_id, evidence_ids=SM.owner_evidence(c, actor), **changes)
    return _view_of(c, segment_id)


def reorder(c, actor, ids):
    """The list order: `ids` names every segment the company plays in, once each."""
    current = [r["id"] for r in _ordered(c)]
    if len(set(ids)) != len(ids) or sorted(ids) != sorted(current):
        raise Problem("validation", "List every segment the company plays in, once each", 422)
    ev = None
    for index, sid in enumerate(ids):
        row = MK.entity(c, sid)
        if _rank(row) != index:
            ev = ev or SM.owner_evidence(c, actor)
            MK.update_entity(c, actor, sid, properties={**_props(row), "rank": index}, evidence_ids=ev)


def remove(c, actor, segment_id):
    """Retire the segment (its sub-segments move up to its parent, backend/strategy_market.py). Its teammate tags stay."""
    _ours_row(c, segment_id)
    return SM.remove_segment(c, actor, segment_id)


# ----------------------------------------------------------------------------- teammates
def _tagged(c):
    """{segment id: [teammate refs]} for the company's segments, `all` left out."""
    ours = ours_ids(c)
    out = {}
    for sid, ref in c.execute("SELECT segment_id, member FROM segment_members ORDER BY segment_id, member"):
        if sid != ALL and sid in ours:
            out.setdefault(sid, []).append(ref)
    return out


def _dedicated(c, segment_id):
    """The teammate refs tagged with this segment (not `all`), whether or not the viewer may see them."""
    return [
        r[0] for r in c.execute("SELECT member FROM segment_members WHERE segment_id=? ORDER BY member", (segment_id,))
    ]


def member_segments(c, ref):
    """The segments a teammate (`bot:<id>` or `person:<id>`) is tagged with: the company's segment ids, and `all` as the
    string `all`. A tag on a segment that is not the company's is kept and not returned."""
    ours = ours_ids(c)
    return [
        r[0]
        for r in c.execute("SELECT segment_id FROM segment_members WHERE member=? ORDER BY segment_id", (ref,))
        if r[0] == ALL or r[0] in ours
    ]


def _member_ref(c, text):
    """A teammate as the tags name it: `bot:<id>` (a bot's id, however it was named) or `person:<id>`."""
    from . import views

    kind, _, ident = str(text or "").strip().partition(":")
    kind = "person" if kind == "human" else kind
    if kind == "bot":
        bot = H.resolve_bot(c, ident)
        if not bot:
            raise Problem("not_found", f"There is no bot {ident}", 404)
        return "bot:" + bot["id"]
    if kind == "person":
        if not P.person(ident, views.roster(c)):
            raise Problem("not_found", f"There is no person {ident}", 404)
        return "person:" + ident
    raise Problem("validation", "A teammate is bot:<id> or person:<id>", 422)


def set_member(c, actor, member, wanted):
    """A teammate's segments, replaced by `wanted` (the company's segment ids and `all`; [] clears them). Answers
    {member, segments}."""
    ref = _member_ref(c, member)
    ids = []
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
    H.event(c, actor, "segments.member", ref, {"segments": ids})
    return {"member": ref, "segments": member_segments(c, ref)}


def _managers(c):
    """{teammate ref: the ref of its manager, "" for none}: a bot's from its config, a person's from the roster."""
    from . import views

    bots = {bot: boss for bot, boss in c.execute("SELECT bot, reports_to FROM bot_config")}

    def boss_ref(text):
        text = str(text or "").strip()
        if not text:
            return ""
        if text.startswith("human:"):
            return "person:" + text[len("human:") :]
        return "bot:" + text if text in bots else "person:" + text

    out = {"bot:" + bot: boss_ref(boss) for bot, boss in bots.items()}
    for person in (views.roster(c) or {}).get("people") or []:
        out["person:" + person["id"]] = boss_ref(person.get("reports_to"))
    return out


def _levels(parent, key):
    """How many people and bots sit above `key` on the org chart: a group on the way up is not a level."""
    levels, seen, at = 0, {key}, parent.get(key, "")
    while at and at not in seen:
        seen.add(at)
        if not at.startswith("g:"):
            levels += 1
        at = parent.get(at, "")
    return levels


def _directory(c, who=None, auth=None):
    """What faces and members are listed with: each teammate on the org chart by its name, its depth on the chart (the
    people and bots above it), and the people the viewer may not see."""
    from . import teammate_access, views

    roster = views.roster(c)
    configs = views.entries(c)
    archived = {r[0] for r in c.execute("SELECT id FROM bots WHERE state='archived'")}
    chart = P.org_view(roster, configs, archived, bare_people=True)
    names, parent = {}, {}
    for row in chart["people"]:
        names["person:" + row["id"]] = row.get("name") or row["id"]
        parent["p:" + row["id"]] = row.get("org_parent") or ""
    for row in chart["bots"]:
        names["bot:" + row["id"]] = row.get("display_name") or row["id"]
        parent["b:" + row["id"]] = row.get("org_parent") or ""
    for row in chart["org_groups"]:
        parent["g:" + row["id"]] = row.get("org_parent") or ""
    depth = {}
    for ref in names:
        key = ("p:" if ref.startswith("person:") else "b:") + ref.split(":", 1)[1]
        depth[ref] = _levels(parent, key)
    hidden = set()
    if who is not None and auth is not None:
        people_hidden, _groups = teammate_access.unseen(c, who, auth)
        hidden = {"person:" + pid for pid in people_hidden}
    return {"names": names, "depth": depth, "hidden": hidden}


def _faces_of(refs, directory):
    """The teammates of a list that are on the chart and that the viewer may see, highest on the chart first."""
    live = [r for r in refs if r in directory["names"] and r not in directory["hidden"]]
    live.sort(key=lambda r: (directory["depth"].get(r, 99), directory["names"][r].lower(), r))
    return live


def _face(ref, directory):
    return {"ref": ref, "name": directory["names"][ref], "kind": "bot" if ref.startswith("bot:") else "person"}


def leads(c, segment_id):
    """The segment's leads, highest on the chart first: its dedicated teammates whose manager is not dedicated to it
    too. They see the segment's P&L line; their reports see its budget and goals."""
    dedicated = set(_dedicated(c, segment_id))
    managers = _managers(c)
    return _faces_of([r for r in dedicated if managers.get(r, "") not in dedicated], _directory(c))


def members(c, who=None, auth=None):
    """GET /api/v2/segments/members: every teammate tagged with one of the company's segments or `all`, highest on the
    chart first, the viewer's hidden people left out. `truncated` says the list was cut at MEMBERS_MAX."""
    ours = ours_ids(c)
    rows = {}
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
def strategy_view(c, segment_id, editable):
    """The segment's strategy field by field: the summary and the seven fixed sections, each with the text the segment
    reads (its own where it changed one, else the company's), the company's text and whether it changed. `editable`
    (the owner) reads the draft; anyone else the published version."""
    from . import strategy as ST

    source = ST.draft(c) if editable else ST.current(c)
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


def write_strategy(c, actor, segment_id, revision, changes):
    """The segment's own text for some sections in the draft (`""` gives the company's text back), against the draft
    revision the page read. Answers the strategy view."""
    from . import strategy as ST

    _ours_row(c, segment_id)
    ST.update_draft(c, actor, revision, sections={"segments": {segment_id: dict(changes)}})
    return strategy_view(c, segment_id, True)


def _bets_of(c, segment_id, editable):
    from . import strategy as ST

    rows = ST.bets(c) if editable else list((ST.current(c) or {}).get("bets") or [])
    return [
        {"id": b["id"], "statement": b["statement"], "status": b["status"]}
        for b in rows
        if b.get("segment_id") == segment_id and b.get("status") != "retired"
    ]


def detail(c, auth, who, segment_id, editable):
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
def run_text(c, bot):
    """What a bot's run is handed about the segments it is dedicated to (backend/learned.py for_turn): each one's name,
    its one line, and the parts of its strategy the segment changed, from the published version. A bot tagged `all`
    is handed every segment. "" when there is no published version or no segment."""
    from . import strategy as ST

    published = ST.current(c)
    if not published:
        return ""
    mine = member_segments(c, "bot:" + str(bot or ""))
    if not mine:
        return ""
    own = published["sections"]["segments"]
    blocks = []
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
def install(app, store, auth, mutate):
    from . import goal_routes

    def reader(who):
        auth.domain(who)

    def owner(who):
        """The owner on their own click: a bot proposes a segment change and the owner confirms it."""
        auth.domain(who)
        if goal_routes.company_editor(who):
            return
        if who.role == "bot":
            raise Problem("forbidden", "A bot proposes a change to the segments and the owner confirms it", 403)
        raise Problem("forbidden", "Only the owner changes the segments, on their own click", 403)

    def tagger(who):
        auth.domain(who)
        if who.role == "owner" or auth.bot_admin(who):
            return
        raise Problem("forbidden", "Only an owner or an admin tags a teammate with a segment", 403)

    def company():
        return str(getattr(store.settings, "company_name", "") or "").strip()

    # The literal routes first: a path parameter below would take them for a segment id.
    @app.get("/api/v2/segments/members")
    def segment_members_list(request: Request):
        """Every teammate tagged with a segment (`segment_id` the company's or `all`), highest on the chart first."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return members(c, who, auth)

    @app.post("/api/v2/segments/members")
    def segment_members_set(request: Request, body: M.SegmentMembers):
        """A teammate's segments, replaced by the ones sent ([] clears them): the company's segment ids and `all`."""
        who = request.state.identity

        def work(c):
            tagger(who)
            return set_member(c, who.actor, body.member, body.segments)

        return mutate(request, body, work)

    @app.post("/api/v2/segments/order")
    def segment_order(request: Request, body: M.SegmentOrder):
        """The list order: every segment the company plays in, once each. Answers the list."""
        who = request.state.identity

        def work(c):
            owner(who)
            reorder(c, who.actor, body.ids)
            return {"segments": listing(c, auth, who)}

        return mutate(request, body, work)

    @app.get("/api/v2/segments")
    def segment_list(request: Request):
        """The segments the company plays in, in list order: id, name, who, color, rank, faces (the dedicated teammates,
        highest on the chart first, at most FACES_MAX) and teammates (how many are dedicated)."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return {"segments": listing(c, auth, who)}

    @app.post("/api/v2/segments")
    def segment_create(request: Request, body: M.SegmentCreate):
        """A segment the company plays in. Answers {segment}."""
        who = request.state.identity

        def work(c):
            owner(who)
            return {"segment": create(c, who.actor, body.name, body.who, body.color, company())}

        return mutate(request, body, work)

    @app.post("/api/v2/segments/{segment_id:path}/remove")
    def segment_remove(request: Request, segment_id: str, body: M.Empty):
        """Retire the segment: it is no longer one of the company's. Answers {id, removed}."""
        who = request.state.identity

        def work(c):
            owner(who)
            return remove(c, who.actor, segment_id)

        return mutate(request, body, work)

    @app.put("/api/v2/segments/{segment_id:path}/strategy")
    def segment_strategy_write(request: Request, segment_id: str, body: M.SegmentStrategy):
        """The segment's own text for the sections sent ("" gives the company's back), in the draft. Answers {strategy}."""
        who = request.state.identity

        def work(c):
            owner(who)
            return {"strategy": write_strategy(c, who.actor, segment_id, body.revision, body.sections)}

        return mutate(request, body, work)

    @app.post("/api/v2/segments/{segment_id:path}")
    def segment_update(request: Request, segment_id: str, body: M.SegmentUpdate):
        """Its name, who it is for and its color: only what is sent changes. Answers {segment}."""
        who = request.state.identity

        def work(c):
            owner(who)
            return {"segment": update(c, who.actor, segment_id, body.name, body.who, body.color)}

        return mutate(request, body, work)

    @app.get("/api/v2/segments/{segment_id:path}")
    def segment_show(request: Request, segment_id: str):
        """One segment: as the list shows it, its leads, its strategy (the draft for the owner, the published text
        for everyone else) and its live bets."""
        who = request.state.identity
        reader(who)
        with store.read() as c:
            return detail(c, auth, who, segment_id, editable=goal_routes.company_editor(who))
