"""Goals: what every person and bot on the org chart is for, and how it says it is going.

A goal has an owner (a person, a bot, or `company`; one field), the goal it serves (`parent_id`,
optional: a goal with none is simply not linked), and a colour. What level a goal is at comes from
its owner: `company` is a company goal, `human:x` a person's, `bot:x` a bot's. A company goal is
optional. Bots read theirs with `hub goal list`; nothing is pushed into a run.

A goal links to KPIs (backend/kpis.py) and its colour is set automatically from them, by the Goal
Manager, or from its owner's check-ins and its tasks when it has none. A person may override that:
the colour they set sticks (`status_source` is `person`, with their note) until a person hands it
back, and the Strategy Bot may only *suggest* a different one. Every colour is stored with who set
it (`status_by`) and how (`status_source`, auto or person), in `goals` and in `goal_events`.

Rows are appended, never deleted: `dropped` is a status. Every change leaves a `goal_events` row, as
`task_events` does for tasks. Changes to a definition or a target that the Strategy Bot wants are
proposals (`goal_proposals`) that the goal's or KPI's owner confirms.
"""

from datetime import datetime, timedelta, timezone

from .batch_work import isolated
from . import strategy_bot
from . import hubdb as H
from . import kpis as K
from . import people as P

COLOURS = ("red", "yellow", "green")
STATUSES = COLOURS + ("done", "dropped")
AUTO_STATUSES = COLOURS + ("gray",)       # what the arithmetic may say; gray is no data
LIVE = ("red", "yellow", "green")         # statuses a goal is still worked under; None is unscored
COMPANY = "company"                       # the owner of a company goal
GOAL_MANAGER = "goal-manager"             # the built-in bot that keeps the KPIs and sets the automatic colours
AUTO_ACTOR = "bot:" + GOAL_MANAGER        # who an automatic status says set it
SIGNALS = ("on_track", "at_risk", "off_track")
SIGNAL_COLOUR = {"on_track": "green", "at_risk": "yellow", "off_track": "red"}
CHECKIN_DAYS = 21                         # a check-in colours a goal for three weeks
TASK_DAYS = 14                            # no task done for two weeks, on a goal at least that old: yellow
SOURCES = ("measured", "estimate")        # legacy names of a reading's quality; a connector name is a source
# goal_why: the Strategy Bot's one line tying a goal to the company's vision (backend/company.py). recovery: the
# Strategy Bot's bot-task changes to get a red or stalled goal back on track, at level 2 (backend/strategy_suggest.py).
# strategy_draft: a change to the team's strategy draft and its bets, from any bot, the owner's to confirm.
# strategy_realign: the goals of bets a publish retired or rewrote, filed by Tico for the owner (backend/strategy.py).
# brand_draft, gtm_draft: sections for the brand or GTM draft from any bot, an admin's to confirm (backend/brand_gtm.py).
PROPOSALS = ("goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag", "goal_why", "recovery",
             "strategy_draft", "strategy_realign", "brand_draft", "gtm_draft")
FLAGS = ("vague", "duplicate")


# ----------------------------------------------------------------------------- reads
def goal(conn, goal_id):
    return H._one(conn, "SELECT * FROM goals WHERE id=?", (goal_id,))


def goals(conn, owner=None, parent_id=None, status=None, live_only=False):
    """Goals, filtered. `status` is one status or a tuple; `live_only` leaves out done and dropped."""
    where, args = [], []
    if owner:
        where.append("owner_id=?")
        args.append(owner)
    if parent_id is not None:
        where.append("parent_id IS NULL" if parent_id == "" else "parent_id=?")
        if parent_id != "":
            args.append(parent_id)
    if status:
        statuses = (status,) if isinstance(status, str) else tuple(status)
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        args.extend(statuses)
    if live_only:
        where.append("(status IS NULL OR status NOT IN ('done','dropped'))")
    sql = "SELECT * FROM goals" + (" WHERE " + " AND ".join(where) if where else "")
    return H._rows(conn.execute(sql + " ORDER BY owner_id, rank, created_at", args))


def chain(conn, goal_id):
    """The goal's parents, nearest first, up to the top one. Cycles stop at the repeat."""
    out, seen, row = [], {goal_id}, goal(conn, goal_id)
    while row and row.get("parent_id") and row["parent_id"] not in seen:
        seen.add(row["parent_id"])
        row = goal(conn, row["parent_id"])
        if row:
            out.append(row)
    return out


def children(conn, goal_id):
    return H._rows(conn.execute("SELECT * FROM goals WHERE parent_id=? ORDER BY owner_id, rank, created_at", (goal_id,)))


def history(conn, goal_id):
    return H._rows(conn.execute("SELECT * FROM goal_events WHERE goal_id=? ORDER BY created_at", (goal_id,)))


def kpis(conn, goal_id):
    """The goal's KPIs: each one's fields and latest reading, its link with the target, and its status."""
    return K.goal_views(conn, [goal_id], include_archived=True)[goal_id]


def kpi(conn, kpi_id):
    return K.kpi(conn, kpi_id)


def readings(conn, kpi_id, limit=500):
    return K.readings(conn, kpi_id, limit=limit)


def tasks_of(conn, goal_id, visible="coalesce(private,1)=0"):
    return H._rows(conn.execute("SELECT id,title,owner_id,requester_id,status,updated_at FROM tasks WHERE goal_id=? "
                                "AND (" + visible + ") ORDER BY status, updated_at DESC", (goal_id,)))


def checkins(conn, goal_id, limit=50):
    """The owner's own words about how it is going, newest first."""
    return H._rows(conn.execute("SELECT * FROM goal_checkins WHERE goal_id=? ORDER BY created_at DESC LIMIT ?",
                                (goal_id, limit)))


def proposals(conn, status="pending", goal_id=None, kpi_id=None):
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if goal_id:
        where.append("goal_id=?")
        args.append(goal_id)
    if kpi_id:
        where.append("kpi_id=?")
        args.append(kpi_id)
    return [_proposal_view(r, conn) for r in H._rows(conn.execute(
        "SELECT * FROM goal_proposals" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY proposed_at", args))]


def _proposal_view(row, conn=None):
    if row:
        row["payload"] = H._json(row.pop("payload"), {}) or {}
        row["result"] = H._json(row.pop("result"), None)
        if conn is not None and row["kind"] in ("strategy_draft", "strategy_realign"):
            from . import strategy
            strategy.card_view(conn, row)
    return row


def view(conn, row, visible="coalesce(private,1)=0"):
    """One goal with everything the page and `hub goal show` print."""
    return {**row, "kpis": kpis(conn, row["id"]), "children": children(conn, row["id"]),
            "chain": chain(conn, row["id"]), "tasks": tasks_of(conn, row["id"], visible),
            "events": history(conn, row["id"]), "checkins": checkins(conn, row["id"], 10),
            "proposals": proposals(conn, "pending", goal_id=row["id"])}


def for_actor(conn, actor, roster=None, entries=None, archived=()):
    """`hub goal list`: the actor's own goals in rank order, the chain above each, and the goals of
    whoever reports to it. That is the whole of what a bot needs to know what it is for."""
    mine = goals(conn, owner=actor, live_only=True)
    above, seen = [], {g["id"] for g in mine}
    for g in mine:
        for parent in chain(conn, g["id"]):
            if parent["id"] not in seen:
                seen.add(parent["id"])
                above.append(parent)
    below = []
    for report in reports_of(actor, roster, entries, archived, True):
        below.extend(goals(conn, owner=report, live_only=True))
    company = goals(conn, owner=COMPANY, live_only=True) if not mine and not above else []
    return {"owner": actor, "goals": mine, "chain": above, "reports": below, "company": company}


# ----------------------------------------------------------------------------- the org chart
def _key(actor):
    kind = H.actor_kind(actor)
    return ("b:" if kind == "bot" else "p:") + H.actor_id(actor) if kind in ("bot", "human") else ""


def _actor(key):
    head, _, rest = str(key or "").partition(":")
    return H.bot_actor(rest) if head == "b" else H.human_actor(rest) if head == "p" else None


def above(actor, roster, entries, archived=(), bare_people=False):
    """Actors above this one on the org chart, nearest first: the same walk as
    `people.manages`, over people and bots alike. Department groups are stepped through. `bare_people`: the redesign's
    switch (backend/people.py `org_parent`)."""
    kind = H.actor_kind(actor)
    if kind not in ("bot", "human"):
        return []
    out, seen = [], set()
    parent = P.org_parent("bot" if kind == "bot" else "person", H.actor_id(actor), roster, entries, archived, bare_people)
    while parent and parent not in seen:
        seen.add(parent)
        head, _, rest = parent.partition(":")
        if head == "g":
            group = ((roster or {}).get("org_groups") or {}).get(rest) or {}
            boss = P._clean(group.get("reports_to"))
            parent = ("p:" + boss) if boss else ""
            continue
        out.append(_actor(parent))
        parent = P.org_parent("person" if head == "p" else "bot", rest, roster, entries, archived, bare_people)
    return out


def reports_of(actor, roster, entries, archived=(), bare_people=False):
    """Direct reports on the org chart: people whose boss this is, bots whose `reports_to` this
    is, and, for a person, the bots that hang under them or their department."""
    kind = H.actor_kind(actor)
    if kind not in ("bot", "human") or roster is None:
        return []
    mine = _key(actor)
    keys = {mine}
    if kind == "human":
        for gid, group in ((roster.get("org_groups") or {})).items():
            if P._clean(group.get("reports_to")) == H.actor_id(actor):
                keys.add("g:" + gid)
    out = []
    for p in P.visible_people(roster):
        if P.org_parent("person", p["id"], roster, entries, archived, bare_people) in keys:
            out.append(H.human_actor(p["id"]))
    for slug in entries or {}:
        if slug in archived:
            continue
        if P.org_parent("bot", slug, roster, entries, archived, bare_people) in keys:
            out.append(H.bot_actor(slug))
    return out


# ----------------------------------------------------------------------------- writes
def _event(conn, goal_id, actor, field, old, new, note="", status_by=None, status_source=None, ts=None):
    conn.execute("INSERT INTO goal_events (id, goal_id, created_at, actor, field, old, new, note, status_by, status_source) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (H.new_id(), goal_id, ts or H.now(), actor, field,
                  None if old is None else str(old), None if new is None else str(new), note or "",
                  status_by, status_source))


def _next_rank(conn, owner, top=False):
    row = conn.execute("SELECT min(rank), max(rank) FROM goals WHERE owner_id=? AND rank IS NOT NULL", (owner,)).fetchone()
    lowest, highest = row[0], row[1]
    if top:
        return (lowest - 1) if lowest is not None else 0
    return (highest + 1) if highest is not None else 0


def _bet(conn, actor, bet_id):
    """The strategy bet a goal serves (backend/strategy.py), checked to exist. A retired bet still counts: the goal
    keeps showing where it came from."""
    from . import strategy
    if not strategy.bet(conn, bet_id):
        H.refuse(conn, actor, "not-found", f"no strategy bet {bet_id}")
    return bet_id


def create(conn, actor, title, owner, parent_id=None, body="", top=False, bet_id=None):
    """A new goal starts uncoloured until its data supplies an automatic colour or a person sets one.
    A parent links it to the goal it supports; with none it is simply unlinked."""
    H._writer(conn, actor)
    title = str(title or "").strip()
    if not title:
        H.refuse(conn, actor, "lint", "give the goal a title that says what you are going for")
    if parent_id and not goal(conn, parent_id):
        H.refuse(conn, actor, "not-found", f"no goal {parent_id}")
    if parent_id and owner == COMPANY:
        H.refuse(conn, actor, "kind", "a company goal does not support another goal")
    bet_id = _bet(conn, actor, bet_id) if bet_id else None
    ts = H.now()
    row = {"id": H.new_id(), "title": title, "owner_id": owner, "parent_id": parent_id or None,
           "body": str(body or ""), "rank": _next_rank(conn, owner, top), "created_at": ts,
           "created_by_id": actor, "updated_at": ts, "bet_id": bet_id}
    conn.execute("INSERT INTO goals (id, title, owner_id, parent_id, body, status, status_note, rank, created_at, "
                 "created_by_id, updated_at, bet_id) VALUES (:id, :title, :owner_id, :parent_id, :body, NULL, '', :rank, "
                 ":created_at, :created_by_id, :updated_at, :bet_id)", row)
    _event(conn, row["id"], actor, "created", None, title)
    if bet_id:
        _event(conn, row["id"], actor, "bet_id", None, bet_id)
    H.event(conn, actor, "goal.create", row["id"], {"owner_id": owner, "title": title, "parent_id": parent_id})
    return goal(conn, row["id"])


def set_status(conn, actor, goal_id, status, note=""):
    """A colour set by hand: it sticks (`status_source` person) until somebody hands it back."""
    H._writer(conn, actor)
    row = goal(conn, goal_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no goal {goal_id}")
    if status not in STATUSES:
        H.refuse(conn, actor, "kind", f"a status is {'|'.join(STATUSES)}, not {status}")
    note = str(note or "").strip()
    if status in COLOURS and not note:
        H.refuse(conn, actor, "lint", "say in one sentence why it is " + status)
    ts = H.now()
    conn.execute("UPDATE goals SET status=?, status_note=?, status_by_id=?, status_at=?, status_source='person', "
                 "suggest_status=NULL, suggest_note=NULL, suggest_at=NULL, updated_at=?, "
                 "rank=CASE WHEN ? IN ('done','dropped') THEN NULL WHEN rank IS NULL THEN ? ELSE rank END "
                 "WHERE id=?", (status, note, actor, ts, ts, status, _next_rank(conn, row["owner_id"]), goal_id))
    # The event's time is the colour's `status_at`, which Undo on "Goal removed" puts back (`_before_drop`).
    _event(conn, goal_id, actor, "status", row["status"], status, note, status_by=actor, status_source="person", ts=ts)
    H.event(conn, actor, "goal.status", goal_id, {"status": status})
    if status == "dropped":
        # Nothing is deleted: the children move up to the goal this one served.
        for child in children(conn, goal_id):
            conn.execute("UPDATE goals SET parent_id=?, updated_at=? WHERE id=?", (row["parent_id"], ts, child["id"]))
            _event(conn, child["id"], actor, "parent_id", goal_id, row["parent_id"], f"{row['title']} was dropped")
    return goal(conn, goal_id)


def _before_finish(conn, goal_id):
    """The status a done or dropped goal had while it was live: its last change from a live status (or none) to done
    or dropped. None when it had no colour yet."""
    row = conn.execute("SELECT old FROM goal_events WHERE goal_id=? AND field='status' AND new IN ('done','dropped') "
                       "AND (old IS NULL OR old NOT IN ('done','dropped')) ORDER BY created_at DESC, rowid DESC LIMIT 1",
                       (goal_id,)).fetchone()
    return row[0] if row else None


def _before_drop(conn, goal_id):
    """The colour a dropped goal had by hand just before the drop, as (colour, note, setter, time), read from the
    history so Undo puts back what was there, not what a page says was there. A reopen's own `status` event carries
    its reason, so the colour it gave is read from the `status_restore` row it writes beside it. None when the colour
    was automatic or there was none (a proposal)."""
    drop = conn.execute("SELECT created_at, rowid FROM goal_events WHERE goal_id=? AND field='status' AND new='dropped' "
                        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (goal_id,)).fetchone()
    if not drop:
        return None
    prior = conn.execute("SELECT field, old, new, note, status_by, status_source, created_at FROM goal_events WHERE goal_id=? "
                         "AND field IN ('status','status_source','status_restore') "
                         "AND (created_at < ? OR (created_at = ? AND rowid < ?)) ORDER BY created_at DESC, rowid DESC LIMIT 1",
                         (goal_id, drop[0], drop[0], drop[1])).fetchone()
    if not prior or prior["field"] == "status_source" or prior["status_source"] != "person" or prior["new"] not in COLOURS:
        return None
    return prior["new"], prior["note"], prior["status_by"], prior["old"] if prior["field"] == "status_restore" else prior["created_at"]


def reopen(conn, actor, goal_id, status=None, note="", undo=False):
    """A done or dropped goal back in the live list. `status` is a colour, which sticks as one set by hand does, or
    `auto`: the colour it had is put back and the Strategy Bot works it out again (a goal that never had one gets it
    from its data, if any). The goals that moved up when it was dropped stay where they are. The history says it was
    reopened, and why. `undo` is the "Goal removed" toast's Undo: a dropped goal gets back the colour it had, with its
    note, setter and time when a person set it, read from the history; it takes no colour or note."""
    H._writer(conn, actor)
    row = goal(conn, goal_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no goal {goal_id}")
    if row["status"] not in ("done", "dropped"):
        H.refuse(conn, actor, "kind", "only a done or dropped goal is reopened")
    ts, rank = H.now(), _next_rank(conn, row["owner_id"])
    if undo:
        if row["status"] != "dropped":
            H.refuse(conn, actor, "kind", "Undo brings back a removed goal; a done goal is reopened with a reason")
        prior = _before_drop(conn, goal_id)
        if prior:
            (new, said, by, at), source = prior, "person"
        else:
            new, source, said, by, at = _before_finish(conn, goal_id), "auto", "", actor, ts
        why = "Undo remove"
    else:
        if status not in COLOURS + ("auto",):
            H.refuse(conn, actor, "kind", f"a reopened goal is {'|'.join(COLOURS)}|auto, not {status}")
        note = " ".join(str(note or "").split())
        if not note:
            H.refuse(conn, actor, "lint", "say in one line why it is reopened")
        if status == "auto":
            new, source, said = _before_finish(conn, goal_id), "auto", ""
        else:
            new, source, said = status, "person", note
        by, at, why = actor, ts, "Reopened: " + note
    conn.execute("UPDATE goals SET status=?, status_note=?, status_by_id=?, status_at=?, status_source=?, "
                 "suggest_status=NULL, suggest_note=NULL, suggest_at=NULL, updated_at=?, rank=? WHERE id=?",
                 (new, said, by, at, source, ts, rank, goal_id))
    _event(conn, goal_id, actor, "status", row["status"], new, why, status_by=by, status_source=source, ts=ts)
    _event(conn, goal_id, actor, "status_restore", at, new, said, status_by=by, status_source=source, ts=ts)
    H.event(conn, actor, "goal.reopen", goal_id, {"status": new, "source": source, "undo": bool(undo)})
    if source == "auto":
        apply_auto(conn, goal_id)
    return goal(conn, goal_id)


def update(conn, actor, goal_id, title=None, body=None, parent_id=None, owner=None, rank=None, top=False,
           bet_id=None):
    """Edit the words, the parent, the owner, the place in the owner's order, or the strategy bet it serves
    ("" unlinks)."""
    H._writer(conn, actor)
    row = goal(conn, goal_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no goal {goal_id}")
    sets, args = [], {}
    if title is not None:
        title = str(title).strip()
        if not title:
            H.refuse(conn, actor, "lint", "give the goal a title that says what you are going for")
        sets.append("title=:title")
        args["title"] = title
        _event(conn, goal_id, actor, "title", row["title"], title)
    if body is not None:
        sets.append("body=:body")
        args["body"] = str(body)
        _event(conn, goal_id, actor, "body", None, None, "body changed")
    if parent_id is not None:
        new_parent = parent_id or None
        if new_parent:
            if new_parent == goal_id:
                H.refuse(conn, actor, "kind", "a goal cannot serve itself")
            if not goal(conn, new_parent):
                H.refuse(conn, actor, "not-found", f"no goal {new_parent}")
            if goal_id in {g["id"] for g in chain(conn, new_parent)}:
                H.refuse(conn, actor, "kind", "a goal cannot serve one of its own children")
        if new_parent and (owner or row["owner_id"]) == COMPANY:
            H.refuse(conn, actor, "kind", "a company goal does not support another goal")
        sets.append("parent_id=:parent_id")
        args["parent_id"] = new_parent
        _event(conn, goal_id, actor, "parent_id", row["parent_id"], new_parent)
    if owner is not None and owner != row["owner_id"]:
        sets.append("owner_id=:owner_id")
        args["owner_id"] = owner
        if owner == COMPANY and (row["parent_id"] or args.get("parent_id")):
            # A company goal supports nothing.
            if "parent_id=:parent_id" not in sets:
                sets.append("parent_id=:parent_id")
                _event(conn, goal_id, actor, "parent_id", row["parent_id"], None)
            args["parent_id"] = None
        sets.append("rank=:rank")
        args["rank"] = _next_rank(conn, owner)
        _event(conn, goal_id, actor, "owner", row["owner_id"], owner)
    if rank is not None or top:
        sets.append("rank=:rank")
        args["rank"] = _next_rank(conn, args.get("owner_id") or row["owner_id"], top=True) if top else int(rank)
        _event(conn, goal_id, actor, "rank", row["rank"], args["rank"])
    if bet_id is not None and (bet_id or None) != row.get("bet_id"):
        sets.append("bet_id=:bet_id")
        args["bet_id"] = _bet(conn, actor, bet_id) if bet_id else None
        _event(conn, goal_id, actor, "bet_id", row.get("bet_id"), args["bet_id"])
    if not sets:
        return row
    args["id"], args["updated_at"] = goal_id, H.now()
    conn.execute(f"UPDATE goals SET {', '.join(sets)}, updated_at=:updated_at WHERE id=:id", args)
    H.event(conn, actor, "goal.update", goal_id, {k: v for k, v in args.items() if k not in ("id", "body")})
    return goal(conn, goal_id)


def reads_due(conn, actor, goal_ids, every=600):
    """The goals `mark_read` would change: read by someone else, or not by `actor` in `every` seconds. A bot reads its
    goals many times an hour; the read path writes only when one is due."""
    since = H.shift(H.now(), seconds=-every)
    return [r[0] for part in H.chunks(sorted(set(goal_ids))) for r in conn.execute(
        "SELECT id FROM goals WHERE id IN (%s) AND (last_read_by_id IS NOT ? OR last_read_at IS NULL OR last_read_at<?)"
        % ",".join("?" * len(part)), (*part, actor, since))]


def mark_read(conn, actor, goal_ids):
    """`hub goal list` and `hub goal show` from a bot: the signal that the mandate was read."""
    ts = H.now()
    for gid in goal_ids:
        conn.execute("UPDATE goals SET last_read_at=?, last_read_by_id=? WHERE id=?", (ts, actor, gid))


# ----------------------------------------------------------------------------- KPIs on goals
def _lint(conn, actor, error):
    H.refuse(conn, actor, "lint", str(error))


def kpi_create(conn, actor, owner, fields, goal_id=None, target=None):
    """A standalone KPI (backend/kpis.py), and with `goal_id` its link to that goal in one go."""
    H._writer(conn, actor)
    if goal_id and not goal(conn, goal_id):
        H.refuse(conn, actor, "not-found", f"no goal {goal_id}")
    try:
        row = K.create(conn, actor, owner, fields)
        values = None
        if goal_id and target is not None:
            values, error = K.validate_target(row, target)
            if error:
                raise ValueError(error)
    except ValueError as exc:
        _lint(conn, actor, exc)
    H.event(conn, actor, "kpi.create", row["id"], {"name": row["name"], "owner_id": owner})
    arm_pass(conn)
    if goal_id:
        kpi_link(conn, actor, goal_id, row["id"], target or {"kind": "none"})
    return kpi(conn, row["id"])


def kpi_edit(conn, actor, kpi_id, fields, owner=None):
    """Change a KPI. A change to what it measures is a new definition version, noted on every goal it serves."""
    H._writer(conn, actor)
    row = kpi(conn, kpi_id)
    if not row or K.auto(kpi_id):
        H.refuse(conn, actor, "not-found", f"no kpi {kpi_id}")
    try:
        after, changes = K.update(conn, actor, kpi_id, fields, owner)
    except ValueError as exc:
        _lint(conn, actor, exc)
    if not changes:
        return row
    H.event(conn, actor, "kpi.update", kpi_id, {k: v[1] for k, v in changes.items()})
    if after["definition_version"] != row["definition_version"]:
        for link in K.links_of(conn, kpi_id=kpi_id):
            _event(conn, link["goal_id"], actor, "kpi_definition", row["definition_version"], after["definition_version"],
                   after["name"])
    for link in K.links_of(conn, kpi_id=kpi_id):
        apply_auto(conn, link["goal_id"])
    return after


def kpi_log(conn, actor, kpi_id, value, note="", source="", at=None, **fields):
    """A reading: a fact somebody measured, with the period it describes. Appended, never edited; a
    correction names the reading it replaces in `supersedes`. `at` is the old spelling of `period_end`."""
    H._writer(conn, actor)
    row = kpi(conn, kpi_id)
    if not row or K.auto(kpi_id):
        H.refuse(conn, actor, "not-found", f"no kpi {kpi_id}" if not row else f"{row['name']} is computed by Tico, not logged")
    if at and not fields.get("period_end"):
        fields["period_end"] = at
    try:
        reading = K.add_reading(conn, actor, row, value, note=note, source=source, **fields)
    except ValueError as exc:
        _lint(conn, actor, exc)
    H.event(conn, actor, "kpi.log", kpi_id, {"value": reading["value"], "quality": reading["quality"]})
    for link in K.links_of(conn, kpi_id=kpi_id):
        apply_auto(conn, link["goal_id"])
    _watch(conn, kpi_id=kpi_id)
    return reading


def _watch(conn, **scope):
    """The strategy's fit check (backend/strategy.py `watch`) after a reading or a status pass. A failure there is
    logged and never undoes the reading or the colours."""
    from . import strategy
    if scope.get("kpi_id") and not strategy.watched(conn, scope["kpi_id"]):
        return
    with isolated(conn, "strategy_watch", scope.get("kpi_id") or "all"):
        strategy.watch(conn, **scope)


def kpi_link(conn, actor, goal_id, kpi_id, target):
    """Link a goal to a KPI, with the target on the link (`kind` none, improve or maintain). Linking again
    replaces the target."""
    H._writer(conn, actor)
    row = kpi(conn, kpi_id)
    if not goal(conn, goal_id):
        H.refuse(conn, actor, "not-found", f"no goal {goal_id}")
    if not row:
        H.refuse(conn, actor, "not-found", f"no kpi {kpi_id}")
    values, error = K.validate_target(row, target or {"kind": "none"})
    if error:
        _lint(conn, actor, error)
    if values["kind"] == "improve" and values["baseline"] is None:
        # No baseline given: the line starts from the latest reading, or from the first one to come.
        newest = K.latest(K.readings(conn, kpi_id, effective=True), usable=True)
        if newest:
            values["baseline"], values["baseline_at"] = newest["value"], newest["period_end"]
    made, old = K.set_link(conn, actor, goal_id, kpi_id, values)
    if not old:
        _event(conn, goal_id, actor, "kpi", None, row["name"], K.target_label(row, made))
    elif K.target_label(row, old) != K.target_label(row, made):
        _event(conn, goal_id, actor, "kpi_target", K.target_label(row, old), K.target_label(row, made), row["name"])
    H.event(conn, actor, "kpi.link", kpi_id, {"goal_id": goal_id, "kind": values["kind"]})
    apply_auto(conn, goal_id)
    return made


def kpi_unlink(conn, actor, goal_id, kpi_id):
    H._writer(conn, actor)
    row = kpi(conn, kpi_id)
    made = K.link(conn, goal_id, kpi_id)
    if not made:
        H.refuse(conn, actor, "not-found", "that KPI is not linked to this goal")
    K.unlink(conn, goal_id, kpi_id)
    _event(conn, goal_id, actor, "kpi", (row or {}).get("name") or kpi_id, None, "unlinked")
    H.event(conn, actor, "kpi.unlink", kpi_id, {"goal_id": goal_id})
    apply_auto(conn, goal_id)


def arm_pass(conn):
    """The Strategy Bot's daily pass starts paused, like a starter bot's first routine, and may start once a
    KPI exists. Once: a person who pauses it afterwards is not overruled."""
    from . import routines
    sid = f"{GOAL_MANAGER}:kpi-pass"
    row = routines.row(conn, sid)
    if not row or row["deleted_at"] or row["enabled"] or not conn.execute("SELECT 1 FROM kpis LIMIT 1").fetchone():
        return False
    if conn.execute("SELECT 1 FROM events WHERE action='goal_manager.armed'").fetchone():
        return False
    routines.update(conn, H.KEEPER, sid, {"enabled": True})
    H.event(conn, H.KEEPER, "goal_manager.armed", sid, {})
    return True


# ----------------------------------------------------------------------------- the colour, worked out
def _age(then, at):
    days = (at - H.parse_ts(then).astimezone(timezone.utc)).total_seconds() / 86400 if H.parse_ts(then) else 0
    return K.age_words(days)


def _from_owner(conn, row, at):
    """A goal with no KPI to judge it by is coloured by its owner's check-in, then by its tasks."""
    since = H.shift(H.now(), days=-CHECKIN_DAYS)
    said = H._one(conn, "SELECT * FROM goal_checkins WHERE goal_id=? AND signal IS NOT NULL AND created_at>? "
                        "ORDER BY created_at DESC LIMIT 1", (row["id"], since))
    if said:
        return {"status": SIGNAL_COLOUR[said["signal"]], "basis": "checkin",
                "reason": f"Check-in {_age(said['created_at'], at)} ago: {said['signal'].replace('_', ' ')}"}
    tasks = conn.execute("SELECT status, done_at, closed_at, updated_at FROM tasks WHERE goal_id=? "
                         "AND status!='declined' AND coalesce(private,1)=0",
                         (row["id"],)).fetchall()
    if not tasks:
        return None
    finished = [t["done_at"] or t["closed_at"] for t in tasks if t["done_at"] or t["closed_at"]]
    waiting = [t for t in tasks if not (t["done_at"] or t["closed_at"])]
    if not waiting:
        return {"status": "green", "basis": "tasks", "reason": f"All {len(tasks)} tasks done"}
    latest = max(finished, default=None)
    if latest and (at - H.parse_ts(latest).astimezone(timezone.utc)).days < TASK_DAYS:
        return {"status": "green", "basis": "tasks",
                "reason": f"{len(finished)} of {len(tasks)} tasks done, latest {_age(latest, at)} ago"}
    created = H.parse_ts(row["created_at"])
    if created and (at - created.astimezone(timezone.utc)).days >= TASK_DAYS:
        moved = max(t["updated_at"] or "" for t in waiting)
        if moved and (at - H.parse_ts(moved).astimezone(timezone.utc)).days >= 30:
            return {"status": "red", "basis": "tasks", "reason": "Nothing has moved in 30 days"}
        return {"status": "yellow", "basis": "tasks", "reason": f"No task done in {TASK_DAYS} days ({len(waiting)} open)"}
    return None


def score(conn, row, at=None):
    """The automatic colour of a goal and the one line that says why: {status, reason, basis}.
    From its KPIs when they carry a target (the worst of them decides); else from its owner's latest
    check-in and its tasks; else gray, no data. A KPI without fresh data never turns a goal red."""
    at = at or K.now()
    views = K.goal_link_status(conn, row["id"], at)
    judged = [v for v in views if v["status"] in COLOURS]
    if judged:
        worst = min(judged, key=lambda v: COLOURS.index(v["status"]))
        others = len(judged) - 1
        return {"status": worst["status"], "basis": "kpis",
                "reason": worst["reason"] + (f" (+{others} more)" if others and worst["status"] != "green" else "")}
    fallback = _from_owner(conn, row, at)
    if fallback:
        return fallback
    if views:
        return {"status": "gray", "basis": "kpis", "reason": views[0]["reason"]}
    measures = K.goal_views(conn, [row["id"]], at, include_archived=True)[row["id"]]
    if len(measures) == 1 and measures[0].get("archived_at"):
        return {"status": "gray", "basis": "kpis",
                "reason": f"Its only measure was archived on {K.day(measures[0]['archived_at'])}"}
    return {"status": "gray", "basis": "none", "reason": "No data yet: no KPI, check-in or task progress"}


def apply_auto(conn, goal_id, at=None):
    """Set a goal's automatic colour, unless a person set one. A goal a person set only gets a visible suggestion when
    the arithmetic disagrees, never a change. Done and dropped goals are left alone. Nothing at all while the owner has
    the Strategy Bot's regular checks off (backend/strategy_bot.py `checks`): turning them back on works every colour
    out again. Returns what happened, or None."""
    row = goal(conn, goal_id)
    if not row or row["status"] in ("done", "dropped"):
        return None
    if not strategy_bot.checks(conn):
        return None
    result = score(conn, row, at)
    ts = H.now()
    if row["status_source"] == "person":
        differs = result["status"] in COLOURS and result["status"] != row["status"]
        suggest = (result["status"], result["reason"]) if differs else (None, None)
        if (suggest[0], suggest[1]) != (row["suggest_status"], row["suggest_note"]):
            conn.execute("UPDATE goals SET suggest_status=?, suggest_note=?, suggest_at=? WHERE id=?",
                         (suggest[0], suggest[1], ts if suggest[0] else None, goal_id))
            return {"goal": goal_id, "suggested": suggest[0], "reason": suggest[1]} if suggest[0] else None
        return None
    if row["status"] is None and result["status"] == "gray":
        return None                                   # nothing to say yet: it stays unscored
    changed = result["status"] != row["status"]
    if not changed and result["reason"] == row["status_note"] and row["status_by_id"] == AUTO_ACTOR:
        return None
    conn.execute("UPDATE goals SET status=?, status_note=?, status_by_id=?, status_source='auto', "
                 "status_at=CASE WHEN ? THEN ? ELSE status_at END, updated_at=CASE WHEN ? THEN ? ELSE updated_at END, "
                 "suggest_status=NULL, suggest_note=NULL, suggest_at=NULL, "
                 "rank=CASE WHEN rank IS NULL THEN ? ELSE rank END WHERE id=?",
                 (result["status"], result["reason"], AUTO_ACTOR, changed, ts, changed, ts,
                  _next_rank(conn, row["owner_id"]), goal_id))
    if changed:
        _event(conn, goal_id, AUTO_ACTOR, "status", row["status"], result["status"], result["reason"],
               status_by=AUTO_ACTOR, status_source="auto")
        H.event(conn, AUTO_ACTOR, "goal.status", goal_id, {"status": result["status"], "source": "auto"})
    return {"goal": goal_id, "status": result["status"], "was": row["status"], "reason": result["reason"],
            "changed": changed}


def refresh(conn, goal_ids=None, at=None):
    """The Strategy Bot's status pass: every live goal (or the ones named) gets its automatic colour
    worked out again, which is how time passing turns fresh data stale."""
    rows = [goal(conn, g) for g in goal_ids] if goal_ids else goals(conn, live_only=True)
    changed, suggested = [], []
    for row in filter(None, rows):
        with isolated(conn, "goal_refresh", row["id"]):
            out = apply_auto(conn, row["id"], at)
            if out and out.get("suggested"):
                suggested.append(out)
            elif out and out.get("changed"):
                changed.append(out)
    _watch(conn)
    return {"changed": changed, "suggested": suggested, "checked": len([r for r in rows if r])}


def hand_back(conn, actor, goal_id):
    """A person lets the Strategy Bot set the colour again: the override ends and it is worked out now."""
    H._writer(conn, actor)
    row = goal(conn, goal_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no goal {goal_id}")
    if row["status_source"] != "person" or row["status"] not in COLOURS:
        H.refuse(conn, actor, "kind", "only a colour a person set can be handed back; this one is already automatic"
                 if row["status_source"] != "person" else "a done or dropped goal is not handed back: set a colour first")
    conn.execute("UPDATE goals SET status_source='auto', suggest_status=NULL, suggest_note=NULL, suggest_at=NULL, "
                 "updated_at=? WHERE id=?", (H.now(), goal_id))
    _event(conn, goal_id, actor, "status_source", "person", "auto", "Let " + strategy_bot.name(conn) + " set it",
           status_by=actor, status_source="auto")
    H.event(conn, actor, "goal.hand_back", goal_id, {})
    apply_auto(conn, goal_id)
    return goal(conn, goal_id)


def checkin_add(conn, actor, goal_id, body, signal=None, source_actor=None, kpi_id=None):
    """The owner's own answer about how a goal is going, in their words: interpretation, kept apart from
    the readings (facts). `source_actor` is whose answer it is when someone records it for them."""
    H._writer(conn, actor)
    if not goal(conn, goal_id):
        H.refuse(conn, actor, "not-found", f"no goal {goal_id}")
    body = str(body or "").strip()
    if not body:
        H.refuse(conn, actor, "lint", "a check-in says what is going on, in a sentence")
    if signal and signal not in SIGNALS:
        H.refuse(conn, actor, "kind", f"a signal is {'|'.join(SIGNALS)}, not {signal}")
    if kpi_id and not kpi(conn, kpi_id):
        H.refuse(conn, actor, "not-found", f"no kpi {kpi_id}")
    row = {"id": H.new_id(), "goal_id": goal_id, "kpi_id": kpi_id or None, "created_at": H.now(), "author": actor,
           "source_actor": source_actor or actor, "body": body[:4000], "signal": signal or None}
    conn.execute("INSERT INTO goal_checkins (id, goal_id, kpi_id, created_at, author, source_actor, body, signal) VALUES "
                 "(:id, :goal_id, :kpi_id, :created_at, :author, :source_actor, :body, :signal)", row)
    _event(conn, goal_id, actor, "checkin", None, signal, body[:300])
    H.event(conn, actor, "goal.checkin", goal_id, {"signal": signal})
    apply_auto(conn, goal_id)
    return row


# ----------------------------------------------------------------------------- proposals
def _payload_error(conn, kind, goal_id, kpi_id, payload):
    """What is wrong with a proposal's payload, in a sentence, or None."""
    if kind in ("goal_wording", "goal_kpi", "flag", "goal_why", "recovery") and not goal(conn, goal_id):
        return "name the goal this is about"
    if kind in ("brand_draft", "gtm_draft") and (goal_id or kpi_id):
        # A draft page proposal carries unpublished words: never filed under a goal or KPI members read.
        return "a brand or GTM draft proposal is about the page, not a goal or KPI: leave goal_id and kpi_id out"
    if kind in ("kpi_definition", "kpi_target") and (not kpi_id or not kpi(conn, kpi_id)
                                                      or (kind == "kpi_definition" and K.auto(kpi_id))):
        return "name a KPI this is about (a KPI Tico computes itself has no definition to change)"
    try:
        if kind == "goal_wording":
            # A bet alone links the goal to the strategy bet it serves (the Strategy Bot's goals per bet).
            if not (payload.get("title") or payload.get("body") or payload.get("bet_id")):
                return "propose a new title, body or bet_id"
            if payload.get("bet_id"):
                from . import strategy
                found = strategy.bet(conn, str(payload["bet_id"]))
                if not found or found["status"] == "retired":
                    return f"no live strategy bet {payload['bet_id']}"
        elif kind == "kpi_definition":
            if not K.clean(payload):
                return "propose a change to the name, definition, unit, direction, cadence or source note"
        elif kind == "kpi_target":
            if not goal_id or not goal(conn, goal_id):
                return "name the goal whose target this changes"
            _, error = K.validate_target(kpi(conn, kpi_id), payload)
            return error
        elif kind == "goal_kpi":
            if payload.get("kpi_id"):
                if not kpi(conn, payload["kpi_id"]):
                    return "no such KPI to link"
            else:
                K.clean(payload.get("kpi") or {})
                if not (payload.get("kpi") or {}).get("name"):
                    return "name the KPI, or name an existing one with kpi_id"
                owner = payload.get("owner")
                if owner and owner != COMPANY and not H.resolve_actor(conn, owner):
                    return f"no person or bot {owner} to own the KPI"
            _, error = K.validate_target(kpi(conn, payload["kpi_id"]) if payload.get("kpi_id")
                                         else {**{"direction": "up"}, **(payload.get("kpi") or {})},
                                         payload.get("target") or {"kind": "none"})
            return error
        elif kind == "flag":
            if payload.get("issue") not in FLAGS:
                return f"a flag says what is wrong: {'|'.join(FLAGS)}"
        elif kind == "goal_why":
            from . import company as C
            return C.why_payload_error(payload)
        elif kind == "recovery":
            from . import strategy_suggest
            return strategy_suggest.payload_error(conn, goal_id, payload)
        elif kind == "strategy_draft":
            from . import strategy
            return strategy.proposal_error(conn, payload)
        elif kind == "strategy_realign":
            from . import strategy
            return strategy.realign_error(conn, payload)
        elif kind in ("brand_draft", "gtm_draft"):
            from . import brand_gtm
            return brand_gtm.proposal_error(conn, brand_gtm.kind_of_proposal(kind), payload)
    except ValueError as exc:
        return str(exc)
    return None


# What a goal suggestion card says beside the payload (ui/goals-kpis.js goalPropHtml): the names, the measure an Add a
# KPI suggestion adds (new or reused), targets in words, and Now / Proposed for new wording or a new definition.
CARD_FIELDS = {"name": "Name", "definition": "Definition", "unit": "Unit", "direction": "Direction",
               "cadence": "Cadence", "source_note": "Source"}


def card(conn, p, hidden, editor):
    """What the card tells this reader. `hidden`: owners whose goals and KPIs the reader may not read
    (backend/goal_routes.py hidden_owners); nothing of theirs is read in, not a reused measure's unit either.
    `editor`: the reader edits the strategy, so reads unpublished bets; anyone else reads the published ones."""
    pl, kind = p.get("payload") or {}, p["kind"]
    seen = lambda record: record if record and record["owner_id"] not in hidden else None
    row = seen(goal(conn, p["goal_id"]) if p.get("goal_id") else None)
    record = seen(kpi(conn, p["kpi_id"]) if p.get("kpi_id") else None)
    out = {"goal_title": row and row["title"], "kpi_name": record and record["name"]}
    if kind == "goal_kpi":
        used = kpi(conn, pl["kpi_id"]) if pl.get("kpi_id") else None
        if used and not seen(used):
            out["measure"] = {"name": "a measure you cannot see", "definition": "", "source_note": "", "unit": "",
                              "direction": "up", "existing": True, "hidden": True}
            out["target_label"] = K.target_label({}, pl.get("target"))
            return out
        spec = used or {"direction": "up", **(pl.get("kpi") or {})}
        out["measure"] = {"name": spec.get("name") or "", "definition": spec.get("definition") or "",
                          "source_note": spec.get("source_note") or "", "unit": spec.get("unit") or "",
                          "direction": spec.get("direction") or "up", "existing": bool(used)}
        out["target_label"] = K.target_label(spec, pl.get("target"))
    elif kind == "kpi_target" and record:
        # The target it has now is the link's on that goal: only for a reader of the goal.
        link = next(iter(K.links_of(conn, goal_id=row["id"], kpi_id=record["id"])), None) if row else None
        out["target_label"] = K.target_label(record, pl)
        if row:
            out["now_label"] = K.target_label(record, link)
    elif kind == "kpi_definition" and record:
        out["changes"] = [{"label": CARD_FIELDS.get(k, k), "now": record.get(k) or "", "proposed": v}
                          for k, v in K.clean(dict(pl)).items()]
    elif kind == "goal_wording" and row:
        # Every field yes writes (update writes any that is not None): an empty body clears the description.
        changes = [{"label": label, "now": row.get(k) or "", "proposed": pl[k]}
                   for k, label in (("title", "Title"), ("body", "Description")) if pl.get(k) is not None]
        if pl.get("bet_id"):
            from . import strategy
            if editor:
                bet = lambda bid: (strategy.bet(conn, str(bid)) or {}).get("statement") if bid else ""
            else:
                published = {b.get("id"): b.get("statement") for b in (strategy.current(conn) or {}).get("bets") or []}
                bet = lambda bid: published.get(bid) if bid else ""
            changes.append({"label": "Serves", "now": bet(row.get("bet_id")) or "", "proposed": bet(pl["bet_id"]) or ""})
        out["changes"] = changes
    return out


WHY_REPLACED = "Replaced by a newer Why"


def propose(conn, actor, kind, goal_id=None, kpi_id=None, payload=None, reason=""):
    """A change somebody may not make themselves (the Strategy Bot, above all, may not change a target it
    is judged against): stored for the goal's or KPI's owner to confirm. The same proposal twice is one."""
    H._writer(conn, actor)
    payload = payload if isinstance(payload, dict) else {}
    if kind not in PROPOSALS:
        H.refuse(conn, actor, "kind", f"a proposal is {'|'.join(PROPOSALS)}, not {kind}")
    if kind == "recovery":
        from . import strategy_suggest
        if actor != strategy_suggest.STRATEGY_BOT:
            H.refuse(conn, actor, "identity", f"Only the {strategy_bot.name(conn)} proposes a recovery")
        if not strategy_suggest.suggests(conn):
            H.refuse(conn, actor, "state",
                     f"The {strategy_bot.name(conn)} suggests recoveries at level 2 or 3; the owner "
                     "chooses the level in Settings")
        kpi_id = None
    error = _payload_error(conn, kind, goal_id, kpi_id, payload)
    if error:
        H.refuse(conn, actor, "lint", error)
    body = H._dump(payload)
    same = H._one(conn, "SELECT id FROM goal_proposals WHERE status='pending' AND kind=? AND coalesce(goal_id,'')=? "
                        "AND coalesce(kpi_id,'')=? AND payload=?", (kind, goal_id or "", kpi_id or "", body))
    if same:
        return proposal(conn, same["id"])
    if kind == "goal_why":
        # One open Why per goal: a new line replaces the one still waiting, so the owner is never asked twice. It is
        # a new proposal, not the old one rewritten, so an Accept clicked on the words a person saw never confirms
        # words they did not (decide refuses the replaced one).
        conn.execute("UPDATE goal_proposals SET status='rejected', decided_by=?, decided_at=?, decision_note=? "
                     "WHERE status='pending' AND kind='goal_why' AND goal_id=?", (actor, H.now(), WHY_REPLACED, goal_id))
    row = {"id": H.new_id(), "kind": kind, "goal_id": goal_id or None, "kpi_id": kpi_id or None, "payload": body,
           "reason": str(reason or "").strip()[:1000], "proposed_by": actor, "proposed_at": H.now()}
    conn.execute("INSERT INTO goal_proposals (id, kind, goal_id, kpi_id, payload, reason, proposed_by, proposed_at) "
                 "VALUES (:id, :kind, :goal_id, :kpi_id, :payload, :reason, :proposed_by, :proposed_at)", row)
    if goal_id:
        _event(conn, goal_id, actor, "proposal", None, kind, row["reason"])
    H.event(conn, actor, "goal.propose", row["id"], {"kind": kind, "goal_id": goal_id, "kpi_id": kpi_id})
    return proposal(conn, row["id"])


def proposal(conn, proposal_id):
    return _proposal_view(H._one(conn, "SELECT * FROM goal_proposals WHERE id=?", (proposal_id,)), conn)


def decide(conn, actor, proposal_id, decision, note="", why=None, option=None, moves=None, target=None):
    """The owner confirms (the change is made, as them) or rejects. Only a pending proposal is decided, once. A Why may
    be confirmed as edited: `why` is the line they kept. A recovery is confirmed with one of its options (`option`, the
    Strategy Bot's pick when None), made as the Strategy Bot (backend/strategy_suggest.py). A strategy realignment is
    confirmed with `moves`, per goal: keep, retire, or a bet id (backend/strategy.py). An Add a KPI suggestion may be
    confirmed with the person's own `target` for the link, checked as any link's target is, in place of its own."""
    H._writer(conn, actor)
    row = proposal(conn, proposal_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no proposal {proposal_id}")
    if row["status"] != "pending":
        if row["kind"] == "goal_why" and row.get("decision_note") == WHY_REPLACED:
            H.refuse(conn, actor, "duplicate", "That Why was replaced by a newer one; read the new one before you decide")
        H.refuse(conn, actor, "duplicate", f"{proposal_id} was already {row['status']}")
    if decision not in ("confirm", "reject"):
        H.refuse(conn, actor, "kind", f"a decision is confirm|reject, not {decision}")
    if why is not None and (row["kind"] != "goal_why" or decision != "confirm"):
        H.refuse(conn, actor, "kind", "only a Why proposal is confirmed with an edited line")
    if option is not None and (row["kind"] != "recovery" or decision != "confirm"):
        H.refuse(conn, actor, "kind", "only a recovery is confirmed with one of its options")
    if moves and (row["kind"] != "strategy_realign" or decision != "confirm"):
        H.refuse(conn, actor, "kind", "only a strategy realignment is confirmed with moves")
    if target is not None:
        if row["kind"] != "goal_kpi" or decision != "confirm":
            H.refuse(conn, actor, "kind", "only an Add a KPI suggestion is confirmed with a target")
        payload = row["payload"]
        measure = (kpi(conn, payload["kpi_id"]) if payload.get("kpi_id")
                   else {"direction": "up", **(payload.get("kpi") or {})}) or {}
        _, error = K.validate_target(measure, target)
        if error:
            _lint(conn, actor, error)
    result = None
    if row["kind"] == "recovery" and decision == "confirm":
        from . import strategy_suggest
        if not strategy_suggest.suggests(conn):
            H.refuse(conn, actor, "state",
                     f"The {strategy_bot.name(conn)} is at Passive now, where it changes no tasks")
        pick = row["payload"]["pick"]
        chosen = pick if option is None else option
        if not isinstance(chosen, int) or not 0 <= chosen < len(row["payload"]["options"]):
            H.refuse(conn, actor, "lint", f"choose option 0 to {len(row['payload']['options']) - 1}")
    if decision == "confirm":
        payload, goal_id, kpi_id = row["payload"], row["goal_id"], row["kpi_id"]
        if row["kind"] == "goal_wording":
            update(conn, actor, goal_id, title=payload.get("title"), body=payload.get("body"),
                   bet_id=payload.get("bet_id") or None)
        elif row["kind"] == "kpi_definition":
            kpi_edit(conn, actor, kpi_id, payload)
        elif row["kind"] == "kpi_target":
            kpi_link(conn, actor, goal_id, kpi_id, payload)
        elif row["kind"] == "goal_kpi":
            target = target if target is not None else payload.get("target") or {"kind": "none"}
            if payload.get("kpi_id"):
                kpi_link(conn, actor, goal_id, payload["kpi_id"], target)
                result = {"kpi_id": payload["kpi_id"]}
            else:
                owner = payload.get("owner")
                owner = (COMPANY if owner == COMPANY else H.resolve_actor(conn, owner)) if owner else None
                made = kpi_create(conn, actor, owner or (goal(conn, goal_id) or {}).get("owner_id"),
                                  payload["kpi"], goal_id=goal_id, target=target)
                result = {"kpi_id": made["id"]}
        elif row["kind"] == "goal_why":
            from . import company as C
            proposed = " ".join(str(payload.get("why") or "").split())
            kept = proposed if why is None else " ".join(str(why).split())
            if not kept:
                H.refuse(conn, actor, "lint", "an accepted Why is one line; reject the proposal to leave it out")
            C.set_why(conn, actor, goal_id, kept, settled_by=proposal_id)
            result = {"why": kept, "edited": kept != proposed}
        elif row["kind"] == "recovery":
            applied = strategy_suggest.apply(conn, actor, row, chosen)
            # Settled before its follow-up is filed: a retry of a retry is a new proposal, not this one found again.
            conn.execute("UPDATE goal_proposals SET status='confirmed' WHERE id=?", (proposal_id,))
            again = strategy_suggest.follow_up(conn, row, applied["left"])
            result = {"option": chosen, "unchanged": chosen == pick, **applied, "follow_up": again and again["id"]}
        elif row["kind"] == "strategy_draft":
            # A bot's proposed strategy, written to the draft and its bets as the owner. Never published.
            from . import strategy
            result = strategy.apply_proposal(conn, actor, payload)
        elif row["kind"] == "strategy_realign":
            from . import strategy
            result = strategy.apply_realign(conn, actor, payload, moves)
        elif row["kind"] in ("brand_draft", "gtm_draft"):
            # Merged into the draft as the admin who confirmed it. Never published.
            from . import brand_gtm
            result = brand_gtm.apply_proposal(conn, brand_gtm.kind_of_proposal(row["kind"]), actor, payload)
    conn.execute("UPDATE goal_proposals SET status=?, decided_by=?, decided_at=?, decision_note=?, result=? "
                 "WHERE id=?", ("confirmed" if decision == "confirm" else "rejected", actor, H.now(),
                                str(note or "").strip()[:1000], H._dump(result) if result else None, proposal_id))
    if row["goal_id"]:
        _event(conn, row["goal_id"], actor, "proposal", row["kind"], "confirmed" if decision == "confirm" else "rejected",
               str(note or ""))
    H.event(conn, actor, "goal.decide", proposal_id, {"decision": decision, "kind": row["kind"]})
    if row["kind"] == "strategy_realign":
        from . import strategy
        # Confirmed with every goal kept (or closed since) changes nothing: it reads as the goals staying.
        changed = decision == "confirm" and any(v in ("retired", "moved") for v in (result or {}).get("goals", {}).values())
        strategy.settle_card(conn, actor, proposal_id, "Resolved: the goals were realigned with the strategy."
                             if changed else "Resolved: the goals stay as they are.")
    return proposal(conn, proposal_id)


REASON_MAX = 280         # one line: a tweet's length is room for "no billing yet, don't track revenue" and its why


def add_reason(conn, actor, proposal_id, note):
    """The reason someone adds after turning a suggestion down ("Tell the Strategy Bot why?"): theirs alone, once, while
    the rejection has none. One line; the Strategy Bot reads it before suggesting again (hub proposal list --status rejected)."""
    H._writer(conn, actor)
    row = proposal(conn, proposal_id)
    if not row:
        H.refuse(conn, actor, "not-found", f"no proposal {proposal_id}")
    if row["status"] != "rejected":
        H.refuse(conn, actor, "state", f"Only a suggestion that was turned down takes a reason; this one is {row['status']}")
    if row["decided_by"] != actor:
        H.refuse(conn, actor, "identity", "Only the person who turned the suggestion down adds its reason")
    if row["decision_note"]:
        H.refuse(conn, actor, "duplicate", "It already has a reason; a reason is added once")
    text = " ".join(str(note or "").split())
    if not text:
        H.refuse(conn, actor, "lint", "The reason is empty; leave the box instead, and the suggestion stays turned down")
    if len(text) > REASON_MAX:
        H.refuse(conn, actor, "lint", f"A reason is one line of at most {REASON_MAX} characters, not {len(text)}")
    # Checked again in the write: two Sends at once save one reason, never the second over the first.
    if conn.execute("UPDATE goal_proposals SET decision_note=? WHERE id=? AND status='rejected' AND decided_by=? "
                    "AND decision_note=''", (text, proposal_id, actor)).rowcount != 1:
        H.refuse(conn, actor, "duplicate", "It already has a reason; a reason is added once")
    H.event(conn, actor, "goal.reason", proposal_id, {"kind": row["kind"]})
    return proposal(conn, proposal_id)


# ----------------------------------------------------------------------------- seed
def seed(conn, document, resolve):
    """Import `registry/goals.yaml` once per goal: a goal whose `id` is already in the table is
    never touched, so the file is a first draft, not a source of truth. `resolve(owner)` maps a
    slug or person id to an actor string, or None when nobody by that name is on the roster;
    such a goal is skipped and tried again at the next start."""
    added = []
    for entry in (document or {}).get("goals") or []:
        gid, owner = str(entry.get("id") or "").strip(), resolve(entry.get("owner"))
        if not gid or not owner or goal(conn, gid):
            continue
        parent = str(entry.get("parent") or "").strip() or None
        if parent and not goal(conn, parent):
            continue
        ts = H.now()
        stated = entry.get("status") if entry.get("status") in STATUSES else None
        conn.execute("INSERT INTO goals (id, title, owner_id, parent_id, body, status, status_note, status_by_id, "
                     "status_at, status_source, rank, created_at, created_by_id, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (gid, str(entry.get("title") or gid).strip(), owner, parent, str(entry.get("body") or ""),
                      stated, str(entry.get("status_note") or ""), H.KEEPER if stated else None,
                      ts if stated else None, "person" if stated else None, _next_rank(conn, owner), ts, H.KEEPER, ts))
        _event(conn, gid, H.KEEPER, "created", None, entry.get("title"), "registry/goals.yaml")
        for measure in entry.get("kpis") or []:
            kid = str(measure.get("id") or "").strip() or None
            if kid and kpi(conn, kid):
                continue
            made = K.create(conn, H.KEEPER, owner, {"name": measure.get("name") or kid or "KPI",
                                                    "unit": measure.get("unit"), "definition": measure.get("definition"),
                                                    "direction": measure.get("direction"), "cadence": measure.get("cadence")},
                            kid=kid)
            target = {"kind": "none"}
            if measure.get("target") is not None:
                target = {"kind": "improve", "target": measure["target"], "deadline": measure.get("deadline")}
            K.set_link(conn, H.KEEPER, gid, made["id"], {"kind": target["kind"], "baseline": None, "baseline_at": None,
                       "target": target.get("target"), "deadline": str(target.get("deadline") or "")[:10] or None,
                       "min": None, "max": None})
            for reading in measure.get("readings") or []:
                try:
                    K.add_reading(conn, resolve(reading.get("by")) or H.KEEPER, made, float(reading.get("value")),
                                  period_end=reading.get("at"), source=str(reading.get("source") or "measured"),
                                  note=str(reading.get("note") or ""))
                except (ValueError, TypeError):
                    continue
        added.append(gid)
    return added
