"""Who may see, read and write to each bot: three audiences per bot, stored on the bot.

    see    the bot in the org chart and in bot lists: its name, role, who runs it, who it reports to
    read   its activity: tasks, updates, files, status and run log, routines, its shared rooms
    write  send it messages, ask it, give it tasks or notes, comment on its tasks

Each level names an audience: everyone, or lists of people (roster ids), groups (stored under `teams`, the
name they had first) and bots (slugs). `bot_config.access` holds the three; NULL means Open
(everyone for all three), which is what every new bot starts with. Whoever may read or write can
see, whatever the See list says. The rules that decide a caller live in `Auth.bot_access`
(backend/auth.py); this module is the pure part: the stored shape and who an audience names.
"""

import json

from .store import Problem

LEVELS = ("see", "read", "write")
LISTS = ("people", "teams", "bots")
OPEN_LEVEL = {"everyone": True, "people": [], "teams": [], "bots": []}
MAX_ENTRIES = 500


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def qlist(values):
    # SQLite accepts an empty IN list: `x IN ()` is false and `x NOT IN ()` is true.
    return "(" + ",".join(q(v) for v in values) + ")"


def _names(values):
    out = []
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in out:
            out.append(item)
    return out


def audience(value):
    """One level in its stored shape. Anything unreadable is Open, never a locked-out bot."""
    if not isinstance(value, dict):
        return dict(OPEN_LEVEL, people=[], teams=[], bots=[])
    if value.get("everyone"):
        return {"everyone": True, "people": [], "teams": [], "bots": []}
    return {"everyone": False, **{key: _names(value.get(key)) for key in LISTS}}


def document(raw):
    """The three levels from the stored JSON text (or a parsed value); NULL and junk are Open."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        value = None
    value = value if isinstance(value, dict) else {}
    return {level: audience(value.get(level)) for level in LEVELS}


def is_open(doc):
    return all(doc[level]["everyone"] for level in LEVELS)


def stored(doc):
    """What `access` holds: NULL for Open, so a bot nobody restricted has no row to keep."""
    return None if is_open(doc) else json.dumps(doc, sort_keys=True, separators=(",", ":"))


def parse(body, roster_ids, teams, bots):
    """A PUT body checked against who exists. `roster_ids`, `teams` and `bots` are sets."""
    doc = {}
    for level in LEVELS:
        raw = body.get(level) if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            raise Problem("access", f"Say who may {level}: everyone, or people, teams and bots", 422)
        unknown = sorted(set(raw) - {"everyone", *LISTS})
        if unknown:
            raise Problem("access", f"{level}: unknown field {unknown[0]}", 422)
        one = audience(raw)
        for key, known, label in (("people", roster_ids, "person"), ("teams", teams, "group"), ("bots", bots, "bot")):
            if len(one[key]) > MAX_ENTRIES:
                raise Problem("access", f"{level}: at most {MAX_ENTRIES} {key}", 422)
            missing = [item for item in one[key] if item not in known]
            if missing:
                raise Problem("not_found", f"Unknown {label}: " + ", ".join(missing), 404)
        doc[level] = one
    return doc


def names(level, person="", team="", bot=""):
    """Whether one level's audience names this person, group or bot. `team` is the person's group, or every group
    they are in (their own and the ones it is nested in): naming a group names everyone in it and in the groups under it."""
    if level["everyone"]:
        return True
    groups = [team] if isinstance(team, str) else list(team or ())
    return bool((person and person in level["people"]) or any(g and g in level["teams"] for g in groups)
                or (bot and bot in level["bots"]))


def owner_ids(raw):
    """The people a bot's owners list names (`bot_config.bot_owners`); the creator and any co-owners."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return _names(value) if isinstance(value, list) else []


def summary(doc):
    """The three levels as short words, for the audit trail: 'everyone' or a count of entries."""
    def word(level):
        return "everyone" if level["everyone"] else "; ".join(
            f"{len(level[key])} {key}" for key in LISTS if level[key]) or "nobody"
    return {level: word(doc[level]) for level in LEVELS}
