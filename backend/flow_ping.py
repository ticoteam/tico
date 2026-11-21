# pyright: strict
"""Ping: a person asks bots for the questions they have for them (docs/tasks.md, "Needs you"; skills/add-to-flow).

Each bot gets one message from the person, in its chat with them, pointing at the add-to-flow skill. The bot does the
thinking: its questions come back as ordinary tasks of its own, each with a ❓ to that person, in their Needs you
(reach.pinged lets a bot ask the person who pinged it for a day). Nothing here calls a model or keeps a table: the
message's `refs.add_to_flow` is the record, and a bot the same person pinged in the last COOLDOWN_MINUTES is skipped,
so a double press asks once.

Scopes: `reports` (the bots whose manager is the caller), `bot` (one bot) or `group` (a team's bots, the teams under it
included). Only bots the caller may read, never Agent Ops or the Assistant, never an archived bot.

`setup` (id: one area, or none for every open one) pings the bot that fills each open area of the company's setup
(backend/setup_gaps.py), Agent Ops included, with the setup message instead: the bot asks one ❓ at a time until the area
is filled (skills/setup-in-flow). Only for someone a setup gap waits on (the owner or an admin). A `reports` ping
carries the same setup message to those bots too, so "Ping your bots" picks setup up where it was left.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from . import hubdb as H_
from . import reach as R_
from . import setup_gaps as G_
from . import views as views_
from .store import Problem
from .api_contract import api_v2

Row = dict[str, Any]
# Untyped helpers, as Any so this strict module checks its own code (as backend/flow_opinions.py does).
H: Any = H_
R: Any = R_
G: Any = G_
views: Any = views_

REF = "add_to_flow"
SKILL = "skills/add-to-flow/SKILL.md"
SETUP_SKILL = "skills/setup-in-flow/SKILL.md"
COOLDOWN_MINUTES = 10
SCOPES = ("reports", "bot", "group", "setup")


def text(name: str) -> str:
    """The person's message to each bot. It names them: the questions are theirs, not the bot's manager's."""
    return (
        f"Any questions for me? Read Tico's {SKILL} (under HUB_DIR) and follow it. Ask me, {name}, not your "
        "manager: each question its own task, with a ❓ message to me. If nothing clears the bar, say so in one line."
    )


def setup_text(name: str, areas: list[str]) -> str:
    """The person's message to the bot that fills `areas` of the setup, most important first."""
    names = ", ".join(G.NAMES[a] for a in areas)
    return (
        f"Help me set up {names}. Read Tico's {SETUP_SKILL} (under HUB_DIR) and follow it: ask me, {name}, one "
        "short question at a time, marked ❓, and fill it in yourself."
    )


def setup_targets(c: Any, auth: Any, who: Any, area: str | None = None) -> list[tuple[Row, list[str]]]:
    """Each bot an open setup area waits on, with its areas in order: only for someone a gap waits on."""
    if area is not None and area not in G.AREAS:
        raise Problem("not_found", "No such setup area", 404)
    if not G.sets_up(auth, who):
        return []
    hidden = auth.unreadable_bots(c, who)
    found: dict[str, tuple[Row, list[str]]] = {}
    for gap in G.open_areas(c):
        bot = G.owner(c, gap) if area in (None, gap) else None
        if bot and bot["id"] not in hidden and bot.get("slug") not in hidden:
            found.setdefault(bot["id"], (bot, []))[1].append(gap)
    return list(found.values())


def _skipped(bot: Row) -> bool:
    # Agent Ops fixes bots and the Assistant is the person's own: neither has questions of the company's work to ask. A
    # bot that cannot take a message (planned, archived: H.say refuses it) is no target either, so it never stops the
    # ping to the rest.
    assistant = R.helpers()[-1]
    takes = bot.get("state") in ("active", *H.HELD_STATES)
    return not takes or bool({str(bot.get("slug") or ""), str(bot["id"])} & {H.FLEET_MAINTAINER, assistant})


def targets(c: Any, person: str, hidden: set[str], scope: str = "reports", ident: str | None = None) -> list[Row]:
    """The bots a ping in `scope` reaches, by name: those the person may read (`hidden`: the bot ids they may not)."""
    if scope not in SCOPES:
        raise Problem("invalid", "Ping your reports, one bot or one team", 422)
    if scope == "setup":
        return []
    ctx: Any = R.context(c)
    rows: list[Row] = [b for b in ctx["bots"].values() if not _skipped(b) and b["id"] not in hidden]
    if scope == "bot":
        found = H.resolve_bot(c, ident or "")
        rows = [b for b in rows if found and b["id"] == found["id"]]
        if not rows:
            raise Problem("not_found", "No bot you may read has that name", 404)
    elif scope == "group":
        if not ident or ident not in (ctx["roster"].get("org_groups") or {}):
            raise Problem("not_found", "No such team", 404)
        rows = [b for b in rows if "group:" + ident in R.chain(ctx, "bot:" + b["id"])]
    else:
        managers: dict[str, str] = R._managers(ctx)
        rows = [b for b in rows if managers.get("bot:" + b["id"]) == person]
    return sorted(rows, key=lambda b: str(b.get("display_name") or b.get("slug") or b["id"]).lower())


def _recent(c: Any, person: str, bot: str) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return (
        c.execute(
            "SELECT 1 FROM messages WHERE sender_id=? AND recipient_id=? AND created_at>=? "
            "AND json_extract(refs, '$.add_to_flow') IS NOT NULL LIMIT 1",
            (person, bot, since),
        ).fetchone()
        is not None
    )


def ping(
    c: Any, person: str, hidden: set[str], scope: str = "reports", ident: str | None = None, setup: Any = ()
) -> Row:
    """Message each bot in scope once; `pinged` and `recent` (skipped: pinged by this person a moment ago) by name.
    `setup`: setup_targets' (bot, areas) pairs, each sent the setup message (scope `setup` sends only those)."""
    name = H.actor_name(c, person) or "me"
    sends: list[tuple[Row, str, Row]] = [(b, text(name), {REF: True}) for b in targets(c, person, hidden, scope, ident)]
    if scope == "setup":
        sends = []
    taken = {b["id"] for b, _, _ in sends}
    for bot, areas in setup:
        words, refs = setup_text(name, areas), {REF: True, "setup": areas}
        sends = [(b, words, refs) if b["id"] == bot["id"] else (b, w, r) for b, w, r in sends]
        if bot["id"] not in taken:
            sends.append((bot, words, refs))
    out: Row = {"pinged": [], "recent": []}
    for bot, words, refs in sends:
        actor = "bot:" + bot["id"]
        named = {"id": bot["id"], "name": bot.get("display_name") or bot.get("slug") or bot["id"]}
        if _recent(c, person, actor):
            out["recent"].append(named)
            continue
        H.say(c, person, actor, words, refs=refs)
        out["pinged"].append(named)
    return out


def on_setup(c: Any, person: str, bot: str) -> str:
    """The one automatic ping, for a bot setup made (backend/setup_live.py start_setup, given once): recorded quiet,
    with no run of its own, because the bot's own setup task asks it to run the skill last, once its files exist.
    Returns the line that task ends with. The button's default scope: only a bot whose manager is `person` (a default
    company's team managers), so the owner is not asked by every bot at once; "" for any other."""
    managers: dict[str, str] = R._managers(R.context(c))
    if managers.get("bot:" + bot) != person:
        return ""
    name = H.actor_name(c, person) or "the owner"
    H.say(c, person, "bot:" + bot, text(name), refs={REF: True, "quiet": True})
    return f"- Last, follow Tico's `{SKILL}` once, for {name}, who set the company up: ask them, not your manager."


def install(app: Any, store: Any, auth: Any, mutate: Any) -> None:
    from fastapi import Request
    from pydantic import Field

    from .models import Contract

    class FlowPing(Contract):
        scope: str = Field(default="reports", max_length=20)
        id: str | None = Field(default=None, max_length=200)

    @app.post(
        "/api/v2/flow/ping",
        **api_v2(
            "Needs you",
            "pingFlowBots",
            "Ask bots for the questions they have for you (people only): `scope` `reports` (default: the bots you manage), `bot` (`id`: one bot) or `group` (`id`: a team's bots, the teams under it included). Only bots you may read; never Agent Ops or the Assistant. Each gets one message from you pointing at the add-to-flow skill; its questions come back as tasks of its own, each with one ask to you, in your Needs you. `setup` (`id`: one area of `GET /api/v2/setup/gaps`, or none for every open one; the owner or an admin) pings the bot that fills each open setup area, Agent Ops included, which asks you one ❓ at a time and fills it in; `reports` pings those bots too. A bot you pinged in the last 10 minutes is listed in `recent` and not messaged again. Calls no model",
            "FlowPinged",
        ),
    )
    def flow_ping(request: Request, body: FlowPing) -> Row:  # pyright: ignore[reportUnusedFunction]
        """Ask bots for their questions for the caller: their reports by default, one bot, or one team's bots."""
        who = request.state.identity
        views.human_only(who)

        def work(c: Any) -> Row:
            hidden = auth.unreadable_bots(c, who)
            setup = setup_targets(c, auth, who, body.id) if body.scope == "setup" else ()
            if body.scope == "reports":
                setup = setup_targets(c, auth, who)
            return ping(c, who.actor, hidden, body.scope, body.id, setup)

        return mutate(request, body, work)
