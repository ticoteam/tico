"""The CEO Bot rollout switch: one per install, off by default (docs/goals-and-kpis.md; spec: Head of Product, CEO Bot).

The CEO Bot is the Goal Manager under another name and in another place on the org chart. Its slug stays
`goal-manager` (actor `bot:goal-manager`), so its KPI readings, goal events and old messages stay attached.

Off, nothing changes: the bot is the Goal Manager, built in and off the chart. On:
- its display name is "CEO Bot" (`bots.display_name`); the name it had before is kept here and given back when the
  switch goes off, unless someone renamed it since;
- the org chart (`chart_entries`) shows it under the owner, and under it the bots that would hang straight under the
  owner and are a Head or have no manager, and (`chart_groups`) the top-level groups those Heads are in. Built-in
  bots, helpers and message bots stay where they are.

The chart is the only thing that moves: `bot_config.reports_to` is never written, so who manages whom (the access
chain in backend/auth.py, goal rights in backend/goals.py, `bot_contact`) is exactly what it was. Turning the switch off
gives the chart back as it was. The switch is one `registry_metadata` row, flipped at runtime by an owner or admin
(`POST /api/v2/ceo-bot`); a bot never flips it.
"""
import json
from datetime import datetime, timezone

from . import hubdb as H
from . import people as P


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

KEY = "ceo-bot"
SLUG = "goal-manager"
NAME = "CEO Bot"
OLD_NAME = "Goal Manager"


def _record(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def on(c):
    """Whether this install has the CEO Bot switch on."""
    return _record(c).get("on") is True


def name(c):
    """The name the built-in Goal Manager is given when it is (re)created: "CEO Bot" while the switch is on."""
    return NAME if on(c) else OLD_NAME


def view(c):
    record = _record(c)
    history = level_history(c)
    return {"on": record.get("on") is True, "name": NAME if record.get("on") is True else OLD_NAME,
            "changed_by": record.get("by") or "", "changed_at": record.get("at") or "",
            "level": level(c), "levels": levels(),
            "level_changed_by": history[0]["by"] if history else "",
            "level_changed_at": history[0]["at"] if history else "",
            "level_history": history, "daily_cap": DAILY_CAP, "asked_today": asked_today_all(c)}


def _rename(c, actor, display_name):
    """The name in both places `SettingsAdmin.update_bot` keeps it, with a new revision so a form opened before
    cannot save the old name back over it."""
    c.execute("UPDATE bots SET display_name=? WHERE slug=?", (display_name, SLUG))
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (SLUG,)).fetchone()
    if not row:
        return
    try:
        declared = json.loads(row["config_json"] or "{}")
    except ValueError:
        declared = None
    if isinstance(declared, dict):
        declared["display_name"] = display_name
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (encode(declared), SLUG))
    c.execute("UPDATE bot_config SET revision=revision+1,definition_updated=?,definition_updated_by=? WHERE bot=?",
              (H.now(), actor, SLUG))


def set_on(c, actor, value):
    """Turn the switch on or off for this install. Idempotent: the same value again changes nothing."""
    record = _record(c)
    value = bool(value)
    if (record.get("on") is True) == value:
        return view(c)
    bot = H.bot(c, SLUG)
    current = (bot or {}).get("display_name") or ""
    if value:
        record["name_before"] = current if current and current != NAME else OLD_NAME
        if bot:
            _rename(c, actor, NAME)
    else:
        # A name a person gave it while the switch was on is theirs: only "CEO Bot" goes back.
        if bot and current == NAME:
            _rename(c, actor, record.get("name_before") or OLD_NAME)
    record.update(on=value, by=actor, at=H.now())
    _review(c, actor, record, value)
    _save(c, record)
    H.event(c, actor, "ceo_bot.switched", SLUG, {"on": value})
    return view(c)


REVIEW = SLUG + ":goals-review"


def _review(c, actor, record, value):
    """The Monday note (the Goal Manager's `goals-review` routine, declared paused) starts when the switch goes on.
    Off, it is paused again only if the switch started it and nobody has paused or started it since: a person's own
    choice is never overruled. `review_settled` says the switch has had its one go at starting it while on, so
    `reconcile` never starts it again."""
    from . import routines
    row = routines.row(c, REVIEW)
    if not value:
        armed = record.pop("review_armed", None)
        record.pop("review_settled", None)
        if row and not row["deleted_at"] and armed and row["enabled"] and not _review_touched(c, armed):
            routines.update(c, H.KEEPER, REVIEW, {"enabled": False})
            H.event(c, actor, "ceo_bot.review_paused", REVIEW, {})
        return
    if not row or row["deleted_at"]:
        return              # not there yet: `reconcile` starts it once it is
    record["review_settled"] = True
    if not row["enabled"]:
        routines.update(c, H.KEEPER, REVIEW, {"enabled": True})
        record["review_armed"] = H.now()
        H.event(c, actor, "ceo_bot.review_armed", REVIEW, {})


def _review_touched(c, since):
    """Whether anyone but the switch started or paused the Monday note after the switch started it."""
    since = since if isinstance(since, str) else ""
    return c.execute("SELECT 1 FROM events WHERE action='routine.updated' AND target=? AND actor!=? AND ts>=? "
                     "AND detail_json LIKE '%\"enabled\"%' LIMIT 1", (REVIEW, H.KEEPER, since)).fetchone() is not None


def reconcile(c):
    """Once an hour (backend/scheduler.py): an install whose switch was on before the Monday note was started on
    switch-on, or whose routine appeared after it, gets it started once. Never again after that one go, so a person
    who pauses it later stays paused."""
    record = _record(c)
    if record.get("on") is not True or record.get("review_settled"):
        return
    before = dict(record)
    _review(c, H.KEEPER, record, True)
    if record != before:
        _save(c, record)


def _outside(slug, entry, assistant, helpers):
    return slug in ("assistant", "botops", "librarian", SLUG, "health-monitor") or slug == assistant \
        or str((entry or {}).get("template") or "") in helpers


def chart_entries(c, settings, roster, entries, archived=()):
    """`entries` as the org chart draws them. With the switch on, the CEO Bot reports to the owner, and so do on the
    chart the bots that hang straight under the owner and are a Head (a `lead: true` card) or have no manager at all.
    Built-in bots, helpers and message bots stay where they are, and so does anyone under a group or another person.
    A copy; with the switch off, `entries` itself. Only for drawing the chart: permission checks read the stored
    `reports_to`."""
    archived = set(archived or ())
    if not on(c) or SLUG not in (entries or {}) or SLUG in archived:
        return entries
    owner = P.person((roster or {}).get("default_user"), roster)
    if not owner or owner.get("hidden"):
        return entries
    from . import onboarding as O
    helpers, leads = O.helper_templates(settings), O.lead_templates(settings)
    inboxes = {p.get("inbox_bot") for p in (roster or {}).get("people") or [] if p.get("inbox_bot")}
    top = "p:" + owner["id"]
    out = dict(entries)
    for slug, entry in entries.items():
        if slug in archived or slug in inboxes or _outside(slug, entry, settings.assistant_bot, helpers):
            continue
        entry = entry or {}
        if (P._clean(entry.get("reports_to")) and str(entry.get("template") or "") not in leads) \
                or P.org_parent("bot", slug, roster, entries, archived) != top:
            continue
        out[slug] = {**entry, "reports_to": SLUG}
    out[SLUG] = {**(entries[SLUG] or {}), "reports_to": "human:" + owner["id"]}
    return out


def chart_groups(roster, entries, chart, groups, archived=()):
    """`groups` (`org_view` rows) as the chart draws them. A Head is usually in a group of its own department, which
    sits at the top of the chart: with the switch on, a top-level group that holds a bot the CEO Bot heads on the
    chart (`chart`, from `chart_entries`) hangs under the CEO Bot, so the department sits in its subtree. Never the
    group the owner is in, since the CEO Bot hangs under the owner. With the switch off, `groups` itself."""
    if chart is entries:
        return groups
    archived = set(archived or ())
    owner = P.person((roster or {}).get("default_user"), roster) or {}
    mine = set(P.group_chain(owner.get("team"), roster))
    led = set()
    for slug, entry in chart.items():
        if slug != SLUG and slug not in archived and P._clean((entry or {}).get("reports_to")) == SLUG:
            chain = P.group_chain(P.team_of(slug, entries, roster), roster)
            if chain:
                led.add(chain[-1])
    return [{**g, "org_parent": "b:" + SLUG} if g["id"] in led and not g.get("parent") and g["id"] not in mine else g
            for g in groups]


# ----------------------------------------------------------------------------- how much it does
# "How much your CEO Bot does" (Settings > CEO Bot): three levels, chosen by the owner or an admin on their own click,
# never by a bot, the CEO Bot included (backend/goal_routes.py). Every install starts at level 1. Each change is kept in
# the switch's record with who and when. Levels 2 and 3 can be chosen now but add nothing to level 1 yet: their
# behaviour arrives in later releases, and each sentence says so until then (`READY`).
LEVELS = {
    1: ("Goals and tracking",
        "It keeps your goals clear and tracked: KPI readings, red and stale flags, Why lines and target changes to "
        "propose, a Monday note, and asking a goal's owner what's happening. It never changes anyone's tasks."),
    2: ("Suggests actions",
        "It also suggests how your bots could recover a goal that falls behind, as items in Needs you that you approve "
        "one by one; nothing changes without your yes."),
    3: ("Acts on its own",
        "It also makes, reorders and closes bot tasks itself to recover a goal, each logged with an Undo; spending, "
        "publishing, people and changes to goals or the vision stay yours."),
}
READY = {1}
NOT_YET = " Not yet: until this level arrives in a later release, it does what level 1 does."
DEFAULT_LEVEL = 1
HISTORY_KEEP = 50


def level(c):
    """The level this install's CEO Bot works at: 1 unless an owner or admin chose another."""
    value = _record(c).get("level")
    return value if value in LEVELS else DEFAULT_LEVEL


def levels():
    return [{"level": n, "name": title, "sentence": text + ("" if n in READY else NOT_YET), "ready": n in READY}
            for n, (title, text) in LEVELS.items()]


def level_history(c):
    """Every level change kept, newest first: [{level, was, by, at}]."""
    rows = _record(c).get("level_history")
    return [r for r in rows if isinstance(r, dict)][::-1] if isinstance(rows, list) else []


def named(c, value):
    """`view` with who changed the level named as the page shows them (`by_name`, `level_changed_by_name`)."""
    from . import names as N
    actors = {r["by"] for r in value["level_history"] if r.get("by")}
    known = N.lookup(c, actors) if actors else {}
    name_of = lambda actor: known.get(actor) or H.actor_id(actor or "")
    return {**value, "level_changed_by_name": name_of(value["level_changed_by"]) if value["level_changed_by"] else "",
            "level_history": [{**r, "by_name": name_of(r.get("by"))} for r in value["level_history"]]}


def set_level(c, actor, value):
    """Choose the level. The caller has checked it is a person who may (backend/goal_routes.py). The same level again
    changes nothing and logs nothing."""
    H._writer(c, actor)
    if value not in LEVELS:
        raise H.Refused("lint", "choose level 1, 2 or 3")
    record = _record(c)
    was = level(c)
    if value == was:
        return
    at = H.now()
    history = [r for r in record.get("level_history") or [] if isinstance(r, dict)]
    history.append({"level": value, "was": was, "by": actor, "at": at})
    record.update(level=value, level_history=history[-HISTORY_KEEP:])
    _save(c, record)
    H.event(c, actor, "ceo_bot.level", SLUG, {"level": value, "was": was})


def _save(c, record):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, encode(record)))


# ----------------------------------------------------------------------------- at most 5 a day in Needs you
# At every level the CEO Bot puts at most 5 items a day in a person's Needs you (spec test 14). The server counts the
# tasks it filed for that person since midnight UTC, whoever filed them for it: its own `hub task create` and the asks
# backend/goal_asks.py files in its name. Only while the switch is on: off, the Goal Manager works as before.
DAILY_CAP = 5


def day_start(at=None):
    """Midnight UTC of `at` (a datetime or a timestamp; now when None), in the format tasks.created sorts by."""
    when = at if isinstance(at, datetime) else (H.parse_ts(at) or datetime.now(timezone.utc))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00.000000Z")


def asked_today(c, person, at=None):
    """How many tasks the CEO Bot filed for this person today (UTC), closed ones included."""
    return c.execute("SELECT COUNT(*) FROM tasks WHERE requester=? AND owner=? AND created>=?",
                     ("bot:" + SLUG, person, day_start(at))).fetchone()[0]


def asked_today_all(c, at=None):
    """{person actor: tasks the CEO Bot filed for them today}, for the people it filed any for."""
    return {r[0]: r[1] for r in c.execute(
        "SELECT owner, COUNT(*) FROM tasks WHERE requester=? AND owner LIKE 'human:%' AND created>=? GROUP BY owner",
        ("bot:" + SLUG, day_start(at)))}


def room_today(c, person, at=None):
    """How many more items the CEO Bot may put in this person's Needs you today; None while the switch is off."""
    if not on(c):
        return None
    return max(0, DAILY_CAP - asked_today(c, person, at))


def check_cap(c, actor, owner):
    """Refuse a sixth task today from the CEO Bot to one person. Like any refusal it counts toward the bot's quarantine,
    so its playbooks read `asked_today` in `GET /api/v2/ceo-bot` first and keep the rest for tomorrow; the asks
    backend/goal_asks.py files check `room_today` and never reach this."""
    if actor != "bot:" + SLUG or not H.is_human(owner):
        return
    if room_today(c, owner) == 0:
        raise H.Refused("cap", f"The {NAME} already put {DAILY_CAP} items in this person's Needs you today; "
                               "keep the rest for tomorrow, most important first")
