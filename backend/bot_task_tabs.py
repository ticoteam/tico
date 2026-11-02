"""A bot's sidebar layout (Task-list tabs 2/3, Editable bot sidebar): the order of its widgets, which are hidden and how
tall each may grow, and the tabs over its task list, with their names, their order and what each one shows.

A layout belongs to the bot, not to whoever looks: everyone who opens the bot's page sees the same sidebar, and
changing one bot's never moves another's. The Assistant is the exception, since its list is each person's own: there
every person keeps a layout of their own. A bot without a saved layout shows Tasks, Updates, Files and Routines in that
order with the four default tabs (Needs You, Doing, Blocked, Assigned to Others).

Needs You, Doing and Blocked are editable filters with default conditions. Their built-in key preserves identity
for Reset to default and Needs You also keeps the bot's questions and approvals. Done Recently keeps its own view.
Filters are rows of field and value that a task must all match, over the
tasks the bot holds or asked for. The fields are a fixed list (FIELDS), not a query language. "me" and "others" mean
the person looking, so the server counts each filter for its viewer. There is no cap on how many tabs a bot keeps;
only the request body limit (backend/app.py) bounds one write. The layout is display state only: it changes no task
and no one's access, and a tab shows only tasks the viewer could already read.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import Request

from . import models as M
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_task_tabs (
  bot TEXT NOT NULL, viewer TEXT NOT NULL DEFAULT '', tabs TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, PRIMARY KEY (bot, viewer));
"""

ACTIVE = ["open", "doing", "waiting"]
FINISHED = ("done", "closed", "declined")
# Finished tasks a filter shows unless it says Done within: the last two weeks, as Done Recently.
RECENT_DAYS = 14
# Rows sent per filter tab in one read; its count is exact, and `next` reads the rest a page at a time.
ROWS = 200

DEFAULT_CONDITIONS = {
    "needs": [{"field": "needs", "value": "you"}],
    "active": [{"field": "assigned_to", "value": "this_bot"}, {"field": "status", "value": ["open", "doing"]}],
    "blocked": [{"field": "assigned_to", "value": "this_bot"}, {"field": "status", "value": ["waiting"]},
                {"field": "waiting_on", "value": "not_me"}],
}

DEFAULT = [{"builtin": "needs", "name": "Needs You", "show_count": True},
           {"builtin": "active", "name": "Doing", "show_count": True},
           {"builtin": "blocked", "name": "Blocked", "show_count": True},
           {"name": "Assigned to Others", "show_count": True, "conditions": [
               {"field": "asked_by", "value": "this_bot"}, {"field": "assigned_to", "value": "others"},
               {"field": "status", "value": ACTIVE}]}]
WIDGETS = ("tasks", "updates", "files")            # an old saved "routines" is dropped: they are automations
DEFAULT_WIDGETS = [{"id": w, "hidden": False, "height": None} for w in WIDGETS]
# Pages and cards pinned to the sidebar (backend/cards.py): any number, beside or instead of the built-ins.
ITEM = re.compile(r"(page|card):[A-Za-z0-9_.-]{1,100}")

# field -> what its value is. Lists are "any of"; every row of a tab must match.
FIELDS = {"status": "statuses", "step": "steps", "type": "types", "assigned_to": "party", "asked_by": "party",
          "waiting_on": "waiting", "needs": "attention", "created_within": "days", "updated_within": "days", "done_within": "days",
          "due": "due", "label": "label"}
WAITING = ("any", "person", "bot", "me", "not_me")


def personal(settings, bot):
    """Whether this bot's layout is each viewer's own: only the Assistant's."""
    return bot == settings.assistant_bot


def viewer_key(settings, who, bot):
    return who.actor if personal(settings, bot) else ""


def may_edit(c, auth, settings, who, bot):
    """The Assistant's layout: any person, for their own. Any other bot's: the bot in its own run, or a person who may
    manage it (the owner, an Admin, its owners, someone above it; `Auth.bot_manager`)."""
    if personal(settings, bot):
        return who.role in ("owner", "human")
    if who.role == "bot" and who.actor == "bot:" + bot and not who.via:
        return True
    return who.role == "owner" or auth.bot_manager(c, who, bot)


def legacy(tab):
    """A tab as stored before filters had rows (Task-list tabs 2/3: types, states, whom it waits on, over the bot's
    own tasks), as the rows that show the same tasks."""
    if tab.get("builtin"):
        out = {"builtin": tab["builtin"], "name": tab["name"], "show_count": tab.get("show_count", True)}
        if tab["builtin"] in DEFAULT_CONDITIONS:
            out["conditions"] = tab.get("conditions", DEFAULT_CONDITIONS[tab["builtin"]])
        return out
    if "conditions" in tab:
        return {"name": tab["name"], "show_count": tab.get("show_count", True), "conditions": tab["conditions"]}
    rows = [{"field": "assigned_to", "value": "this_bot"}, {"field": "status", "value": list(tab.get("states") or [])}]
    if tab.get("types"):
        rows.append({"field": "type", "value": list(tab["types"])})
    if tab.get("waiting_on"):
        rows.append({"field": "waiting_on", "value": tab["waiting_on"]})
    return {"name": tab["name"], "show_count": True, "conditions": rows}


def widgets_of(stored):
    """The saved widgets in order, built-ins, pages and cards, with any built-in the saved list lacks after them as it
    comes by default."""
    out, seen = [], set()
    for w in stored or []:
        wid = str(w.get("id") or "")
        if (wid in WIDGETS or ITEM.fullmatch(wid)) and wid not in seen:
            seen.add(w["id"])
            out.append({"id": w["id"], "hidden": bool(w.get("hidden")), "height": w.get("height")})
    return out + [dict(w) for w in DEFAULT_WIDGETS if w["id"] not in seen]


def stored(c, settings, who, bot):
    return c.execute("SELECT tabs,widgets,updated_at,updated_by FROM bot_task_tabs WHERE bot=? AND viewer=?",
                     (bot, viewer_key(settings, who, bot))).fetchone()


def read(c, settings, who, bot, ctx=None):
    """`ctx` (backend/cards.py Ctx) leaves out the pages its reader may not read, so a widget never names one."""
    row = stored(c, settings, who, bot)
    tabs = [legacy(t) for t in json.loads(row["tabs"])] if row else [legacy(t) for t in DEFAULT]
    widgets = widgets_of(json.loads(row["widgets"]) if row and row["widgets"] else None)
    if ctx is not None:
        from . import cards
        widgets = [w for w in widgets if cards.readable_item(ctx, w["id"])]
    return {"bot": bot, "tabs": tabs, "widgets": widgets, "saved": bool(row), "personal": personal(settings, bot),
            "updated_at": row["updated_at"] if row else None, "updated_by": row["updated_by"] if row else None}


def bad(message):
    return Problem("validation", message, 422)


def names(value, label):
    """A list value ("any of"), or one value; never empty."""
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
        raise bad(f"{label} needs one or more values")
    return list(dict.fromkeys(values))


def days(value, label, low=1):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= 3650:
        raise bad(f"{label} is a number of days from {low} to 3650")
    return value


def condition(c, row):
    """One row, checked and in its stored form: unknown fields and values are refused, named people and bots must
    exist."""
    field, value = row.field, row.value
    kind = FIELDS[field]
    if kind == "statuses":
        value = names(value, "Status")
        if unknown := [v for v in value if v not in H.TASK_STATUSES]:
            raise bad("Unknown status: " + ", ".join(unknown))
    elif kind in ("steps", "types"):
        value = names(value, "Step" if kind == "steps" else "Type")
        table = "task_steps" if kind == "steps" else "task_types"
        known = {r[0] for r in c.execute(f"SELECT id FROM {table} WHERE id IN (SELECT value FROM json_each(?))",
                                         (json.dumps(value),))}
        if unknown := [v for v in value if v not in known]:
            raise bad(f"Unknown {'step' if kind == 'steps' else 'task type'}: " + ", ".join(unknown))
    elif kind == "party":
        mine = ("this_bot", "me", "others") if field == "assigned_to" else ("this_bot", "me")
        if not isinstance(value, str) or not value:
            raise bad("Pick whom for " + field.replace("_", " "))
        if value not in mine:
            found = H.resolve_bot(c, value) if H.is_bot(value) else None
            value = H.bot_actor(found["id"]) if found else value
            actor = value if H.actor_kind(value) in ("bot", "human") else None
            if not actor or H.resolve_actor(c, actor) != actor:
                raise bad("Unknown person or bot: " + value)
    elif kind == "waiting":
        if value not in WAITING:
            raise bad("Waiting on is any, person, bot, me or not_me")
    elif kind == "attention":
        if value != "you":
            raise bad("Needs is you")
    elif kind == "days":
        value = days(value, field.replace("_", " ").capitalize())
    elif kind == "due":
        if value != "overdue":
            value = days(value, "Due within", 0)
    elif kind == "label":
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60:
            raise bad("A label is 1 to 60 characters")
        value = value.strip().lower()
    return {"field": field, "value": value}


def clean(c, tabs):
    """Each default tab at most once; every editable condition uses the same validation."""
    out, builtins = [], set()
    for tab in tabs:
        if tab.builtin:
            if tab.builtin in builtins:
                raise bad("Each built-in tab can be listed once")
            if tab.types or tab.states or tab.waiting_on or (tab.builtin == "done" and tab.conditions):
                raise bad("A built-in tab cannot use legacy fields; Done Recently keeps its own filter")
            builtins.add(tab.builtin)
            item = {"builtin": tab.builtin, "name": tab.name, "show_count": tab.show_count}
            if tab.builtin in DEFAULT_CONDITIONS:
                item["conditions"] = ([condition(c, r) for r in tab.conditions] if "conditions" in tab.model_fields_set
                                      else DEFAULT_CONDITIONS[tab.builtin])
            out.append(item)
            continue
        if tab.conditions and (tab.types or tab.states or tab.waiting_on):
            raise bad("A tab has rows or types and states, not both")
        if not tab.conditions and (tab.types or tab.states or tab.waiting_on):
            # Task-list tabs 2/3's shape, from a client that still sends it.
            if not tab.states:
                raise bad("Pick at least one state for " + tab.name)
            rows = legacy({"name": tab.name, "types": tab.types, "states": tab.states, "waiting_on": tab.waiting_on})["conditions"]
            tab = tab.model_copy(update={"conditions": [M.BotTaskCondition(**r) for r in rows]})
        out.append({"name": tab.name, "show_count": tab.show_count, "conditions": [condition(c, r) for r in tab.conditions]})
    return out


def clean_widgets(widgets):
    ids = [w.id for w in widgets]
    if len(ids) != len(set(ids)):
        raise bad("Each widget can be listed once")
    return widgets_of([w.model_dump() for w in widgets])


def keep_unread(ctx, before, after):
    """The saved pages a writer may not read stay where they were: a save never drops what it could not see."""
    from . import cards
    sent = {w["id"] for w in after}
    out = list(after)
    for at, w in enumerate(before):
        if w["id"] not in sent and not cards.readable_item(ctx, w["id"]):
            out.insert(min(at, len(out)), w)
    return out


def write_widgets(ctx, bot, widgets):
    """Save the sidebar's widgets (the tabs as they are, the defaults when none are saved) and its pins with them."""
    from . import cards
    c, settings, who = ctx.c, ctx.settings, ctx.who
    if any(ITEM.fullmatch(w["id"]) for w in widgets) and personal(settings, bot):
        raise bad("The Assistant's sidebar holds its own widgets; pin pages to a bot or a team page")
    was = stored(c, settings, who, bot)
    tabs = was["tabs"] if was else json.dumps([legacy(t) for t in DEFAULT])
    c.execute("INSERT INTO bot_task_tabs(bot,viewer,tabs,widgets,updated_at,updated_by) VALUES(?,?,?,?,?,?) "
              "ON CONFLICT(bot,viewer) DO UPDATE SET widgets=excluded.widgets,updated_at=excluded.updated_at,"
              "updated_by=excluded.updated_by", (bot, viewer_key(settings, who, bot), tabs, json.dumps(widgets),
                                                 H.now(), who.actor))
    if not personal(settings, bot):
        cards.sync_bot(ctx, bot, widgets)


def saved_widgets(ctx, bot):
    row = stored(ctx.c, ctx.settings, ctx.who, bot)
    return widgets_of(json.loads(row["widgets"]) if row and row["widgets"] else None)


def add_widget(ctx, bot, item, position=None):
    widgets = saved_widgets(ctx, bot)
    if any(w["id"] == item for w in widgets):
        raise Problem("conflict", "Already pinned here", 409)
    if len([w for w in widgets if ITEM.fullmatch(w["id"])]) >= M.SIDEBAR_ITEMS:
        raise Problem("too_many", f"A sidebar holds {M.SIDEBAR_ITEMS} pages and cards", 409)
    entry = {"id": item, "hidden": False, "height": None}
    widgets.insert(len(widgets) if position is None else min(position, len(widgets)), entry)
    write_widgets(ctx, bot, widgets)


def remove_widget(ctx, bot, item):
    write_widgets(ctx, bot, [w for w in saved_widgets(ctx, bot) if w["id"] != item])


def order_widgets(ctx, bot, items):
    """The pages and cards in this order, in the slots they hold among the built-ins."""
    widgets = saved_widgets(ctx, bot)
    by_id, queue = {w["id"]: w for w in widgets}, iter(items)
    write_widgets(ctx, bot, [by_id[next(queue)] if ITEM.fullmatch(w["id"]) else w for w in widgets])


# ---- what a filter shows

def ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%S")


def party_sql(column, value, bot, viewer):
    if value == "this_bot":
        return f"{column}=?", ["bot:" + bot]
    if value == "me":
        return f"{column}=?", [viewer]
    if value == "others":
        return f"coalesce({column},'') NOT IN (?,?)", [viewer, "bot:" + bot]
    return f"{column}=?", [value]


def bot_wait_sql(visible_sql):
    """A bot wait uses the same readable, ordered blockers as the task's displayed wait line.

    Person waits/questions and gate/worktree holds keep their own meaning, even with a bot blocker.
    Never infer a bot from waiting_on: that column stores people only.
    """
    blockers = ("SELECT b.owner_id FROM task_relations r JOIN tasks b ON b.id=r.from_task "
                "WHERE r.to_task=tasks.id AND r.kind='blocks' AND b.status NOT IN ('done','closed','declined') "
                f"AND EXISTS (SELECT 1 FROM tasks WHERE tasks.id=b.id AND ({visible_sql}))")
    return ("status='waiting' AND coalesce(waiting_on,'') NOT LIKE 'human:%' "
            "AND coalesce(blocked_kind,'') NOT IN ('gate','worktree') "
            f"AND ({blockers} ORDER BY r.created_at,r.rowid LIMIT 1) LIKE 'bot:%' "
            f"AND NOT EXISTS ({blockers} AND b.owner_id LIKE 'human:%') "
            "AND NOT EXISTS (SELECT 1 FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE m.conversation_id=tasks.conversation_id AND {H.MESSAGE_TASK_SQL}=tasks.id "
            f"AND m.kind='ask' AND m.deleted_at IS NULL AND {H.OPEN_ASK_SQL} "
            "AND m.recipient_id LIKE 'human:%' AND coalesce(m.body,'')!='')")


def tab_sql(tab, bot, viewer, visible_sql="1"):
    """A filter's WHERE over the bot's tasks: every row, then finished tasks only for the window it names (two weeks
    when it names none) and never a routine's finished runs."""
    where, args, window = ["id IN (SELECT id FROM tasks WHERE owner_id=? UNION SELECT id FROM tasks WHERE requester_id=?)"], \
        ["bot:" + bot, "bot:" + bot], RECENT_DAYS
    for row in tab["conditions"]:
        field, value = row["field"], row["value"]
        many = lambda column: (f"{column} IN (SELECT value FROM json_each(?))", [json.dumps(value)])  # noqa: E731
        if field == "status":
            part = many("status")
        elif field == "step":
            part = many("step_id")
        elif field == "type":
            part = many("type_id")
        elif field == "assigned_to":
            part = party_sql("owner_id", value, bot, viewer)
        elif field == "asked_by":
            part = party_sql("requester_id", value, bot, viewer)
        elif field == "needs":
            part = "((owner_id=? AND status IN ('open','doing','waiting')) OR (status='waiting' AND waiting_on=?))", [viewer, viewer]
        elif field == "waiting_on":
            part = {"any": ("status='waiting'", []),
                    "person": ("status='waiting' AND waiting_on LIKE 'human:%'", []),
                    "bot": (bot_wait_sql(visible_sql), []),
                    "me": ("status='waiting' AND waiting_on=?", [viewer]),
                    "not_me": ("status='waiting' AND coalesce(waiting_on,'')!=?", [viewer])}[value]
        elif field == "created_within":
            part = "created_at>=?", [ago(value)]
        elif field == "updated_within":
            part = "updated_at>=?", [ago(value)]
        elif field == "done_within":
            window = None
            part = "coalesce(done_at,closed_at,'')>=?", [ago(value)]
        elif field == "due":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if value == "overdue":
                part = ("coalesce(due_at,'')!='' AND substr(due_at,1,10)<? AND status NOT IN ('done','closed','declined')",
                        [today])
            else:
                until = (datetime.now(timezone.utc) + timedelta(days=value)).strftime("%Y-%m-%d")
                part = "coalesce(due_at,'')!='' AND substr(due_at,1,10) BETWEEN ? AND ?", [today, until]
        else:  # label
            part = ("EXISTS (SELECT 1 FROM task_tags JOIN tags ON tags.id=task_tags.tag_id "
                    "WHERE task_tags.task_id=tasks.id AND tags.key=?)", [value])
        where.append(part[0])
        args += part[1]
    finished = "status IN ('done','closed','declined')"
    if window:
        where.append(f"(NOT {finished} OR coalesce(done_at,closed_at,updated_at,'')>=?)")
        args.append(ago(window))
    where.append(f"NOT ({finished} AND (EXISTS (SELECT 1 FROM schedule_occurrences s WHERE s.task_id=tasks.id) "
                 "OR coalesce(body,'') LIKE '%from schedule%'))")
    return " AND ".join(where), args


BRIEF = ("body", "acceptance_criteria", "acceptance", "public_title", "public_summary", "requester_count",
         "external_requesters", "internal_requesters", "important_requesters", "organization", "size")


def install(app, store, auth, mutate, settings, task_views=None):
    from .cards import Ctx

    @app.get("/api/v2/bots/{bot}/task-tabs")
    def task_tabs(request: Request, bot: str):
        """The bot's sidebar: its widgets in order and its task tabs in order (the defaults until someone saves a
        layout), and whether the caller may change them. The Assistant's are the caller's own."""
        who = request.state.identity
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            return {**read(c, settings, who, bot, Ctx(c, auth, who, settings)),
                    "can_edit": may_edit(c, auth, settings, who, bot)}

    @app.get("/api/v2/bots/{bot}/task-tabs/tasks")
    def task_tabs_tasks(request: Request, bot: str, tab: str = "", before: str = "", match_ids: str = ""):
        """What each filter tab shows the caller: its count and its newest tasks, among the tasks the bot holds or
        asked for that the caller may read, with `next` when it has more. `tab` (a key, c0, c1…) reads that tab alone
        and `before` (a tab's `next`) its next page. Default filters use their stable built-in keys;
        Done Recently is drawn by the page from its own reads. `match_ids` optionally checks supplemental
        question IDs against Needs You, so its count can deduplicate even beyond the current page."""
        who = request.state.identity
        auth.domain(who)
        after = before.rsplit("|", 1) if before else None
        if after is not None and (not tab or len(after) != 2):
            raise bad("before needs its tab and is a tab's next")
        with store.read_transaction() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            visible = auth.task_sql(c, who)
            out, ids = [], []
            for at, layout_tab in enumerate(read(c, settings, who, bot)["tabs"]):
                key = layout_tab.get("builtin") or "c" + str(at)
                if key == "done" or tab and tab != key:
                    continue
                where, args = tab_sql(layout_tab, bot, who.actor, visible)
                where = f"({visible}) AND {where}"
                count = c.execute(f"SELECT count(*) FROM tasks WHERE {where}", args).fetchone()[0]
                page, page_args = where, list(args)
                if after:
                    page += " AND (created_at<? OR created_at=? AND id<?)"
                    page_args += [after[0], after[0], after[1]]
                found = c.execute(f"SELECT id, created_at FROM tasks WHERE {page} ORDER BY created_at DESC, id DESC "
                                  "LIMIT ?", (*page_args, ROWS + 1)).fetchall()
                more = len(found) > ROWS
                found = found[:ROWS]
                matched = []
                if key == "needs" and match_ids:
                    matched = [r[0] for r in c.execute(f"SELECT id FROM tasks WHERE {where} "
                               "AND id IN (SELECT value FROM json_each(?))", (*args, json.dumps(match_ids.split(","))))]
                out.append({"key": key, "name": layout_tab["name"], "count": count, "ids": [r[0] for r in found],
                            "next": f"{found[-1][1]}|{found[-1][0]}" if more else None,
                            **({"matched_ids": matched} if key == "needs" else {})})
                ids += [r[0] for r in found]
            unique = list(dict.fromkeys(ids))
            rows = H.tasks(c, ids=unique, limit=max(1, len(unique)), visible=visible) if unique else []
            rows = task_views(rows, c, who, visible) if task_views else rows
            for row in rows:
                for field in BRIEF:
                    row.pop(field, None)
            return {"bot": bot, "tabs": out, "tasks": rows}

    @app.put("/api/v2/bots/{bot}/task-tabs")
    def task_tabs_save(request: Request, bot: str, body: M.BotTaskTabs):
        """Save the bot's sidebar: the whole tab list in order and, when sent, the widgets in order. `tabs: null` goes
        back to the defaults, widgets and all; leaving `widgets` out keeps the saved ones."""
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        def work(c):
            who = request.state.identity
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_see(c, who, bot)
            if not may_edit(c, auth, settings, who, bot):
                raise Problem("forbidden", "Only the bot or a person who manages it changes its sidebar", 403)
            viewer = viewer_key(settings, who, bot)
            ctx = Ctx(c, auth, who, settings)
            if body.tabs is None:
                c.execute("DELETE FROM bot_task_tabs WHERE bot=? AND viewer=?", (bot, viewer))
                if not viewer:
                    from . import cards
                    cards.sync_bot(ctx, bot, DEFAULT_WIDGETS)
            else:
                if not body.tabs:
                    raise bad("Keep at least one tab")
                was = stored(c, settings, who, bot)
                tabs = json.dumps(clean(c, body.tabs))
                c.execute("INSERT INTO bot_task_tabs(bot,viewer,tabs,widgets,updated_at,updated_by) "
                          "VALUES(?,?,?,?,?,?) ON CONFLICT(bot,viewer) DO UPDATE SET tabs=excluded.tabs,"
                          "updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                          (bot, viewer, tabs, was["widgets"] if was else None, H.now(), who.actor))
                if "widgets" in body.model_fields_set:
                    if body.widgets is None:
                        write_widgets(ctx, bot, keep_unread(ctx, saved_widgets(ctx, bot), list(DEFAULT_WIDGETS)))
                        if not any(ITEM.fullmatch(w["id"]) for w in saved_widgets(ctx, bot)):
                            c.execute("UPDATE bot_task_tabs SET widgets=NULL WHERE bot=? AND viewer=?", (bot, viewer))
                    else:
                        write_widgets(ctx, bot, keep_unread(ctx, saved_widgets(ctx, bot), clean_widgets(body.widgets)))
            if not viewer:
                H.event(c, who.actor, "bot.task_tabs_changed", bot,
                        {"tabs": None if body.tabs is None else len(body.tabs)})
            return {**read(c, settings, who, bot, ctx), "can_edit": True}
        return mutate(request, body, work)
