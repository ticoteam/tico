"""The CEO Bot: every install has one, at the top of its org chart (docs/ceo-bot.md; spec: Head of Product, CEO Bot v3).

The CEO Bot is the built-in bot that was the Goal Manager. Its slug stays `goal-manager` (actor `bot:goal-manager`), so
its KPI readings, goal events and old messages stay attached. It is named "CEO Bot" unless the owner renames it
(`name`), and nobody sees "Goal Manager" any more.

- `migrate` (once, when the server starts after the upgrade) names the bot "CEO Bot" where it still had its old name and
  records `on` in this install's `registry_metadata` row. `on` is an internal flag that says the migration ran; no person
  sees it or turns it off.
- The org chart (`chart_entries`) shows it under the owner, and under it the bots that would hang straight under the
  owner and are a Head or have no manager, and (`chart_groups`) the top-level groups those Heads are in. Built-in
  bots, helpers and message bots stay where they are. The chart is the only thing that moves: `bot_config.reports_to`
  is never written, so who manages whom (the access chain in backend/auth.py, goal rights in backend/goals.py,
  `bot_contact`) is exactly what it was.
- "Regular checks" (`checks`, default on) are the KPI pass routine and the automatic goal colours. Off, neither runs
  until the owner turns them back on; the owner's chat still works.
- "How much your CEO Bot does" (`level`): Passive (1, the default), Suggests actions (2), Acts on its own (3). At
  Passive it does nothing on its own beyond the regular checks (`proactive`).

Only the owner changes the level, the regular checks and its name, on their own click (backend/goal_routes.py,
backend/settings_admin.py); a bot never does, the CEO Bot included.
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
OLD_NAME = "Goal Manager"       # its name before; only `migrate` reads it


def _record(c):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def name(c):
    """What a person sees it called: the name the owner gave it, else "CEO Bot"."""
    current = ((H.bot(c, SLUG) or {}).get("display_name") or "").strip()
    return current if current and current != OLD_NAME else NAME


def view(c):
    history = level_history(c)
    return {"name": name(c), "regular_checks": checks(c),
            "level": level(c), "levels": levels(),
            "level_changed_by": history[0]["by"] if history else "",
            "level_changed_at": history[0]["at"] if history else "",
            "level_history": history, "daily_cap": DAILY_CAP, "asked_today": asked_today_all(c),
            "level3_offer": _offer(c)}


def _offer(c):
    from . import ceo_suggest
    return ceo_suggest.offer_view(c)


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


def migrate(c):
    """Once per install, when the server starts (backend/store.py): the bot that was the Goal Manager becomes the CEO
    Bot. Its old name (or none) becomes "CEO Bot"; a name a person gave it stays. Idempotent: `on` in the record says
    it ran. Also run where the bot is built in later (backend/onboarding.py names it with `name`)."""
    record = _record(c)
    bot = H.bot(c, SLUG)
    if bot and (bot.get("display_name") or "").strip() in ("", OLD_NAME):
        _rename(c, H.KEEPER, NAME)
    if record.get("on") is True:
        return False
    record.update(on=True, migrated_at=H.now())
    record.pop("name_before", None)
    _save(c, record)
    H.event(c, H.KEEPER, "ceo_bot.migrated", SLUG, {})
    return True


# ----------------------------------------------------------------------------- regular checks
def checks(c):
    """Whether the regular checks run: the KPI pass routine and the automatic goal colours. On unless the owner
    turned them off."""
    return _record(c).get("checks") is not False


def set_checks(c, actor, value):
    """Turn the regular checks on or off. The caller has checked it is the owner (backend/goal_routes.py). Back on,
    every goal's automatic colour is worked out again at once. The same value again changes nothing."""
    H._writer(c, actor)
    value = bool(value)
    if checks(c) == value:
        return
    record = _record(c)
    record.update(checks=value, checks_by=actor, checks_at=H.now())
    _save(c, record)
    H.event(c, actor, "ceo_bot.checks", SLUG, {"on": value})
    if value:
        from . import goals as G
        G.refresh(c)


PASS = SLUG + ":kpi-pass"


def held_routine(c, schedule):
    """Why a scheduled run of the CEO Bot's KPI pass is skipped (the regular checks are off), or None."""
    if schedule["id"] == PASS and not checks(c):
        return "regular checks are off"
    return None


REVIEW = SLUG + ":goals-review"


def proactive(c):
    """Whether it does anything on its own beyond the regular checks: asking goal owners, the vision ask, Why lines
    in Needs you, the Monday note. Only at a level the owner raised it to; at Passive, never."""
    return level(c) >= 2


def _review(c, actor, record):
    """The Monday note (the bot's `goals-review` routine, declared paused) starts when the owner raises the level
    from Passive. Back at Passive, it is paused again only if this started it and nobody has paused or started it
    since: a person's own choice is never overruled. `review_settled` says it has had its one go at starting it at
    the current raise, so `reconcile` never starts it again."""
    from . import routines
    row = routines.row(c, REVIEW)
    if (record.get("level") if record.get("level") in LEVELS else DEFAULT_LEVEL) < 2:     # the level being saved
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
    """Whether anyone but the server started or paused the Monday note after the server started it."""
    since = since if isinstance(since, str) else ""
    return c.execute("SELECT 1 FROM events WHERE action='routine.updated' AND target=? AND actor!=? AND ts>=? "
                     "AND detail_json LIKE '%\"enabled\"%' LIMIT 1", (REVIEW, H.KEEPER, since)).fetchone() is not None


def reconcile(c):
    """Once an hour (backend/scheduler.py): above Passive, a Monday note whose routine appeared after the level was
    raised is started once, never again after that one go, so a person who pauses it later stays paused. At Passive,
    a Monday note the old level 1 started (before v3) and nobody touched since is paused."""
    record = _record(c)
    if proactive(c) and record.get("review_settled"):
        return
    if not proactive(c) and not record.get("review_armed"):
        return
    before = dict(record)
    _review(c, H.KEEPER, record)
    if record != before:
        _save(c, record)


def _outside(slug, entry, assistant, helpers):
    return slug in ("assistant", "botops", "librarian", SLUG, "health-monitor") or slug == assistant \
        or str((entry or {}).get("template") or "") in helpers


def chart_entries(c, settings, roster, entries, archived=()):
    """`entries` as the org chart draws them. The CEO Bot reports to the owner, and so do on the chart the bots that
    hang straight under the owner and are a Head (a `lead: true` card) or have no manager at all. Built-in bots,
    helpers and message bots stay where they are, and so does anyone under a group or another person. A copy; with no
    CEO Bot (or no owner on the roster) yet, `entries` itself. Only for drawing the chart: permission checks read the
    stored `reports_to`."""
    archived = set(archived or ())
    if SLUG not in (entries or {}) or SLUG in archived:
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
    sits at the top of the chart: a top-level group that holds a bot the CEO Bot heads on the
    chart (`chart`, from `chart_entries`) hangs under the CEO Bot, so the department sits in its subtree. Never the
    group the owner is in, since the CEO Bot hangs under the owner. With nothing moved, `groups` itself."""
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
# "How much your CEO Bot does" (Settings > CEO Bot): three levels, chosen by the owner on their own click, never by
# an admin or a bot, the CEO Bot included (backend/goal_routes.py). Every install starts at Passive (level 1; installs
# on the old level 1, "Goals and tracking", are Passive now). Each change is kept in the record with who and when.
# Level 2 asks goal owners, proposes Why lines, sends the Monday note and suggests recoveries (backend/goal_asks.py,
# backend/ceo_suggest.py, backend/ceo_merge.py). Level 3 can be chosen now but adds nothing to level 2 yet: its
# behaviour arrives in a later release, and its sentence says so until then (`READY`).
LEVELS = {
    1: ("Passive",
        "It keeps the regular checks running, KPI readings and the automatic goal colours, and otherwise acts only when "
        "you talk to it: it suggests KPIs, goals and Why lines when you ask and changes nothing until you accept."),
    2: ("Suggests actions",
        "It also asks a goal's owner what's happening when a KPI goes red or stale, proposes Why lines, sends you a "
        "Monday note, and suggests how your bots could recover a goal that falls behind, as items in Needs you that you "
        "approve one by one; nothing changes without your yes."),
    3: ("Acts on its own",
        "It also makes, reorders and closes bot tasks itself to recover a goal, each logged with an Undo; spending, "
        "publishing, people and changes to goals or the vision stay yours."),
}
READY = {1, 2}
NOT_YET = " Not yet: until this level arrives in a later release, it does what level 2 does."
DEFAULT_LEVEL = 1
HISTORY_KEEP = 50


def level(c):
    """The level this install's CEO Bot works at: 1 (Passive) unless the owner chose another."""
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
    """Choose the level. The caller has checked it is the owner (backend/goal_routes.py). The same level again
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
    if (value >= 2) != (was >= 2):
        _review(c, actor, record)               # the Monday note starts above Passive and stops back at it
    _save(c, record)
    if value < 2:
        from . import ceo_suggest
        ceo_suggest.withdraw(c, actor)          # Passive suggests no task changes: what still waits is withdrawn
    H.event(c, actor, "ceo_bot.level", SLUG, {"level": value, "was": was})


def _save(c, record):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
              (KEY, encode(record)))


# ----------------------------------------------------------------------------- at most 5 a day in Needs you
# At every level the CEO Bot puts at most 5 items a day in a person's Needs you (spec test 14). The server counts the
# tasks it filed for that person since midnight UTC, whoever filed them for it: its own `hub task create` and the asks
# backend/goal_asks.py files in its name, and the items it merged several bots' asks into (backend/ceo_merge.py).
DAILY_CAP = 5


def day_start(at=None):
    """Midnight UTC of `at` (a datetime or a timestamp; now when None), in the format tasks.created sorts by."""
    when = at if isinstance(at, datetime) else (H.parse_ts(at) or datetime.now(timezone.utc))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00.000000Z")


def asked_today(c, person, at=None):
    """How many tasks the CEO Bot filed for this person today (UTC), closed ones included, and merged items."""
    since = day_start(at)
    return c.execute("SELECT COUNT(*) FROM tasks WHERE requester=? AND owner=? AND created>=?",
                     ("bot:" + SLUG, person, since)).fetchone()[0] + c.execute(
        "SELECT COUNT(DISTINCT merged_into) FROM ceo_merges WHERE person=? AND created>=?", (person, since)).fetchone()[0]


def asked_today_all(c, at=None):
    """{person actor: tasks the CEO Bot filed for them today}, for the people it filed any for."""
    since, out = day_start(at), {}
    for who, n in c.execute(
            "SELECT owner, COUNT(*) FROM tasks WHERE requester=? AND owner LIKE 'human:%' AND created>=? GROUP BY owner "
            "UNION ALL SELECT person, COUNT(DISTINCT merged_into) FROM ceo_merges WHERE created>=? GROUP BY person",
            ("bot:" + SLUG, since, since)):
        out[who] = out.get(who, 0) + n
    return out


def room_today(c, person, at=None):
    """How many more items the CEO Bot may put in this person's Needs you today."""
    return max(0, DAILY_CAP - asked_today(c, person, at))


def check_cap(c, actor, owner):
    """Refuse a sixth task today from the CEO Bot to one person. Like any refusal it counts toward the bot's quarantine,
    so its playbooks read `asked_today` in `GET /api/v2/ceo-bot` first and keep the rest for tomorrow; the asks
    backend/goal_asks.py files check `room_today` and never reach this."""
    if actor != "bot:" + SLUG or not H.is_human(owner):
        return
    if room_today(c, owner) == 0:
        raise H.Refused("cap", f"The {name(c)} already put {DAILY_CAP} items in this person's Needs you today; "
                               "keep the rest for tomorrow, most important first")
