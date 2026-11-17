"""Bots made with a repository someone named get it recorded as chosen, as a Settings > Bots save would have.

Bot creation stored the named repository but never recorded the choice (`bot-repositories-chosen`), so a repository
GitHub had before the bot read as another company's (backend/repositories.py Homes): its computer was sent no
repository to clone, and a bot on a new computer failed every turn with "Missing bot repository or AGENTS.md".
Creation records it now (repositories.note_named); this records it for the bots made before.

A bot's repository is recorded when it is none of the names Tico gives a bot unasked: `bot-<slug>` or `emp-<slug>`
under its current slug, its id or a slug it was renamed from, or its checkout's folder. Those may be another company's
(GitHub had the name before the bot), so they keep the rules Homes applies. A branch (`shared_from`) is skipped: its
row repeats its original's repository, which is not its name. A bare name is under the App's organization, else
TICO_GITHUB_OWNER, as Tico reads it; with neither there is nothing to record. The App's chosen-repositories rule is
not touched: Homes asks it first. Like repositories.note_repository, recording one clears its `taken` and `asked` marks.

Running it again changes nothing.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone

CHOSEN = "bot-repositories-chosen"
TAKEN = "bot-repositories-taken"
ASKED = "bot-repositories-asked"
PART = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BOTS = (
    "SELECT b.id, b.slug, bc.repo, s.folder, s.shared_from FROM bot_config bc JOIN bots b ON b.id=bc.bot "
    "LEFT JOIN bot_settings s ON s.bot_id=bc.bot WHERE trim(coalesce(bc.repo,''))<>''"
)


def _metadata(conn, key):
    row = conn.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    try:
        value = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _save(conn, key, value):
    conn.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def _full(repo, owner):
    """owner/name, lowercased, from a stored bot repo (bare name, owner/name or github.com URL), or ''."""
    repo = str(repo or "").strip()
    if repo.startswith(("http://", "https://")):
        rest = repo.split("://", 1)[1]
        host, _, path = rest.partition("/")
        if host.lower() != "github.com":
            return ""
        repo = path.strip("/")
    repo = repo.removesuffix(".git")
    if "/" not in repo:
        repo = f"{owner}/{repo}" if owner and repo else ""
    parts = repo.split("/")
    if len(parts) != 2 or not all(PART.match(p) for p in parts):
        return ""
    return repo.lower()


def _owner(conn):
    try:
        row = conn.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    except sqlite3.OperationalError:
        row = None
    return (
        (row[0] if row else "")
        or os.environ.get("TICO_GITHUB_OWNER", "").strip()
        or os.environ.get("TICO_ROUTINES_GITHUB_OWNER", "").strip()
    )


def up(conn):
    owner = _owner(conn)
    renamed = {}
    for target, was in conn.execute(
        "SELECT target, json_extract(detail,'$.from') FROM events WHERE action='bot.renamed' AND json_valid(detail)"
    ):
        if was:
            renamed.setdefault(target, set()).add(str(was))
    chosen, taken, asked = _metadata(conn, CHOSEN), _metadata(conn, TAKEN), _metadata(conn, ASKED)
    ours = set(chosen) | set(_metadata(conn, "bot-repositories-made"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    changed = set()
    for bot, slug, repo, folder, shared_from in conn.execute(BOTS).fetchall():
        if shared_from:
            continue
        name = _full(repo, owner)
        if not name or name in ours:
            continue
        names = {str(slug or ""), str(bot)} | renamed.get(bot, set())
        defaults = {_full(prefix + n, owner) for n in names if n for prefix in ("bot-", "emp-")}
        if folder:
            defaults.add(_full(folder, owner))
        if name in defaults:
            continue
        chosen[name] = now
        ours.add(name)
        changed.add(CHOSEN)
        for key, marks in ((TAKEN, taken), (ASKED, asked)):
            if marks.pop(name, None) is not None:
                changed.add(key)
    for key, marks in ((CHOSEN, chosen), (TAKEN, taken), (ASKED, asked)):
        if key in changed:
            _save(conn, key, marks)
