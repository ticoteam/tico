"""The Strategy Bot: every install has one, at the top of its org chart (docs/strategy-bot.md; spec: Head of Product,
Strategy Bot v3).

Its slug stays `goal-manager` (actor `bot:goal-manager`), so its KPI readings, goal events and old messages stay
attached. It is named "Strategy Bot" unless the owner renames it (`name`); the names it had before are read only by
`migrate`.

- `migrate` (every start, backend/store.py) moves what an earlier install kept under its old names, and names the bot
  "Strategy Bot" where it still had an old name. It records `on` in this install's `registry_metadata` row, which says
  the one-time part ran; no person sees it or turns it off.
- The org chart (`chart_entries`) shows it under the owner, and under it the bots that would hang straight under the
  owner and are a Head or have no manager, and (`chart_groups`) the top-level groups those Heads are in. Built-in bots,
  helpers and message bots stay where they are. The chart is the only thing that moves: `bot_config.reports_to` is never
  written, so who manages whom (the access chain in backend/auth.py, goal rights in backend/goals.py, `bot_contact`) is
  exactly what it was.
- "Hide from my chart" (`hidden_for`, preference `chart.hide_strategy_bot` in the caller's own `preferences` row) is one
  person's view: on their chart the Strategy Bot is not drawn and nothing moves under it. Everyone else's chart, and the
  bot itself (its config, `reports_to`, access, level, Goals, chat), are as they were.
- "Regular checks" (`checks`, default on) are the KPI pass routine and the automatic goal colours. Off, neither runs
  until the owner turns them back on; the owner's chat still works.
- "How much your Strategy Bot does" (`level`): Passive (1), Suggests actions (2, where every install starts), Acts on
  its own (3). At Passive it does nothing on its own beyond the regular checks (`proactive`).

Only the owner changes the level, the regular checks and its name, on their own click (backend/goal_routes.py,
backend/settings_admin.py); a bot never does, the Strategy Bot included."""
import json
from datetime import datetime, timezone

from . import hubdb as H
from . import people as P


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

KEY = "strategy-bot"
# The registry row and "Hide from my chart" under their old keys, which an older release reopened on the file after a
# rollback reads and writes. For one release this one writes both keys alike (`_save`, `mirror_hide`), and `migrate`
# takes what that release wrote there since.
OLD_KEY = "ceo-bot"
SLUG = "goal-manager"
NAME = "Strategy Bot"
# The names the bot had before, and "" when it had none: `migrate` renames these, and `name` treats them as no name.
OLD_NAMES = ("Goal Manager", "CEO Bot")
# The notes a withdrawn recovery carried before the rename: `migrate` rewrites them to the current note.
OLD_WITHDRAWN = ("Withdrawn: the CEO Bot was set to Passive, where it suggests no task changes",
                 "Withdrawn: the CEO Bot was set to level 1, where it suggests no task changes")
OLD_HIDE_PREF = "chart.hide_ceo_bot"


def _record(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def name(c):
    """What a person sees it called: the name the owner gave it, else "Strategy Bot"."""
    current = ((H.bot(c, SLUG) or {}).get("display_name") or "").strip()
    return current if current and current not in OLD_NAMES else NAME


def view(c):
    history = level_history(c)
    return {"name": name(c), "regular_checks": checks(c),
            "level": level(c), "level_chosen": _record(c).get("level") in LEVELS, "levels": levels(),
            "level_changed_by": history[0]["by"] if history else "",
            "level_changed_at": history[0]["at"] if history else "",
            "level_history": history, "asked_today": asked_today_all(c),
            "level3_offer": _offer(c)}


def _offer(c):
    from . import strategy_suggest
    return strategy_suggest.offer_view(c)


def _rename(c, actor, display_name):
    """The name in both places `SettingsAdmin.update_bot` keeps it, with a new revision so a form opened before
    cannot save the old name back over it."""
    c.execute("UPDATE bots SET display_name=? WHERE slug=?", (display_name, SLUG))
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (SLUG,)).fetchone()
    if not row:
        return
    try:
        declared = json.loads(row["config"] or "{}")
    except ValueError:
        declared = None
    if isinstance(declared, dict):
        declared["display_name"] = display_name
        c.execute("UPDATE bot_config SET config=? WHERE bot=?", (encode(declared), SLUG))
    c.execute("UPDATE bot_config SET revision=revision+1,definition_updated_at=?,definition_updated_by=? WHERE bot=?",
              (H.now(), actor, SLUG))


def migrate(c):
    """Every start, once the tables are renamed (backend/hubdb.py `migrate`). Each step does nothing once it is done:
    the registry row and "Hide from my chart" take what their old keys hold; a withdrawn recovery with an old note
    reads the current note; and the bot's name becomes "Strategy Bot" where it had none or an old one, a name the
    owner chose stays. The old keys stay, kept equal to the new ones (OLD_KEY): where one differs, an older release
    reopened the file and wrote it since, so what it holds wins (each of its fields over the record; the later "Hide
    from my chart"), and a choice the owner made then is kept. Also run where the bot is built in later
    (backend/onboarding.py names it with `name`)."""
    old, new = (c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
                for key in (OLD_KEY, KEY))
    if old and (not new or old[0] != new[0]):
        try:
            since = json.loads(old[0])
        except ValueError:
            since = None
        if isinstance(since, dict):
            _save(c, {**_record(c), **since})
        else:
            c.execute("INSERT OR IGNORE INTO registry_metadata(key,value) SELECT ?, value "
                      "FROM registry_metadata WHERE key=?", (KEY, OLD_KEY))
    elif new and not old:
        _save(c, _record(c))
    c.execute("INSERT INTO preferences(actor,key,value,updated_at) SELECT actor, ?, value, updated_at "
              "FROM preferences AS o WHERE key=? AND NOT EXISTS (SELECT 1 FROM preferences AS p "
              "WHERE p.actor=o.actor AND p.key=? AND p.updated_at>=o.updated_at) "
              "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
              (HIDE_PREF, OLD_HIDE_PREF, HIDE_PREF))
    # Then each old key reads as its new one, for an older release to read after a rollback.
    c.execute("INSERT INTO preferences(actor,key,value,updated_at) SELECT actor, ?, value, updated_at "
              "FROM preferences AS n WHERE key=? AND NOT EXISTS (SELECT 1 FROM preferences AS p "
              "WHERE p.actor=n.actor AND p.key=? AND p.updated_at>=n.updated_at) "
              "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
              (OLD_HIDE_PREF, HIDE_PREF, OLD_HIDE_PREF))
    from . import strategy_suggest
    # Decided by Tico, as `strategy_suggest.withdraw` records one now, so an older release after a rollback, which
    # knows only its own note, still passes it over in its streak.
    c.execute("UPDATE goal_proposals SET decision_note=?, decided_by=? WHERE decision_note IN (?, ?)",
              (strategy_suggest.WITHDRAWN, H.KEEPER, *OLD_WITHDRAWN))
    bot = H.bot(c, SLUG)
    if bot and (bot.get("display_name") or "").strip() in ("", *OLD_NAMES):
        _rename(c, H.KEEPER, NAME)
    # Before the scheduler's first pass: a Monday note the old level 1 started must not fire at Passive.
    reconcile(c)
    record = _record(c)
    if record.get("on") is True:
        return False
    record.update(on=True, migrated_at=H.now())
    record.pop("name_before", None)
    _save(c, record)
    H.event(c, H.KEEPER, "strategy_bot.migrated", SLUG, {})
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
    H.event(c, actor, "strategy_bot.checks", SLUG, {"on": value})
    if value:
        from . import goals as G
        G.refresh(c)


PASS = SLUG + ":kpi-pass"


def held_routine(c, schedule):
    """Why a scheduled run of the Strategy Bot's KPI pass is skipped (the regular checks are off), or None."""
    if schedule["id"] == PASS and not checks(c):
        return "regular checks are off"
    return None


REVIEW = SLUG + ":goals-review"


def proactive(c):
    """Whether it does anything on its own beyond the regular checks: asking goal owners, the vision ask, Why lines
    in Needs you, the Monday note. Above Passive, which every install is at until its owner chooses otherwise; at
    Passive, never."""
    return level(c) >= 2


def _review(c, actor, record, raised=False):
    """The Monday note (the bot's `goals-review` routine, declared paused) starts when the level is above Passive: when
    the owner raises it from Passive (`raised`), or later, once `reconcile` finds the routine, if no person has
    started or paused it since that raise (`review_raised`, kept while the routine is missing) or, with no raise, ever.
    Back at Passive, it is paused again only if this started it and nobody has paused or started it since: a person's
    own choice is never overruled. `review_settled` says it has had its one go at starting it at the current raise, so
    `reconcile` never starts it again."""
    from . import routines
    row = routines.row(c, REVIEW)
    if (record.get("level") if record.get("level") in LEVELS else DEFAULT_LEVEL) < 2:     # the level being saved
        armed = record.pop("review_armed", None)
        record.pop("review_settled", None)
        record.pop("review_raised", None)
        if row and not row["deleted_at"] and armed and row["enabled"] and not _review_touched(c, armed):
            routines.update(c, H.KEEPER, REVIEW, {"enabled": False})
            H.event(c, actor, "strategy_bot.review_paused", REVIEW, {})
        return
    if not row or row["deleted_at"]:
        if raised:
            record["review_raised"] = H.now()
        return              # not there yet: `reconcile` starts it once it is
    record["review_settled"] = True
    since = record.pop("review_raised", None)
    # Only the owner's raise from Passive overrides a person's earlier start or pause; the default never does, and a
    # person's choice after the raise still wins.
    if not row["enabled"] and (raised or not _review_touched(c, since or "")):
        routines.update(c, H.KEEPER, REVIEW, {"enabled": True})
        record["review_armed"] = H.now()
        H.event(c, actor, "strategy_bot.review_armed", REVIEW, {})


def _review_touched(c, since):
    """Whether anyone but the server started or paused the Monday note after the server started it."""
    since = since if isinstance(since, str) else ""
    return c.execute("SELECT 1 FROM events WHERE action='routine.updated' AND target=? AND actor!=? AND ts>=? "
                     "AND detail LIKE '%\"enabled\"%' LIMIT 1", (REVIEW, H.KEEPER, since)).fetchone() is not None


def reconcile(c):
    """At startup (`migrate`) and once an hour (backend/scheduler.py): above Passive, a Monday note whose routine
    appeared after the level was raised, or was never chosen, is started once, never again after that one go, so a
    person who pauses it later stays paused. At Passive, a Monday note the old level 1 started (before v3) and nobody
    touched since is paused."""
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
    return slug in ("assistant", "botops", "librarian", H.COUNSEL, SLUG, "health-monitor", "task-manager", "bookkeeper") \
        or slug == assistant \
        or str((entry or {}).get("template") or "") in helpers


# "Hide from my chart" on the Strategy Bot (its menu on the Team sidebar, Settings > Bots > Strategy Bot): one person's
# own preference, kept like their other view settings in `preferences` (actor, key). True hides it; anything else shows
# it.
HIDE_PREF = "chart.hide_strategy_bot"


def hidden_for(c, who):
    """Whether the caller, a person, chose Hide from my chart on the Strategy Bot. A bot's or a token's view is never
    hidden. Only the chart reads it (`chart_entries`, `chart_groups`, `chart_hidden_row`)."""
    if getattr(who, "role", "") not in ("owner", "human") or getattr(who, "via_token", False):
        return False
    row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (who.actor, HIDE_PREF)).fetchone()
    return bool(row) and json.loads(row[0]) is True


def chart_hidden_row(slug, hidden):
    """What a bot's row says on a chart the caller hid the Strategy Bot from: the Strategy Bot's row carries
    `chart_hidden`, so the Team sidebar leaves it off while Goals, its chat and Settings > Bots still list it."""
    return {"chart_hidden": True} if hidden and slug == SLUG else {}


def chart_entries(c, settings, roster, entries, archived=(), hidden=False):
    """`entries` as the org chart draws them. The Strategy Bot reports to the owner, and so do on the chart the bots
    that hang straight under the owner and are a Head (a `lead: true` card) or have no manager at all. Built-in bots,
    helpers and message bots stay where they are, and so does anyone under a group or another person. A copy; with no
    Strategy Bot (or no owner on the roster) yet, or on the chart of a person who hid it (`hidden`, from `hidden_for`),
    `entries` itself. Only for drawing the chart: permission checks read the stored `reports_to`."""
    archived = set(archived or ())
    if hidden or SLUG not in (entries or {}) or SLUG in archived:
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
                or P.org_parent("bot", slug, roster, entries, archived, True) != top:
            continue
        out[slug] = {**entry, "reports_to": SLUG}
    out[SLUG] = {**(entries[SLUG] or {}), "reports_to": "human:" + owner["id"]}
    return out


def chart_groups(roster, entries, chart, groups, archived=(), hidden=False):
    """`groups` (`org_view` rows) as the chart draws them. A Head is usually in a group of its own department, which
    sits at the top of the chart: a top-level group that holds a bot the Strategy Bot heads on the chart (`chart`, from
    `chart_entries`) hangs under the Strategy Bot, so the department sits in its subtree. Never the group the owner is
    in, since the Strategy Bot hangs under the owner. With nothing moved, or on the chart of a person who hid the
    Strategy Bot (`hidden`), `groups` itself."""
    if hidden or chart is entries:
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
# "How much your Strategy Bot does" (Settings > Strategy Bot): three levels, chosen by the owner on their own click,
# never by an admin or a bot, the Strategy Bot included (backend/goal_routes.py). Every install starts at Suggests
# actions (level 2) until its owner chooses another; a level the owner stored, Passive included, is kept. Each change is
# kept in the record with who and when. Level 2 asks goal owners, proposes Why lines, sends the Monday note and suggests
# recoveries (backend/goal_asks.py, backend/strategy_suggest.py, backend/strategy_merge.py). Level 3 also decides alone
# (backend/strategy_acts.py). A level whose behaviour has not arrived yet says so in its sentence (`READY`, `NOT_YET`);
# every level has arrived.
LEVELS = {
    1: ("Passive",
        "It keeps the regular checks running, KPI readings and the automatic goal colours, and otherwise acts only when "
        "asked through your Assistant: it suggests KPIs, goals and Why lines and changes nothing until you accept."),
    2: ("Suggests actions",
        "It also asks a goal's owner what's happening when a KPI goes red or stale, proposes Why lines, sends you a "
        "Monday note, and suggests how your bots could recover a goal that falls behind, as items in Needs you that you "
        "approve one by one; nothing changes without your yes."),
    3: ("Acts on its own",
        "It also decides on its own: it makes bot tasks, moves them up a bot's queue, gives them to another bot and "
        "closes them to recover a goal, each decision logged with an Undo. It never acts alone on a private task; "
        "what it can't do alone becomes an item in Needs you, and spending, publishing, people and changes to goals "
        "or the vision stay yours."),
}
READY = {1, 2, 3}
NOT_YET = " Not yet: until this level arrives in a later release, it does what level 2 does."
DEFAULT_LEVEL = 2
HISTORY_KEEP = 50


def level(c):
    """The level this install's Strategy Bot works at: 2 (Suggests actions) unless the owner chose another."""
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
    changes nothing and logs nothing, except that picking the default is kept as the owner's choice, so a later
    default never moves it."""
    H._writer(c, actor)
    if value not in LEVELS:
        raise H.Refused("lint", "choose level 1, 2 or 3")
    record = _record(c)
    was = level(c)
    if value == was:
        if record.get("level") not in LEVELS:
            record["level"] = value
            _save(c, record)
        return
    at = H.now()
    history = [r for r in record.get("level_history") or [] if isinstance(r, dict)]
    history.append({"level": value, "was": was, "by": actor, "at": at})
    record.update(level=value, level_history=history[-HISTORY_KEEP:])
    if (value >= 2) != (was >= 2):
        _review(c, actor, record, raised=True)  # the Monday note starts above Passive and stops back at it
    _save(c, record)
    if value < 2:
        from . import strategy_suggest
        strategy_suggest.withdraw(c)                 # Passive suggests no task changes: what still waits is withdrawn
    H.event(c, actor, "strategy_bot.level", SLUG, {"level": value, "was": was})


def _save(c, record):
    for key in (KEY, OLD_KEY):
        c.execute("INSERT INTO registry_metadata VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(record)))


def mirror_hide(c, actor, value_json, updated):
    """Write a person's "Hide from my chart" under its old key too (OLD_KEY)."""
    c.execute("INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) ON CONFLICT(actor,key) "
              "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
              (actor, OLD_HIDE_PREF, value_json, updated))


# ----------------------------------------------------------------------------- what it asked today
# The Strategy Bot has no daily limit on a person's Needs you. The server counts the tasks it filed for that person
# since midnight UTC, whoever filed them for it: its own `hub task create` and the asks backend/goal_asks.py files in
# its name, and the items it merged several bots' asks into (backend/strategy_merge.py).
NO_LIMIT = 10 ** 6          # no daily cap: the room never runs out


def day_start(at=None):
    """Midnight UTC of `at` (a datetime or a timestamp; now when None), in the format tasks.created sorts by."""
    when = at if isinstance(at, datetime) else (H.parse_ts(at) or datetime.now(timezone.utc))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00.000000Z")


def asked_today(c, person, at=None):
    """How many tasks the Strategy Bot filed for this person today (UTC), closed ones included, and merged items."""
    since = day_start(at)
    return c.execute("SELECT COUNT(*) FROM tasks WHERE requester=? AND owner=? AND created_at>=?",
                     ("bot:" + SLUG, person, since)).fetchone()[0] + c.execute(
        "SELECT COUNT(DISTINCT merged_into) FROM "
        "strategy_merges WHERE person=? AND created_at>=?", (person, since)).fetchone()[0]


def asked_today_all(c, at=None):
    """{person actor: tasks the Strategy Bot filed for them today}, for the people it filed any for."""
    since, out = day_start(at), {}
    for who, n in c.execute(
            "SELECT owner, COUNT(*) FROM tasks WHERE requester=? AND owner LIKE 'human:%' AND created_at>=? GROUP BY owner "
            "UNION ALL SELECT person, COUNT(DISTINCT merged_into) FROM strategy_merges "
            "WHERE created_at>=? GROUP BY person",
            ("bot:" + SLUG, since, since)):
        out[who] = out.get(who, 0) + n
    return out


def room_today(c, person, at=None):
    """How many more items the Strategy Bot may put in this person's Needs you today: no limit."""
    return NO_LIMIT
