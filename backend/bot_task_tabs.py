"""The tabs over a bot's task list (Task-list tabs 2/3): their names, their order and what each one shows.

A layout belongs to the bot, not to whoever looks: everyone who opens the bot's page sees the same tabs, and changing
one bot's tabs never moves another's. The Assistant is the exception, since its list is each person's own: there every
person keeps a layout of their own. A bot without a saved layout shows the four built-in tabs (Needs You, Doing,
Blocked, Done Recently; ui/app/bot-tasks.js).

A tab is either one of those built-ins, which may be renamed, moved or removed but keeps its own view, or a filter the
bot's owner (or the bot itself) wrote: which task types, which states, and whom a task waits on. Nothing else: the
filter is not a query language. There is no cap on how many tabs a bot keeps; MAX_TABS only bounds what one write
stores. The layout is display state only: it changes no task and no one's access, and a tab shows only tasks the
viewer could already read.
"""

import json

from fastapi import Request

from . import models as M
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_task_tabs (
  bot TEXT NOT NULL, viewer TEXT NOT NULL DEFAULT '', tabs_json TEXT NOT NULL,
  updated TEXT NOT NULL, updated_by TEXT NOT NULL, PRIMARY KEY (bot, viewer));
"""

DEFAULT = [{"builtin": "needs", "name": "Needs You"}, {"builtin": "active", "name": "Doing"},
           {"builtin": "blocked", "name": "Blocked"}, {"builtin": "done", "name": "Done Recently"}]


def personal(settings, bot):
    """Whether this bot's tabs are each viewer's own: only the Assistant's."""
    return bot == settings.assistant_bot


def viewer_key(settings, who, bot):
    return who.actor if personal(settings, bot) else ""


def may_edit(c, auth, settings, who, bot):
    """The Assistant's tabs: any person, for their own. Any other bot's: the bot in its own run, or a person who may
    manage it (the owner, an Admin, its owners, someone above it; `Auth.bot_manager`)."""
    if personal(settings, bot):
        return who.role in ("owner", "human")
    if who.role == "bot" and who.actor == "bot:" + bot and not who.via:
        return True
    return who.role == "owner" or auth.bot_manager(c, who, bot)


def read(c, settings, who, bot):
    row = c.execute("SELECT tabs_json,updated,updated_by FROM bot_task_tabs WHERE bot=? AND viewer=?",
                    (bot, viewer_key(settings, who, bot))).fetchone()
    tabs = json.loads(row["tabs_json"]) if row else DEFAULT
    return {"bot": bot, "tabs": tabs, "saved": bool(row), "personal": personal(settings, bot),
            "updated": row["updated"] if row else None, "updated_by": row["updated_by"] if row else None}


def clean(c, tabs):
    """The tabs as stored: each built-in at most once and without a filter; a filter's types must exist."""
    out, builtins = [], set()
    known = {r[0] for r in c.execute("SELECT id FROM task_types")}
    for tab in tabs:
        if tab.builtin:
            if tab.builtin in builtins:
                raise Problem("validation", "Each built-in tab can be listed once", 422)
            if tab.types or tab.states or tab.waiting_on:
                raise Problem("validation", "A built-in tab keeps its own filter", 422)
            builtins.add(tab.builtin)
            out.append({"builtin": tab.builtin, "name": tab.name})
            continue
        if not tab.states:
            raise Problem("validation", "Pick at least one state for " + tab.name, 422)
        if unknown := [t for t in tab.types if t not in known]:
            raise Problem("validation", "Unknown task type: " + ", ".join(unknown), 422)
        out.append({"name": tab.name, "types": list(dict.fromkeys(tab.types)),
                    "states": list(dict.fromkeys(tab.states)), "waiting_on": tab.waiting_on})
    return out


def install(app, store, auth, mutate, settings):
    @app.get("/api/v2/bots/{bot}/task-tabs")
    def task_tabs(request: Request, bot: str):
        """The tabs over this bot's task list, in order (the defaults until someone saves a layout), and whether the
        caller may change them. The Assistant's are the caller's own."""
        who = request.state.identity
        with store.read() as c:
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return {**read(c, settings, who, bot), "can_edit": may_edit(c, auth, settings, who, bot)}

    @app.put("/api/v2/bots/{bot}/task-tabs")
    def task_tabs_save(request: Request, bot: str, body: M.BotTaskTabs):
        """Save the bot's tabs: the whole list, in order. `tabs: null` goes back to the four built-in tabs."""
        def work(c):
            who = request.state.identity
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            if not may_edit(c, auth, settings, who, bot):
                raise Problem("forbidden", "Only the bot or a person who manages it changes its tabs", 403)
            viewer = viewer_key(settings, who, bot)
            if body.tabs is None:
                c.execute("DELETE FROM bot_task_tabs WHERE bot=? AND viewer=?", (bot, viewer))
            else:
                if not body.tabs:
                    raise Problem("validation", "Keep at least one tab", 422)
                c.execute("INSERT INTO bot_task_tabs(bot,viewer,tabs_json,updated,updated_by) VALUES(?,?,?,?,?) "
                          "ON CONFLICT(bot,viewer) DO UPDATE SET tabs_json=excluded.tabs_json,updated=excluded.updated,"
                          "updated_by=excluded.updated_by",
                          (bot, viewer, json.dumps(clean(c, body.tabs)), H.now(), who.actor))
            if not viewer:
                H.event(c, who.actor, "bot.task_tabs_changed", bot,
                        {"tabs": None if body.tabs is None else len(body.tabs)})
            return {**read(c, settings, who, bot), "can_edit": True}
        return mutate(request, body, work)
