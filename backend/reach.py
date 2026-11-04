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
- Enforcement: `decide` is the one decision, pure (the context's roster, configs, settings and lines, plus the write's
  facts and a counter, in; allow or refuse with the reason out). `check` and `check_post` gather a bot's write's facts
  and raise the 409 on the write; `scripts/reach_dry_run.py` replays history through the same `decide`. Only a bot's
  own write is checked: a person's, the keeper's and the hub's own hand-offs (step flows, routines, rule wakes) never
  come through here. Task caps (backend/task_caps.py) are still their own table.

Tables: backend/migrations/0040_reach.sql (0045_reach_nearby.sql adds the `nearby` mode).
"""
import dataclasses
import os
from datetime import timedelta
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
# With no mode set anywhere in its chain a bot (and so a team and Everyone, which bots inherit from) reaches only its
# org-chart neighbors. A person reads open: nothing a person does is limited.
DEFAULTS = {"mode": "nearby", "chats_per_day": 999, "tasks_per_day": 999}


def bot_mode():
    """A bot's default mode: `nearby`, unless TICO_REACH_DEFAULT names another mode (conftest.py starts tests about
    something else `open`, since most test companies put their bots on no chart)."""
    mode = os.environ.get("TICO_REACH_DEFAULT", "")
    return mode if mode in ("open", "nearby", "closed") else DEFAULTS["mode"]
PERSON_MODE = "open"
MODES = ("open", "nearby", "closed")
LINES_MAX = 2000            # the lines one company keeps; the map answers them all at once

# ----------------------------------------------------------------------------------------------- the roster
def context(c):
    """What one read or check needs, read once: the roster, the bots' configs, the stored settings and lines."""
    roster = G._roster(c)
    return {"c": c, "roster": roster, "configs": G._configs(c), "settings": _settings(c), "lines": _lines(c),
            "owner": owner(c), "led": {}, "bots": {b["id"]: b for b in H.bots(c)}}


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
            default = PERSON_MODE if field == "mode" and party.startswith("human:") else (
                bot_mode() if field == "mode" else DEFAULTS[field])
            out[field], came[field] = default, None
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
        "defaults": {**DEFAULTS, "person_mode": PERSON_MODE},
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
            raise Problem("validation", "Mode is open, nearby or closed", 422)
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


# ----------------------------------------------------------------------------------------------- the chart around a bot
# The built-in helpers that work for the whole company from off the chart: what they send is never limited, and anyone
# may reach them (a bot that needs a fix asks BotOps). The Assistant's slug is the install's own (TICO_ASSISTANT_BOT).
# The built-in bots of Auth.system_bot plus the support helper, except the Health Monitor: its tasks to people far from
# it are what nearby reach is for, so it is checked like any bot.
HELPERS = ("botops", "task-manager", "goal-manager", "librarian", "support", "assistant", "bookkeeper", H.COUNSEL)
SOLICITED_DAYS = 7          # how far back a teammate's message to a bot makes the bot's answer to them solicited
NAMES_SHOWN = 6             # the teammates a refusal names as the ones the bot does reach


def helpers():
    return HELPERS + (os.environ.get("TICO_ASSISTANT_BOT", "").strip() or "coo",)


def _bot_row(ctx, ident):
    """A bot that is not archived, by id or slug, from the context's bots."""
    rows = ctx["bots"]
    if "_slugs" not in ctx:
        ctx["_slugs"] = {r.get("slug"): r for r in rows.values() if r.get("slug")}
    row = rows.get(ident) or ctx["_slugs"].get(ident)
    return row if row and row.get("state") != "archived" else None


def is_helper(ctx, actor):
    if not H.is_bot(actor):
        return False
    row = ctx["bots"].get(H.actor_id(actor)) or {}
    return H.actor_id(actor) in helpers() or row.get("slug") in helpers()


def _ref(ctx, raw):
    """A `reports_to` value as an actor: a bot (id or slug) that is not archived, or a person on the roster; else None."""
    text = str(raw or "").strip()
    if not text:
        return None
    kind, sep, ident = text.partition(":")
    if sep and kind in ("human", "person"):
        return "human:" + ident if P.person(ident, ctx["roster"]) else None
    row = _bot_row(ctx, ident if sep and kind == "bot" else text)
    if row:
        return "bot:" + row["id"]
    return "human:" + text if not sep and P.person(text, ctx["roster"]) else None


def _managers(ctx):
    """{actor: its manager's actor or None}: every bot that is not archived (`bot_config.reports_to`) and every person
    on the chart (`reports_to`, a person's id unless it names a bot). A bot with no manager on the chart counts as
    reporting to the company owner, so it reaches the owner and the other bots with no manager are its peers: a bot
    that reaches no one would go silent."""
    if "_managers" not in ctx:
        out, top = {}, ctx.get("owner")
        for bid, row in ctx["bots"].items():
            if row.get("state") != "archived":
                out["bot:" + bid] = _ref(ctx, (ctx["configs"].get(bid) or {}).get("reports_to")) or top
        for person in P.visible_people(ctx["roster"]):
            raw = str(person.get("reports_to") or "")
            out["human:" + person["id"]] = _ref(ctx, raw if ":" in raw or not raw else "human:" + raw)
        ctx["_managers"] = out
    return ctx["_managers"]


def _teams(ctx):
    if "_teams" not in ctx:
        ctx["_teams"] = {actor: _team(ctx, actor) for actor in _managers(ctx)}
    return ctx["_teams"]


def nearby(ctx, actor):
    """{teammate: why} a bot in `nearby` reaches: its manager, its direct reports, its peers (the same manager) and the
    members of the team it is in. A bot with no manager on the chart has the owner as its manager (`_managers`)."""
    managers, teams = _managers(ctx), _teams(ctx)
    boss, team = managers.get(actor), teams.get(actor) or ""
    out = {boss: "manager"} if boss else {}
    for other, theirs in managers.items():
        if other == actor or other in out:
            continue
        if theirs == actor:
            out[other] = "report"
        elif boss and theirs == boss:
            out[other] = "peer"
        elif team and teams.get(other) == team:
            out[other] = "team"
    return out


def line_for(ctx, sender, receiver):
    """The line that applies to this pair: the nearest one, the sender's side first (itself, its teams nearest first,
    Everyone), then the receiver's. None when no line names them."""
    senders, receivers = chain(ctx, sender), chain(ctx, receiver)
    best = None
    for line in ctx["lines"]:
        if line["sender_id"] in senders and line["receiver_id"] in receivers:
            rank = (senders.index(line["sender_id"]), receivers.index(line["receiver_id"]))
            if best is None or rank < best[0]:
                best = (rank, line)
    return best[1] if best else None


def name(ctx, actor):
    if H.is_bot(actor):
        row = ctx["bots"].get(H.actor_id(actor)) or {}
        return row.get("display_name") or row.get("slug") or H.actor_id(actor)
    if actor == "*":
        return "Everyone"
    if str(actor).startswith("group:"):
        return ((ctx["roster"].get("org_groups") or {}).get(actor[6:]) or {}).get("name") or actor[6:]
    return (P.person(H.actor_id(actor), ctx["roster"]) or {}).get("name") or H.actor_id(actor)


# ----------------------------------------------------------------------------------------------- the decision
@dataclasses.dataclass(frozen=True)
class Decision:
    """`why` says what let it through (person, self, system, helper, task, solicited, open, line, manager, report,
    peer, team) or, refused, is the code (reach_closed, reach_line, reach_cap). `via`: who to send it to instead."""
    allowed: bool
    why: str
    message: str = ""
    via: str | None = None
    can_reach: tuple = ()


def decide(ctx, sender, receiver, kind="task", *, system=False, on_task=False, solicited=False, count=None,
           unseen=frozenset()):
    """Whether `sender` may reach `receiver` with one write. Pure: the context's roster, configs, settings and lines,
    the write's facts, and `count(scope)` for what was already sent today (scope `("line", line)`, `("task",)` or
    `("chat",)`; none counted when it is None). `kind` is `task` (a task made, handed or asked on) or `chat` (a message
    or a note). Only a bot is ever refused; the owner as receiver is not exempt."""
    count = count or (lambda scope: 0)
    if not H.is_bot(sender):
        return Decision(True, "person")
    if sender == receiver:
        return Decision(True, "self")
    if system:
        return Decision(True, "system")
    if is_helper(ctx, sender) or is_helper(ctx, receiver):
        return Decision(True, "helper")
    if on_task:
        return Decision(True, "task")
    if solicited:
        return Decision(True, "solicited")
    eff = effective(ctx, sender)
    line, why = line_for(ctx, sender, receiver), "open"
    if line is not None and line["per_day"] is not None:
        if line["per_day"] == 0 or count(("line", line)) >= line["per_day"]:
            return _refused(ctx, "reach_line", sender, receiver, eff["mode"], line, unseen=unseen)
    if line is not None:
        why = "line"
    elif eff["mode"] != "open":
        near = nearby(ctx, sender) if eff["mode"] == "nearby" else {}
        if receiver not in near:
            return _refused(ctx, "reach_closed", sender, receiver, eff["mode"], unseen=unseen)
        why = near[receiver]
    field = "tasks_per_day" if kind == "task" else "chats_per_day"
    if count((kind,)) >= eff[field]:
        return _refused(ctx, "reach_cap", sender, receiver, eff["mode"], cap=(field, eff[field]), unseen=unseen)
    return Decision(True, why)


def _reached(ctx, sender, mode):
    """Who the sender does reach in its mode: its nearby teammates, then the receivers of its own lines."""
    near = list(nearby(ctx, sender)) if mode == "nearby" else []
    senders = chain(ctx, sender)
    for line in ctx["lines"]:
        if line["sender_id"] in senders and line["per_day"] != 0 and line["receiver_id"] not in near:
            near.append(line["receiver_id"])
    return near


def _refused(ctx, code, sender, receiver, mode, line=None, cap=None, unseen=frozenset()):
    """The refusal a bot reads: what it hit, that nothing was sent, and exactly where the work goes instead. Teammates
    the bot may not See (`unseen`) are never named, its manager included; the receiver it named itself."""
    boss = _managers(ctx).get(sender)
    via = boss if boss and boss != receiver and boss not in unseen else None
    reached = [a for a in _reached(ctx, sender, mode) if a != receiver and a not in unseen and not
               (a.startswith("group:") and a in unseen)]
    s, r = name(ctx, sender), name(ctx, receiver)
    if code == "reach_cap":
        field, limit = cap
        what = "tasks" if field == "tasks_per_day" else "chats"
        head = f"{s} has sent its {limit} {what} for today, its limit. Nothing was sent."
        tail = (" The count starts again at midnight: do not send more before then. A person who manages "
                f"{s} can raise the limit on Rules › Internal.")
        return Decision(False, code, head + tail, via, tuple(reached))
    shown = ", ".join(name(ctx, a) for a in reached[:NAMES_SHOWN]) + (" and others" if len(reached) > NAMES_SHOWN
                                                                       else "")
    if code == "reach_line":
        head = (f"A line on Rules › Internal blocks {s} from reaching {r}." if line["per_day"] == 0 else
                f"{s} has reached {r} {line['per_day']} times today, the most its line on Rules › Internal allows.")
    elif mode == "nearby":
        boss_is = ("the owner (it has no manager on the chart)" if boss and boss == ctx.get("owner") and not
                   _ref(ctx, (ctx["configs"].get(H.actor_id(sender)) or {}).get("reports_to")) else "its manager")
        head = f"{r} is outside {s}'s reach: it reaches only {boss_is}, its reports, its peers and its team"
        head += f" ({shown})." if shown else "."
    else:
        head = f"{r} is outside {s}'s reach: it reaches only who its lines on Rules › Internal name"
        head += f" ({shown})." if shown else "."
    if via:
        step = f" Send it to {name(ctx, via)}, who can pass it on."
    elif reached:
        step = f" {s} has no manager to pass it on: send it to one it reaches."
    else:
        step = (f" {s} has no manager and reaches no one: tell the person who runs it, in its own chat, that it needs a "
                "manager on the chart.")
    again = " Do not retry: sending it again is refused the same way."
    return Decision(False, code, head + " Nothing was sent." + step + again, via, tuple(reached))


# ----------------------------------------------------------------------------------------------- the write's facts
def midnight():
    from .task_caps import _midnight
    return _midnight()


def _held(c, task_id, field, current, before):
    """Who held a task's `field` (owner, waiting_on) at any point before `before`, and at that moment: the values its
    changes moved between, and its value then (the one the first later change moved away from, else today's)."""
    out, later = set(), None
    for r in c.execute("SELECT old, new, created_at FROM history_events WHERE subject_type='task' AND subject_id=? "
                       "AND kind=? ORDER BY created_at", (task_id, field)):
        if before and r["created_at"] >= before:
            later = r["old"] if later is None else later
            continue
        out |= {r["old"], r["new"]}
    out.add(current if later is None or not before else later)
    return out


def task_parties(c, task_id, before=None):
    """Everyone a task is shared with: its owner, requester, the one it waits on, its guest, everyone who owned it, and
    whoever posted in or is a member of its thread. `before`: who was on it then (the dry-run's history; thread members
    are left out, as their joins are not dated)."""
    row = c.execute("SELECT owner_id, requester_id, waiting_on, guest, conversation_id FROM tasks WHERE id=?",
                    (task_id,)).fetchone() if task_id else None
    if not row:
        return set()
    out = {row["requester_id"], row["guest"]}
    out |= _held(c, task_id, "owner", row["owner_id"], before)
    out |= _held(c, task_id, "waiting_on", row["waiting_on"], before)
    if row["conversation_id"]:
        out |= {r[0] for r in c.execute("SELECT DISTINCT sender_id FROM messages WHERE conversation_id=?"
                                        + (" AND created_at<?" if before else "") + " LIMIT 500",
                                        (row["conversation_id"], *((before,) if before else ())))}
        if not before and H._has_table(c, "thread_members"):
            out |= {r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=? LIMIT 500",
                                            (row["conversation_id"],))}
    return {a for a in out if a}


def on_task(c, task_id, sender, receiver, before=None):
    """Both the sender and the receiver are on this task: the write is about work they already share."""
    parties = task_parties(c, task_id, before)
    return sender in parties and receiver in parties


def solicited(c, sender, receiver, conversation_id=None, in_reply_to=None, before=None):
    """The receiver came to the sender first: it gave the sender an open task, or (in the last SOLICITED_DAYS) wrote
    to it, here when the write names a conversation (addressed to it or @mentioning it), or this is a reply to the
    receiver's message. `conversation_id` and `in_reply_to` may each be several (a sub-thread and its parent thread; a
    reply and the message its sub-thread hangs from). `before`: only what was written before then (the dry-run's
    history)."""
    for message_id in _many(in_reply_to):
        row = c.execute("SELECT sender_id FROM messages WHERE id=?", (message_id,)).fetchone()
        if row and row["sender_id"] == receiver:
            return True
    if c.execute("SELECT 1 FROM tasks WHERE requester_id=? AND owner_id=? AND status NOT IN ('done','closed','declined')"
                 + (" AND created_at<?" if before else "") + " LIMIT 1",
                 (receiver, sender, *((before,) if before else ()))).fetchone():
        return True
    until = before or H.now()
    since = (H.parse_ts(until) - timedelta(days=SOLICITED_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    args = [receiver, since, until, sender]
    where = "m.sender_id=? AND m.created_at>=? AND m.created_at<? AND (m.recipient_id=?"
    if H._has_table(c, "message_mentions"):
        where += " OR EXISTS(SELECT 1 FROM message_mentions x WHERE x.message_id=m.id AND x.actor=?)"
        args.append(sender)
    where += ")"
    places = _many(conversation_id)
    if places:
        where += f" AND m.conversation_id IN ({','.join('?' * len(places))})"
        args.extend(places)
    if c.execute(f"SELECT 1 FROM messages m WHERE {where} LIMIT 1", args).fetchone():
        return True
    # A DM the bot is in: what the receiver wrote there was written to the bot, and a person who wrote there brought
    # its members together.
    dms = [p for p in places if _is_dm(c, p)]
    return bool(dms) and c.execute(
        f"SELECT 1 FROM messages WHERE (sender_id=? OR sender_id LIKE 'human:%') AND created_at>=? AND created_at<? "
        f"AND conversation_id IN ({','.join('?' * len(dms))}) LIMIT 1", (receiver, since, until, *dms)).fetchone() \
        is not None


def _is_dm(c, conversation_id):
    from . import threads as T
    thread = T.thread_row(c, conversation_id)
    return bool(thread) and not thread.get("parent_id") and T.audience_of(T.root_of(c, thread)) in ("personal",
                                                                                                    "participants")


def _many(value):
    return [v for v in (value if isinstance(value, (list, tuple)) else (value,)) if v]


def task_of(c, task_id=None, conversation_id=None):
    """The task a write is on: the one it names, else the task whose thread it is written in."""
    if task_id or not conversation_id:
        return task_id
    row = c.execute("SELECT task_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return row["task_id"] if row else None


def members(ctx, party):
    """The teammates a party stands for: one, a team's (its nested teams' too), or None for Everyone."""
    if party == "*":
        return None
    if not party.startswith("group:"):
        return {party}
    return {a for a in _managers(ctx) if party in chain(ctx, a)}


def _in(column, actors, args):
    if actors is None:
        return "1=1"
    args.extend(sorted(actors))
    return f"{column} IN ({','.join('?' * len(actors))})" if actors else "0=1"


def counted(c, ctx, sender, scope, since=None, until=None, exclude=None):
    """What `sender` already sent today (or between `since` and `until`): its tasks to someone else, or its chats
    (messages that reached another teammate outside task threads and its own Home), or, for a line, every task, chat
    and note from the line's sender side to its receiver side. `exclude`: the message being checked after its own
    insert, which is not one already sent."""
    since = since or midnight()
    until = until or "9999"
    exclude = exclude or ""
    if scope[0] == "task":
        return c.execute("SELECT COUNT(*) FROM task_cap_log WHERE sender=? AND sent_at>=? AND sent_at<?",
                         (sender, since, until)).fetchone()[0]
    if scope[0] == "chat":
        return c.execute(
            "SELECT COUNT(*) FROM messages m LEFT JOIN conversations v ON v.id=m.conversation_id WHERE m.sender_id=? "
            "AND m.created_at>=? AND m.created_at<? AND m.id<>? AND v.task_id IS NULL AND coalesce(v.owner_id,'')<>? AND EXISTS("
            "SELECT 1 FROM message_deliveries d WHERE d.message_id=m.id AND d.recipient<>? "
            "AND (d.recipient LIKE 'bot:%' OR d.recipient LIKE 'human:%'))",
            (sender, since, until, exclude, sender, sender)).fetchone()[0]
    line = scope[1]
    froms, tos = members(ctx, line["sender_id"]), members(ctx, line["receiver_id"])
    total = 0
    args = [since, until]
    total += c.execute(f"SELECT COUNT(*) FROM task_cap_log WHERE sent_at>=? AND sent_at<? AND sender<>receiver AND "
                       f"{_in('sender', froms, args)} AND {_in('receiver', tos, args)}", args).fetchone()[0]
    args = [since, until, exclude]
    total += c.execute(f"SELECT COUNT(*) FROM message_deliveries d JOIN messages m ON m.id=d.message_id WHERE "
                       f"m.created_at>=? AND m.created_at<? AND m.id<>? AND d.recipient<>m.sender_id AND "
                       f"{_in('m.sender_id', froms, args)} AND {_in('d.recipient', tos, args)}", args).fetchone()[0]
    args = [since, until]
    total += c.execute(f"SELECT COUNT(*) FROM notes WHERE created_at>=? AND created_at<? AND message_id IS NULL AND "
                       f"{_in('from_actor', froms, args)} AND {_in('to_actor', tos, args)}", args).fetchone()[0]
    return total


# ----------------------------------------------------------------------------------------------- the gates
class Refused(Problem):
    """The 409 a bot's write gets when it is out of reach (`reach_closed`, `reach_line` or `reach_cap`). Nothing is
    written: the write's transaction goes with it."""

    def __init__(self, decision, receiver):
        super().__init__(decision.why, decision.message, 409, extra={
            "receiver": receiver, "via": decision.via, "can_reach": list(decision.can_reach[:20])})


REPLAY_AGENT = "org-rule-hold"   # org_rule_check.replay_identity: a held write a person chose to Send anyway


def _sender(who):
    """The bot a write is checked for, or None: a person, the keeper and a person's BotOps or Assistant are never. Nor
    is a held write a person sent anyway: it passed reach before it was held, and a person approved it since."""
    if getattr(who, "role", "") != "bot" or not H.is_bot(getattr(who, "actor", "")):
        return None
    return None if getattr(who, "agent", "") == REPLAY_AGENT else who.actor


def check(c, who, receiver, kind="task", *, task_id=None, conversation_id=None, in_reply_to=None, ctx=None,
          exclude=None):
    """Refuse (409, nothing written) a bot's task, hand-off, question or message to a teammate out of its reach. The
    exemptions that need the database (a task they share, a teammate who came to the bot first) are read only when the
    write would be refused without them."""
    sender = _sender(who)
    receiver = str(receiver or "")
    if not sender or receiver == sender or not receiver.startswith(("bot:", "human:")):
        return
    ctx = ctx or context(c)
    memo = {}

    def count(scope):
        key = (scope[0], scope[1]["id"]) if scope[0] == "line" else scope
        if key not in memo:
            memo[key] = counted(c, ctx, sender, scope, exclude=exclude)
        return memo[key]

    found = decide(ctx, sender, receiver, kind, count=count)
    if found.allowed:
        return
    task_id = task_of(c, task_id, _many(conversation_id)[0] if _many(conversation_id) else None)
    if (task_id and on_task(c, task_id, sender, receiver)) or solicited(c, sender, receiver, conversation_id,
                                                                        in_reply_to):
        return
    from .teammate_access import hidden
    unseen = hidden(c, who)
    if unseen:
        found = decide(ctx, sender, receiver, kind, count=count, unseen=frozenset(unseen))
    raise Refused(found, receiver)


def precheck(store, who, fn):
    """A gate run before the LLM checks, in a read: a refused write spends no judgment. Only the reach refusal is
    raised here; anything else the write itself reports."""
    if not _sender(who):
        return
    with store.read() as c:
        try:
            fn(c)
        except Refused:
            raise
        except Problem:
            return


def check_post(c, who, thread, mentioned, target, in_reply_to=None, ctx=None):
    """A bot's post in a thread (threads.post): everyone it reaches is checked, the one it is addressed to, those it
    @mentions who can read the thread, the other members of a DM and the bot whose Home it is. Followers' digests and
    a sub-thread's earlier posters are not counted: they follow the thread themselves. In a task's thread each is
    checked against the task, so writing to those already on it passes."""
    from . import threads as T
    if not _sender(who):
        return
    root = T.root_of(c, thread)
    audience = T.audience_of(root)
    receivers = [target] if target else []
    receivers += [a for a in mentioned if T.audience_has(c, thread, a)]
    if audience in ("personal", "participants") and not thread.get("parent_id"):
        receivers += T._dm_members(c, root)
    if audience == "home":
        receivers.append(T.home_owner(root))
    task_id = root.get("task_id") if audience == "task" else None
    # A sub-thread's post answers the message it hangs from, and whoever wrote to the bot in the thread above it.
    places = [thread["id"], thread.get("parent_id")]
    evidence = [in_reply_to, thread.get("parent_message_id")]
    ctx = ctx or context(c)
    for receiver in dict.fromkeys(r for r in receivers if r):
        check(c, who, receiver, "chat", task_id=task_id, conversation_id=places, in_reply_to=evidence, ctx=ctx)


def check_thread(c, who, thread_id, text, to=None, parent_message_id=None):
    """check_post before the write, from what a post names: its thread (the sub-thread under `parent_message_id`
    once there is one), the @mentions in its text and who it is addressed to."""
    from . import threads as T
    if not _sender(who):
        return
    thread = T.thread_row(c, thread_id)
    if thread is None:
        return
    if parent_message_id:
        sub = c.execute("SELECT id FROM conversations WHERE parent_message_id=?", (parent_message_id,)).fetchone()
        thread = T.thread_row(c, sub[0]) if sub else thread
    target = H.resolve_actor(c, to) if to else None      # a DM's other members check_post finds itself
    check_post(c, who, thread, [a for a, _ in T.mentions(c, thread, text, author=who.actor)], target,
               in_reply_to=parent_message_id)


def check_edit(c, who, message_id, text, viewer=None):
    """A bot's edit of its own message: the teammates it @mentions now and did not before get the message, so each
    must be in its reach, as on a post. The message must first be one the bot reads and wrote (`viewer`, as the edit
    itself checks): anything else is the edit's 404 or 403, never a refusal that names who it would reach."""
    from . import threads as T
    if not _sender(who):
        return
    actor, v = T._actor_of(viewer if viewer is not None else who)
    message = T._own_say(c, actor, v, message_id)
    thread = T.thread_row(c, message["conversation_id"])
    if thread is None:
        return
    had = {r[0] for r in c.execute("SELECT actor FROM message_mentions WHERE message_id=? AND in_audience=1",
                                   (message_id,))} if H._has_table(c, "message_mentions") else set()
    added = [a for a, _ in T.mentions(c, thread, text, author=who.actor) if a not in had]
    if added:
        check_post(c, who, thread, added, None, in_reply_to=message.get("in_reply_to"))


DELIVERED = ("dm", "mention", "ask", "assign", "addressed", "home")   # the deliveries a bot's post reaches someone by


def check_delivered(c, who, message_id, task_id=None, conversation_id=None, in_reply_to=None):
    """After a write in the bot's transaction: everyone the message was delivered to (addressed, mentioned, a DM's
    members, a Home's owner) must be in its reach. A refusal rolls the whole write back, so nothing is sent."""
    if not _sender(who) or not H._has_table(c, "message_deliveries"):
        return
    marks = ",".join("?" * len(DELIVERED))
    receivers = [r[0] for r in c.execute(
        f"SELECT DISTINCT recipient FROM message_deliveries WHERE message_id=? AND reason IN ({marks})",
        (message_id, *DELIVERED))]
    ctx = context(c) if receivers else None
    for receiver in receivers:        # it is already written: today's counts leave it out, so a cap of 1 allows one
        check(c, who, receiver, "chat", task_id=task_id, conversation_id=conversation_id, in_reply_to=in_reply_to,
              ctx=ctx, exclude=message_id)


# ----------------------------------------------------------------------------------------------- HTTP
class ReachSetting(Contract):
    """Any of the three; null clears it (the party takes it from above again)."""
    mode: Literal["open", "nearby", "closed"] | None = None
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


__all__ = ["check", "check_delivered", "check_edit", "check_post", "decide", "effective", "install", "listing", "nearby", "precheck"]
