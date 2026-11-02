"""What a person's chat bot has learned (docs/assistant.md, docs/strategy-bot.md; spec: Strategy Bot v3, section 10.7),
in two layers.

- The personal layer: how one person likes things ranked and summarised. One per person, kept by their Assistant, so it
  goes with them when ownership moves on. Private to
  that person: only they read it, change it or delete it, and only a turn in their own chat is handed it. No one else,
  the owner and admins included, reads it, and it is not in SQL (backend/sql.py denies a table it does not name).
- The company layer: the vision, the company's priorities (its live company goals) and what the owner taught it about
  the company. Only the owner reads it and adds to it: on their own click, or in their own Assistant chat, whose turn
  there acts as them. The vision and the goals are changed where they live, on Goals. Only the owner's Assistant uses
  it, in the owner's chat; nobody else's Assistant sees it.

Teaching happens in chat: the bot saves one short line with `hub assistant learn` (POST /api/v2/assistant/learned) and
says so. The person edits and deletes entries on "What it has learned" (ui/app/learned.js). Nothing is cached: each turn
in a person's chat is handed both layers as they are when it starts (`for_turn`, backend/execution.py), so an entry
deleted now is gone from the next turn. A deleted entry is deleted, not kept: its audit event names it by id only, as do
the others, so no entry's words are in the audit log.

Unversioned and idempotent, like the task tabs (backend/bot_task_tabs.py): an older release never reads the table."""

import logging
import sqlite3

from fastapi import Request

from . import strategy_bot, rooms
from . import goals as G
from . import company as C
from . import models as M
from .store import H, Problem

log = logging.getLogger("tico.learned")

TEXT_MAX = 280            # one short line
LAYER_MAX = 100           # entries in one layer: every turn in the chat is handed all of them
PRIORITIES_MAX = 10       # live company goals in the company layer, in their order on Goals
LAYERS = ("personal", "company")

SCHEMA = """
CREATE TABLE IF NOT EXISTS learned_entries (
  id TEXT PRIMARY KEY, layer TEXT NOT NULL CHECK (layer IN ('personal','company')),
  person TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  via TEXT, updated_at TEXT, updated_by TEXT);
CREATE INDEX IF NOT EXISTS learned_entries_layer ON learned_entries(layer, person, created_at);
"""


def _person(who, settings):
    """The person whose layers these are: a person, on their own or through a turn of their own Assistant (backend/auth.py
    assistant_principal). Never a bot, nor a bot that borrows the person's rights for other work: BotOps doing what they
    asked is not their chat."""
    own_turn = (who.via == "assistant" and not who.confirmed and bool(who.attempt_id)
                and who.task_actor == "bot:" + settings.assistant_bot)
    if who.role not in ("owner", "human") or not H.is_human(who.actor) or (who.via and not own_turn):
        raise Problem("forbidden", "What a chat bot has learned belongs to its person: only they, or their own chat's "
                      "turn, read or change it", 403)
    return who.actor


def company_reader(c, who, settings):
    """The owner, once the company has its Strategy Bot: on their own, or in their own Assistant chat. Nobody else's
    Assistant, and no borrowed rights (BotOps acting for the owner)."""
    if who.role != "owner" or not H.is_human(who.actor):
        return False
    row = H.bot(c, strategy_bot.SLUG)
    if not row or row["state"] in ("archived", "planned"):
        return False
    return not who.via or (who.via == "assistant" and not who.confirmed and bool(who.attempt_id)
                           and who.task_actor == "bot:" + settings.assistant_bot)


def _view(row):
    return {"id": row["id"], "layer": row["layer"], "text": row["text"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "taught_in_chat": bool(row["via"])}


def entries(c, layer, person=""):
    return [_view(r) for r in c.execute("SELECT * FROM learned_entries WHERE layer=? AND person=? ORDER BY created_at",
                                        (layer, person if layer == "personal" else ""))]


def priorities(c):
    return [{"id": g["id"], "title": g["title"], "status": g.get("status")}
            for g in G.goals(c, owner=G.COMPANY, live_only=True)[:PRIORITIES_MAX]]


def company_layer(c):
    """The owner's alone (`company_reader`), so the vision is their strategy draft's (backend/strategy.py)."""
    from . import strategy
    profile = C.profile(c)
    return {"vision": strategy.vision(c, editor=True), "description": profile["description"], "priorities": priorities(c),
            "entries": entries(c, "company")}


def view(c, who, settings):
    person = _person(who, settings)
    return {"personal": entries(c, "personal", person),
            "company": company_layer(c) if company_reader(c, who, settings) else None}


def for_turn(c, settings, conversation, bot):
    """What a turn in a person's own chat is handed, read now: their personal layer, and in the owner's own Assistant
    chat the company layer too. Neither for any other turn (a task, a shared room, the Strategy Bot's goals session).
    Every turn, whoever it is for, also carries the team's published strategy as `strategy` (the text of GET
    /api/v2/strategy/summary, backend/strategy.py) once one is published; None when there is nothing to hand over."""
    from . import strategy
    layers = _layers(c, settings, conversation, bot)
    text = strategy.summary_text(c)
    if text:
        layers = {**(layers or {}), "strategy": text}
    if growth_facing(c, bot):
        # The brand and the go-to-market, for the bots whose words and plans reach customers (backend/brand_gtm.py).
        from . import brand_gtm
        for kind in brand_gtm.KINDS:
            text = brand_gtm.summary_text(c, kind)
            if text:
                layers = {**(layers or {}), kind: text}
    return layers


GROWTH_TEAMS = ("market", "sales", "growth", "partner")


def growth_facing(c, bot):
    """Whether a bot faces customers: it, or a team it sits in (up the team tree), is named for marketing, sales,
    growth or partnerships."""
    try:
        from . import people, views
        if any(word in str(bot or "").lower() for word in GROWTH_TEAMS):
            return True
        roster = views.roster(c)
        groups = (roster or {}).get("org_groups") or {}
        gid, seen = people.team_of(bot, views.entries(c), roster), set()
        while gid and gid not in seen:
            seen.add(gid)
            group = groups.get(gid) or {}
            if any(word in f"{gid} {group.get('name') or ''}".lower() for word in GROWTH_TEAMS):
                return True
            gid = group.get("parent")
    except (sqlite3.Error, LookupError, TypeError, ValueError, AttributeError):
        # A roster that does not parse leaves the turn without the brand, never without a turn.
        log.warning("growth-facing check failed for %s", bot, exc_info=True)
    return False


def _layers(c, settings, conversation, bot):
    conversation = conversation or {}
    owner = str(conversation.get("owner_actor") or "")
    if conversation.get("scope") != rooms.PERSONAL or not H.is_human(owner) or conversation.get("closed_at"):
        return None
    if bot != settings.assistant_bot or conversation.get("room_key") != rooms.ASSISTANT_ROOM:
        return None
    layers = {"personal": [e["text"] for e in entries(c, "personal", owner)]}
    if _is_owner(c, settings, owner):
        layers["company"] = {**{k: v for k, v in company_layer(c).items() if k != "entries"},
                             "priorities": [g["title"] for g in priorities(c)],
                             "taught": [e["text"] for e in entries(c, "company")]}
    return layers


def _is_owner(c, settings, actor):
    """Whether this person is the owner now, with the Strategy Bot there (the company layer's reader, `company_reader`):
    a turn in a former owner's chat is handed no company layer."""
    from . import access
    row = H.bot(c, strategy_bot.SLUG)
    if not row or row["state"] in ("archived", "planned"):
        return False
    owner = access.load_owner(c, settings)["email"]
    email = str((H.human(c, H.actor_id(actor)) or {}).get("email") or "").strip().lower()
    return bool(owner) and email == owner


def _clean(text):
    text = " ".join(str(text or "").split())
    if not text:
        raise Problem("validation", "Say what it learned in a few words", 422)
    if len(text) > TEXT_MAX:
        raise Problem("validation", f"Keep it to one short line, under {TEXT_MAX} characters", 422)
    return text


def _may_write(c, who, layer, settings):
    person = _person(who, settings)
    if layer == "company" and not company_reader(c, who, settings):
        raise Problem("forbidden", "Only the owner teaches their " + settings.assistant_name + " about the company, on "
                      "their own or in their own chat with it; an admin or a member cannot", 403)
    return person if layer == "personal" else ""


def add(c, who, layer, text, settings):
    if layer not in LAYERS:
        raise Problem("validation", "The layer is personal or company", 422)
    person = _may_write(c, who, layer, settings)
    text = _clean(text)
    H._writer(c, who.actor)
    same = c.execute("SELECT * FROM learned_entries WHERE layer=? AND person=? AND text=?",
                     (layer, person, text)).fetchone()
    if same:
        return _view(same)                # taught twice is one entry
    if c.execute("SELECT count(*) FROM learned_entries WHERE layer=? AND person=?", (layer, person)).fetchone()[0] \
            >= LAYER_MAX:
        raise Problem("too_many", f"That layer holds {LAYER_MAX} entries: delete some on What it has learned first", 409)
    row = {"id": H.new_id(), "layer": layer, "person": person, "text": text, "created_at": H.now(),
           "created_by": who.actor, "via": (who.task_actor or "") if who.via else None}
    c.execute("INSERT INTO learned_entries(id,layer,person,text,created_at,created_by,via) "
              "VALUES(:id,:layer,:person,:text,:created_at,:created_by,:via)", row)
    H.event(c, who.actor, "learned.added", row["id"], {"layer": layer})
    return _view({**row, "updated_at": None})


def _own_entry(c, who, eid, settings):
    """The entry, when it is this person's to change on their own click: their personal entry, or a company entry for
    the owner. Anyone else's is not found, so a refusal never says it is there."""
    if who.via:
        raise Problem("forbidden", "The person edits and deletes what it learned, on What it has learned", 403)
    person = _person(who, settings)
    row = c.execute("SELECT * FROM learned_entries WHERE id=?", (eid,)).fetchone()
    if not row or (row["layer"] == "personal" and row["person"] != person) \
            or (row["layer"] == "company" and not company_reader(c, who, settings)):
        raise Problem("not_found", "No such entry", 404)
    return row


def edit(c, who, eid, text, settings):
    row = _own_entry(c, who, eid, settings)
    text = _clean(text)
    H._writer(c, who.actor)
    c.execute("UPDATE learned_entries SET text=?, updated_at=?, updated_by=? WHERE id=?", (text, H.now(), who.actor, eid))
    H.event(c, who.actor, "learned.edited", eid, {"layer": row["layer"]})
    return _view(c.execute("SELECT * FROM learned_entries WHERE id=?", (eid,)).fetchone())


def delete(c, who, eid, settings):
    row = _own_entry(c, who, eid, settings)
    H._writer(c, who.actor)
    c.execute("DELETE FROM learned_entries WHERE id=?", (eid,))
    H.event(c, who.actor, "learned.deleted", eid, {"layer": row["layer"]})
    return {"deleted": eid}


def install(app, store, auth, mutate):
    @app.get("/api/v2/assistant/learned")
    def learned_show(request: Request):
        """What the caller's chat bot has learned: their personal layer, and for the owner the company layer."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return view(c, who, store.settings)

    @app.post("/api/v2/assistant/learned")
    def learned_add(request: Request, body: M.LearnedAdd):
        """Save one thing the person taught their chat bot, in their personal layer or (the owner only) the company's."""
        who = request.state.identity
        def work(c):
            auth.domain(who)
            return {"entry": add(c, who, body.layer, body.text, store.settings)}
        return mutate(request, body, work)

    @app.patch("/api/v2/assistant/learned/{eid}")
    def learned_edit(request: Request, eid: str, body: M.LearnedEdit):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            return {"entry": edit(c, who, eid, body.text, store.settings)}
        return mutate(request, body, work)

    @app.delete("/api/v2/assistant/learned/{eid}")
    def learned_delete(request: Request, eid: str):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            return delete(c, who, eid, store.settings)
        return mutate(request, M.Empty(), work)
