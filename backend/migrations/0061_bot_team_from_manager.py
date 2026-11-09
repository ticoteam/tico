"""A bot that reports to a person in a group takes that group (backend/people.py `manager_team`, settings_admin `_team`).

A bot made before that default has no team of its own: `bot_config.team` is NULL and its config names no `team`. `team_of`
walks a bot's manager up through bots but stops at a person, so such a bot under Arthur, who is in Engineering, sat at the
root of the org chart, while Arthur's older bots, which have a team, sat under him. This gives each of them its manager's
group:

- a bot whose chain reaches a person in a group takes that person's group. Its config gets the `team`, so the chart keeps
  it there (external_sync does the same for the bots it brings in);
- a bot whose chain reaches a team on a bot above it, with no person on the way, takes that team in its column only: it
  follows that chain, as it always did.

Left alone: a bot that names a team, an empty one too (`""` keeps a bot out of groups on purpose, and the groups migration
wrote it for bots it found in none); the company's own bots and helpers (backend/groups.py `SYSTEM_BOTS`, the Assistant of
this install (`TICO_ASSISTANT_BOT`, else `coo`), the helper cards in templates/catalog); the inbox bots; a bot whose manager
is in no group.

The rules are copied here on purpose: a migration records the rules as they were when it ran. Run again it changes
nothing, because every bot it could move has a team now.
"""
import json
import os

# The company's own bots, by slug (backend/groups.py SYSTEM_BOTS), and the templates that sit outside groups: the built-in
# ones and the helpers (templates/catalog/*/card.yaml, kind: helper). The Assistant is not in the list: it is the install's
# own (backend/config.py `assistant_bot`), read in `_assistant`, as backend/groups.py `outside` reads it.
BUILT_IN_SLUGS = frozenset({"assistant", "botops", "librarian", "goal-manager", "health-monitor", "task-manager",
                            "bookkeeper", "tico-counsel"})
OUTSIDE_TEMPLATES = frozenset({"assistant", "botops", "librarian", "goal-manager", "bookkeeper", "health-monitor",
                               "inbox", "task-manager", "tico-counsel"})


def _assistant():
    """The install's Assistant slug: TICO_ASSISTANT_BOT, else `coo`."""
    return os.environ.get("TICO_ASSISTANT_BOT", "").strip() or "coo"


def _text(value):
    return str(value or "").strip()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _roster(conn):
    """(groups, roots, people, inbox): the group ids; {the root bot: its group}; {person id: their group}; the inbox bots.
    A group is an `org_groups` entry, or a `teams` entry with a root (a roster from before groups), as backend/people.py
    `load` reads it."""
    row = conn.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    doc = json.loads(row[0]) if row and row[0] else {}
    doc = doc if isinstance(doc, dict) else {}
    groups, roots, people, inbox = set(), {}, {}, set()
    for gid, body in (doc.get("org_groups") or {}).items():
        gid = _text(gid)
        if gid and isinstance(body, dict):
            groups.add(gid)
            if _text(body.get("root")):
                roots[_text(body.get("root"))] = gid
    for name, body in (doc.get("teams") or {}).items():
        root = _text(body.get("root")) if isinstance(body, dict) else _text(body)
        if _text(name) and root:
            groups.add(_text(name))
            roots.setdefault(root, _text(name))
    for person in doc.get("people") or []:
        if isinstance(person, dict) and _text(person.get("id")):
            people[_text(person["id"])] = _text(person.get("team"))
            if _text(person.get("inbox_bot")):
                inbox.add(_text(person["inbox_bot"]))
    return groups, roots, people, inbox


def _bots(conn):
    """{bot id: what the chain needs and what a move would write} for every bot, read before anything changes."""
    rows = conn.execute("SELECT bc.bot, bc.config, bc.team, bc.reports_to, b.slug FROM bot_config bc "
                        "LEFT JOIN bots b ON b.id = bc.bot").fetchall()
    bots = {}
    for bot, raw, stored, reports_to, slug in rows:
        try:
            config = json.loads(raw or "{}")
        except ValueError:
            config = None
        config = config if isinstance(config, dict) else None
        named = None if config is None or config.get("team") is None else _text(config.get("team"))
        bots[bot] = {"slug": _text(slug) or _text((config or {}).get("name")), "config": config,
                     "stored": _text(stored), "named": named, "reports_to": _text(reports_to),
                     "template": _text((config or {}).get("template"))}
    return bots


def _above(start, bots, groups, roots, people):
    """The group the chain from `start` (a reports_to) reaches, and whether it got there through a person. Walks as
    backend/people.py `team_of` does, except that reaching a person reads that person's group (`manager_team`)."""
    at, seen = start, set()
    while at and at not in seen:
        seen.add(at)
        if at.startswith("human:") or (at not in bots and at in people):
            pid = at[6:] if at.startswith("human:") else at
            team = people.get(_text(pid), "")
            return (team if team in groups else None), True
        entry = bots.get(at)
        if entry is None:
            return None, False
        named = entry["named"]
        if named is not None and (named in groups or not named):
            return named or None, False
        if at in roots:
            return roots[at], False
        at = entry["reports_to"]
    return None, False


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"bot_config", "registry_metadata"} <= tables:
        return                                   # a new file: the module's tables are made after the migrations
    groups, roots, people, inbox = _roster(conn)
    bots = _bots(conn)
    assistant = _assistant()
    moves = []
    for bot, entry in bots.items():
        if entry["config"] is None or entry["stored"] or entry["named"] is not None:
            continue                             # a bot that has a team, or whose config this cannot read
        if (entry["slug"] in BUILT_IN_SLUGS or assistant in (entry["slug"], bot)
                or entry["template"] in OUTSIDE_TEMPLATES or bot in inbox):
            continue
        if bot in roots:
            team, through_person = roots[bot], False
        else:
            team, through_person = _above(entry["reports_to"], bots, groups, roots, people)
        if team:
            moves.append((bot, team, through_person, entry["config"]))
    for bot, team, through_person, config in moves:
        if through_person:
            config["team"] = team
            conn.execute("UPDATE bot_config SET config=?,team=? WHERE bot=?", (_encode(config), team, bot))
        else:
            conn.execute("UPDATE bot_config SET team=? WHERE bot=?", (team, bot))
