"""Reach (docs/reach.md): who each teammate may reach, how often, and the rules they follow.

- Parties: `*` (Everyone), `group:<id>`, `human:<id>`, `bot:<id>`, the hub's actor strings.
- `reach_settings`: one row per party that sets anything (`mode`, `chats_per_day`, `tasks_per_day`, each nullable: null
  takes it from above). `effective` resolves a party's value from itself, its team and the teams above it (nearest
  first, `people.group_chain`), then Everyone, then DEFAULTS.
- `reach_lines`: one line per sender and receiver, with `per_day` (null, 0 or N) and the id of its internal org rule
  (Who = sender, With = receiver, if broken = warn), made, edited and retired through backend/org_rules.py.
- Who changes what: the owner and admins every party; a team lead its teams (and those nested under them) and the
  people and bots in them; a bot's managers (`Auth.bot_manager`) that bot. A line belongs to its sender. No one changes
  their own reach. Every teammate reads every setting and line except those naming a teammate they may not See.
- Every change writes an audit row (`reach_setting:<party>`, `reach_line:<id>`) and an event (`reach.*`).

Stored and shown only: nothing enforces reach yet, and task caps (backend/task_caps.py) are still their own table.

Unversioned and idempotent (`ensure`), like backend/org_rules.py.
"""
import dataclasses
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, Field

from . import audit_log
from . import groups as G
from . import hubdb as H
from . import people as P
from .models import Contract
from .store import Problem

MAX = 100_000
FIELDS = ("mode", "chats_per_day", "tasks_per_day")
DEFAULTS = {"mode": "open", "chats_per_day": 999, "tasks_per_day": 999}
MODES = ("open", "closed")
LINES_MAX = 2000            # the lines one company keeps; the map answers them all at once

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS reach_settings(id TEXT PRIMARY KEY, party_id TEXT NOT NULL, "
    "mode TEXT CHECK (mode IS NULL OR mode IN ('open','closed')), chats_per_day INTEGER, tasks_per_day INTEGER, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by_id TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS reach_settings_party ON reach_settings(party_id)",
    "CREATE TABLE IF NOT EXISTS reach_lines(id TEXT PRIMARY KEY, sender_id TEXT NOT NULL, receiver_id TEXT NOT NULL, "
    "per_day INTEGER, org_rule_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "created_by_id TEXT NOT NULL, updated_by_id TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS reach_lines_pair ON reach_lines(sender_id, receiver_id)",
    "CREATE INDEX IF NOT EXISTS reach_lines_receiver ON reach_lines(receiver_id)",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


# ----------------------------------------------------------------------------------------------- the roster
def context(c):
    """What one read or check needs, read once: the roster, the bots' configs, the stored settings and lines."""
    roster = G._roster(c)
    return {"c": c, "roster": roster, "configs": G._configs(c), "settings": _settings(c), "lines": _lines(c),
            "owner": owner(c), "led": {}}


def _settings(c):
    return {r["party_id"]: dict(r) for r in c.execute("SELECT * FROM reach_settings")}


def _lines(c):
    return [dict(r) for r in c.execute("SELECT * FROM reach_lines ORDER BY created_at, id")]


def owner(c):
    """The company owner as an actor: never limited."""
    from .automations import top_person
    pid = top_person(c)
    return "human:" + pid if pid else None


def _team(ctx, actor):
    kind, _, ident = str(actor).partition(":")
    if kind == "bot":
        return "" if ident in G.SYSTEM_BOTS else (P.team_of(ident, ctx["configs"], ctx["roster"]) or "")
    if kind == "human":
        return (P.person(ident, ctx["roster"]) or {}).get("team") or ""
    return ""


def chain(ctx, party):
    """The party and everything above it, nearest first: a teammate, its team, the teams above, then Everyone. A team
    starts with itself."""
    if party == "*":
        return ["*"]
    kind, _, ident = party.partition(":")
    if kind == "group":
        return ["group:" + g for g in P.group_chain(ident, ctx["roster"])] + ["*"]
    team = _team(ctx, party)
    groups = P.group_chain(team, ctx["roster"]) if team else []
    return [party] + ["group:" + g for g in groups] + ["*"]


def effective(ctx, party):
    """A party's mode and counts, each from the nearest party in its chain that sets it, else the default. `from` names
    that party for each field (null for the default)."""
    out, came = {}, {}
    parties = chain(ctx, party)
    for field in FIELDS:
        for at in parties:
            row = ctx["settings"].get(at)
            if row and row[field] is not None:
                out[field], came[field] = row[field], at
                break
        else:
            out[field], came[field] = DEFAULTS[field], None
    return {**out, "from": came}


# ----------------------------------------------------------------------------------------------- parties
def party_of(c, value, ctx=None):
    """A party as stored: `*`, `group:<id>`, `human:<id>` (`person:` is taken too) or `bot:<id>` (a slug is taken and
    stored as the bot's id). Anything unknown is refused."""
    text = str(value or "").strip()
    if text in ("*", "everyone"):
        return "*"
    kind, sep, ident = text.partition(":")
    ident = ident.strip()
    if not sep or not ident:
        raise Problem("validation", "A party is *, group:<id>, human:<id> or bot:<id>", 422)
    roster = ctx["roster"] if ctx else G._roster(c)
    if kind == "group":
        if ident not in (roster.get("org_groups") or {}):
            raise Problem("not_found", f"Unknown team: {ident}", 404)
        return "group:" + ident
    if kind in ("human", "person"):
        if not P.person(ident, roster):
            raise Problem("not_found", f"Unknown person: {ident}", 404)
        return "human:" + ident
    if kind == "bot":
        found = H.resolve_bot(c, ident) or {}
        row = H.bot(c, found.get("id", ident))
        if not row or row.get("state") == "archived":
            raise Problem("not_found", f"Unknown bot: {ident}", 404)
        return "bot:" + row["id"]
    raise Problem("validation", "A party is *, group:<id>, human:<id> or bot:<id>", 422)


def all_parties(c, ctx):
    """Everyone, every team, every person on the chart and every bot that is not archived."""
    out = ["*"] + ["group:" + g for g in sorted(ctx["roster"].get("org_groups") or {})]
    out += ["human:" + p["id"] for p in P.visible_people(ctx["roster"])]
    out += ["bot:" + b["id"] for b in H.bots(c) if b.get("state") != "archived"]
    return out


# ----------------------------------------------------------------------------------------------- permissions
def _led(ctx, actor):
    """The teams `actor` leads, and every team nested under one."""
    if actor not in ctx["led"]:
        from .task_caps import actor as ref_actor
        roster, configs = ctx["roster"], ctx["configs"]
        out = set()
        for gid in roster.get("org_groups") or {}:
            if ref_actor(G.lead_of(gid, roster, configs)) == actor:
                out |= P.group_and_below(gid, roster)
        ctx["led"][actor] = out
    return ctx["led"][actor]


def may_edit(c, auth, who, party, ctx):
    """Whether `who` may change this party's settings and the lines it sends: never their own; the owner and admins
    anyone; a team lead its teams and the people and bots in them; a bot's managers that bot."""
    if party == who.actor or party == ctx["owner"]:
        return False
    if auth.bot_admin(who):
        return True
    if party == "*":
        return False
    kind, _, ident = party.partition(":")
    led = _led(ctx, who.actor)
    if kind == "group" and ident in led:
        return True
    if kind in ("human", "bot") and _team(ctx, party) in led and _team(ctx, party):
        return True
    return kind == "bot" and who.role == "human" and \
        auth.bot_manager(c, who, (H.bot(c, ident) or {}).get("slug") or ident)


def _require_edit(c, auth, who, party, ctx):
    if party == who.actor:
        raise Problem("forbidden", "No one changes their own reach: ask your team's lead or an admin", 403)
    if party == ctx["owner"]:
        raise Problem("validation", "The company owner is never limited", 422)
    if not may_edit(c, auth, who, party, ctx):
        raise Problem("forbidden", "Only the owner, an admin, the team's lead or the bot's manager changes this reach",
                      403)


# ----------------------------------------------------------------------------------------------- views
def _setting_view(row):
    return {"party": row["party_id"], "mode": row["mode"], "chats_per_day": row["chats_per_day"],
            "tasks_per_day": row["tasks_per_day"], "updated_at": row["updated_at"], "updated_by": row["updated_by_id"]}


def _line_row(c, line_id):
    row = c.execute("SELECT * FROM reach_lines WHERE id=?", (line_id,)).fetchone()
    if not row:
        raise Problem("not_found", f"There is no line {line_id}", 404)
    return dict(row)


def _line_core(row):
    return {"id": row["id"], "sender": row["sender_id"], "receiver": row["receiver_id"], "per_day": row["per_day"],
            "rule_id": row["org_rule_id"]}


def _rule_of(c, row):
    if not row["org_rule_id"]:
        return None
    found = c.execute("SELECT id, text, status FROM org_rules WHERE id=?", (row["org_rule_id"],)).fetchone()
    return {"id": found["id"], "text": found["text"]} if found and found["status"] != "retired" else None


def line_view(c, auth, who, row, ctx):
    return {"id": row["id"], "sender": row["sender_id"], "receiver": row["receiver_id"], "per_day": row["per_day"],
            "rule": _rule_of(c, row), "may_edit": may_edit(c, auth, who, row["sender_id"], ctx),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def listing(c, auth, who):
    """GET /api/v2/reach: the defaults, the stored settings, each party's effective values, the lines, and the parties
    the caller may change. A setting, line or party naming a teammate the caller may not See is left out."""
    from . import teammate_access as TA
    auth.domain(who)
    ctx = context(c)
    hidden = TA.hidden(c, who, "see", auth=auth)

    def shown(party):
        return party == "*" or party not in hidden

    parties = [p for p in all_parties(c, ctx) if shown(p)]
    effective_view = {}
    for party in parties:
        found = effective(ctx, party)
        found["from"] = {k: (v if v is None or shown(v) else None) for k, v in found["from"].items()}
        effective_view[party] = found
    return {
        "defaults": dict(DEFAULTS),
        "owner": ctx["owner"],
        "settings": [_setting_view(r) for p, r in sorted(ctx["settings"].items()) if shown(p)],
        "effective": effective_view,
        "lines": [line_view(c, auth, who, r, ctx) for r in ctx["lines"]
                  if shown(r["sender_id"]) and shown(r["receiver_id"])],
        "may_edit": [p for p in parties if may_edit(c, auth, who, p, ctx)],
    }


# ----------------------------------------------------------------------------------------------- writes
def _via(who):
    return getattr(who, "via", None) or H.VIA.get() or "api"


def save_setting(c, auth, who, party_value, changes):
    """Set or clear (None) the fields `changes` names for one party. A row with nothing left set goes."""
    ctx = context(c)
    party = party_of(c, party_value, ctx)
    _require_edit(c, auth, who, party, ctx)
    for field, value in changes.items():
        if field == "mode" and value is not None and value not in MODES:
            raise Problem("validation", "Mode is open or closed", 422)
        if field != "mode" and value is not None and not (0 <= int(value) <= MAX):
            raise Problem("validation", f"{field} is 0 to {MAX}", 422)
    before = ctx["settings"].get(party)
    after = {f: (before or {}).get(f) for f in FIELDS}
    after.update({k: v for k, v in changes.items() if k in FIELDS})
    now = H.now()
    if all(after[f] is None for f in FIELDS):
        c.execute("DELETE FROM reach_settings WHERE party_id=?", (party,))
    elif before:
        c.execute("UPDATE reach_settings SET mode=?, chats_per_day=?, tasks_per_day=?, updated_at=?, updated_by_id=? "
                  "WHERE party_id=?", (after["mode"], after["chats_per_day"], after["tasks_per_day"], now, who.actor,
                                        party))
    else:
        c.execute("INSERT INTO reach_settings(id, party_id, mode, chats_per_day, tasks_per_day, created_at, updated_at, "
                  "updated_by_id) VALUES(?,?,?,?,?,?,?,?)",
                  (H.new_id(), party, after["mode"], after["chats_per_day"], after["tasks_per_day"], now, now,
                   who.actor))
    core_before = {"party": party, **{f: before[f] for f in FIELDS}} if before else None
    core_after = None if all(after[f] is None for f in FIELDS) else {"party": party, **after}
    if core_before != core_after:
        audit_log.write(c, who.actor, "reach_setting:" + party, core_before, core_after, _via(who))
        H.event(c, who.actor, "reach.setting_changed", "reach_setting:" + party, {"fields": sorted(changes)})
    ctx = context(c)
    row = ctx["settings"].get(party)
    return {"setting": _setting_view(row) if row else {"party": party, **{f: None for f in FIELDS}},
            "effective": effective(ctx, party)}


def _rule_identity(who):
    """The identity a line's rule is written with: the caller, with the rights the line's own check already gave them
    (a team lead manages its people's lines, which org_rules alone would leave to admins)."""
    return dataclasses.replace(who, role="owner")


def _scope(party):
    if party == "*":
        return {"everyone": True}
    return {"names": [party]}


def _with(party):
    return {} if party == "*" else {"names": [party]}


def _rule_text(value):
    text = " ".join(str(value or "").split())
    return text or None


def _rule_write(c, auth, who, row, text, moved=False):
    """The line's rule after a change: added, edited or retired through org_rules, and its id (None for none). `moved`:
    the line's sender or receiver changed, so its rule's Who and With follow."""
    from . import org_rules as O
    acting = _rule_identity(who)
    current = c.execute("SELECT id, text, status, version FROM org_rules WHERE id=?",
                        (row["org_rule_id"],)).fetchone() if row.get("org_rule_id") else None
    live = current if current and current["status"] != "retired" else None
    if text is None:
        if live:
            O.decide_rule(c, auth, acting, live["id"], {"version": live["version"]}, "retire")
        return None
    if live:
        change = {"text": text} if live["text"] != text else {}
        if moved:
            change.update({"who": _scope(row["sender_id"]), "with": _with(row["receiver_id"])})
        if change:
            O.edit_rule(c, auth, acting, live["id"], {**change, "version": live["version"]})
        return live["id"]
    made = O.add_rule(c, auth, acting, {"kind": "internal", "text": text, "who": _scope(row["sender_id"]),
                                         "with": _with(row["receiver_id"]), "if_broken": "warn"})
    return made["id"]


def _line_event(c, who, action, row, detail=None):
    H.event(c, who.actor, "reach.line_" + action, "reach_line:" + row["id"],
            {"sender": row["sender_id"], "receiver": row["receiver_id"], **(detail or {})})


def add_line(c, auth, who, body, via=None):
    ctx = context(c)
    sender, receiver = party_of(c, body.get("sender"), ctx), party_of(c, body.get("receiver"), ctx)
    if sender == receiver:
        raise Problem("validation", "A line runs between two parties", 422)
    _require_edit(c, auth, who, sender, ctx)
    if c.execute("SELECT 1 FROM reach_lines WHERE sender_id=? AND receiver_id=?", (sender, receiver)).fetchone():
        raise Problem("conflict", "There is already a line from that sender to that receiver: change it instead", 409)
    if len(ctx["lines"]) >= LINES_MAX:
        raise Problem("validation", f"A company keeps up to {LINES_MAX} lines", 422)
    per_day = body.get("per_day")
    now = H.now()
    row = {"id": H.new_id(), "sender_id": sender, "receiver_id": receiver, "per_day": per_day, "org_rule_id": None}
    rule_id = _rule_write(c, auth, who, row, _rule_text(body.get("rule")))
    c.execute("INSERT INTO reach_lines(id, sender_id, receiver_id, per_day, org_rule_id, created_at, updated_at, "
              "created_by_id, updated_by_id) VALUES(?,?,?,?,?,?,?,?,?)",
              (row["id"], sender, receiver, per_day, rule_id, now, now, who.actor, who.actor))
    stored = _line_row(c, row["id"])
    audit_log.write(c, who.actor, "reach_line:" + row["id"], None, _line_core(stored), via or _via(who))
    _line_event(c, who, "added", stored, {"per_day": per_day})
    return stored


def edit_line(c, auth, who, line_id, changes, via=None):
    """Change a line's sender, receiver, per_day or rule (the fields `changes` names). A changed pair (a flipped
    direction, say) must be the caller's to change on both senders, and must not already have a line."""
    row = _line_row(c, line_id)
    ctx = context(c)
    _require_edit(c, auth, who, row["sender_id"], ctx)
    before = _line_core(row)
    sets = {}
    sender = party_of(c, changes["sender"], ctx) if changes.get("sender") is not None else row["sender_id"]
    receiver = party_of(c, changes["receiver"], ctx) if changes.get("receiver") is not None else row["receiver_id"]
    moved = (sender, receiver) != (row["sender_id"], row["receiver_id"])
    if moved:
        if sender == receiver:
            raise Problem("validation", "A line runs between two parties", 422)
        _require_edit(c, auth, who, sender, ctx)
        if c.execute("SELECT 1 FROM reach_lines WHERE sender_id=? AND receiver_id=? AND id<>?",
                     (sender, receiver, line_id)).fetchone():
            raise Problem("conflict", "There is already a line from that sender to that receiver: change it instead",
                          409)
        sets["sender_id"], sets["receiver_id"] = sender, receiver
    if "per_day" in changes:
        sets["per_day"] = changes["per_day"]
    moved_row = {**row, "sender_id": sender, "receiver_id": receiver}
    if "rule" in changes:
        sets["org_rule_id"] = _rule_write(c, auth, who, moved_row, _rule_text(changes["rule"]), moved)
    elif moved and row["org_rule_id"]:
        current = c.execute("SELECT text FROM org_rules WHERE id=? AND status<>'retired'",
                            (row["org_rule_id"],)).fetchone()
        if current:
            _rule_write(c, auth, who, moved_row, current["text"], moved)
    if not sets or all(row[k] == v for k, v in sets.items()):
        return row
    c.execute(f"UPDATE reach_lines SET {', '.join(k + '=?' for k in sets)}, updated_at=?, updated_by_id=? WHERE id=?",
              (*sets.values(), H.now(), who.actor, line_id))
    stored = _line_row(c, line_id)
    audit_log.write(c, who.actor, "reach_line:" + line_id, before, _line_core(stored), via or _via(who))
    _line_event(c, who, "changed", stored, {"fields": sorted(changes)})
    return stored


def remove_line(c, auth, who, line_id, via=None, checked=True):
    row = _line_row(c, line_id)
    if checked:
        _require_edit(c, auth, who, row["sender_id"], context(c))
    if row["org_rule_id"]:
        _rule_write(c, auth, who, row, None)
    c.execute("DELETE FROM reach_lines WHERE id=?", (line_id,))
    audit_log.write(c, who.actor, "reach_line:" + line_id, _line_core(row), None, via or _via(who))
    _line_event(c, who, "removed", row)
    return {"id": line_id, "removed": True}


# ----------------------------------------------------------------------------------------------- HTTP
class ReachSetting(Contract):
    """Any of the three; null clears it (the party takes it from above again)."""
    mode: Literal["open", "closed"] | None = None
    chats_per_day: int | None = Field(default=None, ge=0, le=MAX)
    tasks_per_day: int | None = Field(default=None, ge=0, le=MAX)


class ReachLine(Contract):
    sender: str = Field(min_length=1, max_length=210)
    receiver: str = Field(min_length=1, max_length=210)
    per_day: int | None = Field(default=None, ge=0, le=MAX)
    rule: str | None = Field(default=None, max_length=1000)


class ReachLineEdit(Contract):
    """The fields sent change: a full body (as the editor sends) may flip or move the pair too."""
    sender: str | None = Field(default=None, min_length=1, max_length=210)
    receiver: str | None = Field(default=None, min_length=1, max_length=210)
    per_day: int | None = Field(default=None, ge=0, le=MAX)
    rule: str | None = Field(default=None, max_length=1000)


class _Nothing(BaseModel):
    """The body of a route that takes none (mutate keys its idempotency on the body)."""


def install(app, store, auth, mutate):
    def fresh(who, line_id):
        with store.read() as c:
            ctx = context(c)
            return line_view(c, auth, who, _line_row(c, line_id), ctx)

    @app.get("/api/v2/reach")
    def reach_map(request: Request):
        """The reach map: defaults, the stored settings, each party's effective values (and the party each comes
        from), the lines, and the parties the caller may change. Naming a teammate the caller may not See leaves a
        setting, line or party out."""
        with store.read() as c:
            return listing(c, auth, request.state.identity)

    @app.put("/api/v2/reach/settings/{party}")
    def reach_setting(request: Request, party: str, body: ReachSetting):
        """Set any of mode, chats_per_day and tasks_per_day for one party; null clears a field."""
        who = request.state.identity
        auth.domain(who)
        changes = {k: getattr(body, k) for k in FIELDS if k in body.model_fields_set}

        def work(c):
            return save_setting(c, auth, who, party, changes)
        return mutate(request, body, work)

    @app.post("/api/v2/reach/lines")
    def reach_line_add(request: Request, body: ReachLine):
        """Add a line from a sender to a receiver (one per pair): per_day null (no count), 0 (blocks) or N, and an
        optional rule sentence, kept as an internal org rule."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            return {"id": add_line(c, auth, who, body.model_dump())["id"]}
        made = mutate(request, body, work)
        return {"line": fresh(who, made["id"])}

    @app.post("/api/v2/reach/lines/{line_id}")
    def reach_line_edit(request: Request, line_id: str, body: ReachLineEdit):
        """Change a line's sender, receiver, per_day or rule (only the fields sent; a null or empty rule retires it)."""
        who = request.state.identity
        auth.domain(who)
        changes = {k: getattr(body, k) for k in ("sender", "receiver", "per_day", "rule") if k in body.model_fields_set}

        def work(c):
            edit_line(c, auth, who, line_id, changes)
            return {"id": line_id}
        mutate(request, body, work)
        return {"line": fresh(who, line_id)}

    @app.delete("/api/v2/reach/lines/{line_id}")
    def reach_line_remove(request: Request, line_id: str):
        """Remove a line; its rule is retired."""
        who = request.state.identity
        auth.domain(who)
        return mutate(request, _Nothing(), lambda c: remove_line(c, auth, who, line_id))


__all__ = ["ensure", "effective", "install", "listing"]
