"""What Finance reads from and calls in the rest of Tico, typed at the border: the roster and org chart, the install
owner, the company's usage cap, the audit log and approvals. Every function takes the caller's connection."""
import json
import sqlite3
from collections.abc import Mapping
from typing import Any, cast

from ... import hubdb as _hubdb
from ..core.org import Org, Team

# Tico's own modules are untyped: Finance reaches them only through this file, typed at each call.
_H: Any = _hubdb

Row = Mapping[str, Any]
Refused: type[Exception] = _H.Refused


def new_id() -> str:
    return str(_H.new_id())


def now() -> str:
    return str(_H.now())


def shift(at: str, days: int) -> str:
    return str(_H.shift(at, days=days))


def _metadata(c: sqlite3.Connection, key: str) -> Any:
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def owner_person(c: sqlite3.Connection, fallback: str = "") -> str:
    """The install owner as `human:<id>`: the stored owner record's person, else `fallback` (the person Finance was
    set up for). Admins are not owners."""
    stored = _metadata(c, "owner")
    email = str(cast(dict[str, Any], stored).get("email") or "").strip().lower() if isinstance(stored, dict) else ""
    if email:
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
        if row:
            return "human:" + str(row[0])
    return fallback


def _actor(value: str, bots: set[str]) -> str:
    """A reports-to or lead reference as an actor: `bot:x`/`human:x` kept, `person:x` is a human, a bare slug of a
    bot is that bot, anything else a person id."""
    text = str(value or "").strip()
    if not text:
        return ""
    kind, _, ident = text.partition(":")
    if ident and kind in ("bot", "human"):
        return text
    if ident and kind == "person":
        return "human:" + ident
    return "bot:" + text if text in bots else "human:" + text


def org(c: sqlite3.Connection, owner: str) -> Org:
    """The org chart now: groups with their leads, who is in which group, and who reports to whom. Archived bots are
    off it."""
    from ... import groups, people
    G: Any = groups
    P: Any = people
    doc = _metadata(c, "people")
    roster = cast(dict[str, Any], P.load(doc if isinstance(doc, dict) else {"people": []}))
    configs: dict[str, dict[str, Any]] = {}
    archived = {str(r[0]) for r in c.execute("SELECT slug FROM bots WHERE state='archived'")}
    for row in c.execute("SELECT bot, config_json, reports_to FROM bot_config"):
        if str(row[0]) in archived:
            continue
        try:
            config = json.loads(row[1] or "{}")
        except ValueError:
            config = {}
        config = cast(dict[str, Any], config) if isinstance(config, dict) else {}
        config["reports_to"] = row[2] or config.get("reports_to") or ""
        configs[str(row[0])] = config
    slugs = set(configs)
    groups = cast(dict[str, dict[str, Any]], roster.get("org_groups") or {})
    teams: dict[str, Team] = {}
    for gid, group in groups.items():
        lead = str(G.lead_of(gid, roster, configs) or "")
        teams[gid] = Team(id=gid, name=str(group.get("name") or gid), parent=str(group.get("parent") or ""),
                          lead=_actor(lead, slugs))
    team_of: dict[str, str] = {}
    reports: dict[str, str] = {}
    humans: set[str] = set()
    for person in cast(list[dict[str, Any]], roster.get("people") or []):
        actor = "human:" + str(person.get("id") or "")
        humans.add(actor)
        if person.get("team") in teams:
            team_of[actor] = str(person["team"])
        if person.get("reports_to"):
            reports[actor] = _actor(str(person["reports_to"]), slugs)
    for row in c.execute("SELECT id FROM humans"):
        humans.add("human:" + str(row[0]))
    for slug, config in configs.items():
        actor = "bot:" + slug
        team = str(P.team_of(slug, configs, roster) or "")
        if team in teams:
            team_of[actor] = team
        if config.get("reports_to"):
            reports[actor] = _actor(str(config["reports_to"]), slugs)
    return Org(owner=owner, teams=teams, team_of=team_of, reports_to=reports, humans=frozenset(humans),
               bots=frozenset("bot:" + s for s in slugs))


def company_monthly_cap(c: sqlite3.Connection) -> object | None:
    """The company pool's monthly usage cap in USD (backend/usage_limits.py), or None when none is set."""
    from ... import usage_limits
    U: Any = usage_limits
    caps = cast(Mapping[str, object], U.pool(c, U.COMPANY))
    return caps.get("monthly_usd")


def audit(c: sqlite3.Connection, actor: str, action: str, target: str, detail: Mapping[str, object]) -> None:
    """The `events` row every Finance write leaves, in the same transaction."""
    _H.event(c, actor, action, target, dict(detail))


def approval_request(c: sqlite3.Connection, requester: str, kind: str, payload: Mapping[str, object],
                     to: str) -> str:
    """A Tico approval with no task, decided by `to` (one person). The id."""
    row = cast(Row, _H.approval_request(c, requester, kind, dict(payload), to=to))
    return str(row["id"])


def approval(c: sqlite3.Connection, approval_id: str) -> Row | None:
    return cast(Row | None, _H.approval(c, approval_id))


def approval_consume(c: sqlite3.Connection, actor: str, approval_id: str) -> None:
    _H.approval_consume(c, actor, approval_id)


def approval_withdraw(c: sqlite3.Connection, approval_id: str, note: str) -> None:
    _H.approval_withdraw(c, approval_id, note)


def payload_hash(payload: Mapping[str, object]) -> str:
    return str(_H.payload_hash(dict(payload)))


def known_actor(c: sqlite3.Connection, actor: str) -> bool:
    """Whether hubdb would accept `actor` as a writer (a roster person, a bot, or the keeper)."""
    kind, _, ident = actor.partition(":")
    if actor == "keeper":
        return True
    if kind == "human":
        return c.execute("SELECT 1 FROM humans WHERE id=?", (ident,)).fetchone() is not None
    if kind == "bot":
        return c.execute("SELECT 1 FROM bots WHERE slug=?", (ident,)).fetchone() is not None
    return False
