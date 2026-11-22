# pyright: strict
"""Sources: places outside Tico a task can wait on or link to (docs/dev/pipelines-v3.md "Sources and items"). One module
each, named in SOURCES: GitHub (`github.py`) and Jira (`jira.py`: links and their state).

A source module has `SOURCE` (its id), `NAME`, `ITEMS` (its item types) and `CONDITIONS` (what a requirement of kind
`item` can ask of one of its items), `match_link(url)` (the item a link is), optionally `match_key(item_type, text)` (an
item named by its key, in the source's spelling) and `site(url)` (the host a link's item lives on, for a source whose
keys are unique only within one site, like Jira's), and the functions that read, save and act on its items.

An item is (connection, source, item type, key): the connection is the `tools` row it came through, '' for none (every
GitHub item: the company's one GitHub App), so two connections holding the same key are two items. A site source's item
always has the connection that serves its site (`outside_item`): one whose base URL and sites name that one host and no
other, so one connection never stands for two sites' keys.
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any, cast
from urllib.parse import urlsplit

SOURCES = ("github", "jira")


@dataclass(frozen=True)
class Item:
    """A type of item a source has: `type` (its id, e.g. `pr`) and `name` (what people call it, e.g. "PR")."""

    type: str
    name: str


@dataclass(frozen=True)
class Condition:
    """What a requirement of kind `item` asks of a source's items (e.g. GitHub's `pr_merged`). `name` is the
    requirement's name ("PR merged"); `settings` the keys its `settings` may hold; `can_push_past` "never" when no one
    may move a task on without it, else "yes". `standing(conn, task, requirement, answer)` reads saved metadata only and
    says where the requirement stands (a task_requirements.Standing)."""

    id: str
    name: str
    item_type: str
    standing: Callable[..., Any]
    settings: tuple[str, ...] = ()
    can_push_past: str = "yes"


def source(source_id: object) -> ModuleType | None:
    """A source's module, or None for an unknown source. Each source is imported by name, not computed from its id, so
    the test selection's import map (scripts/pr_checks.py) sees which modules a source reaches."""
    if source_id == "github":
        from . import github

        return github
    if source_id == "jira":
        from . import jira

        return jira
    return None


def condition(source_id: object, condition_id: object) -> Condition | None:
    """A source's condition, or None for an unknown source or condition."""
    module = source(source_id) if isinstance(source_id, str) else None
    conditions: dict[str, Condition] = getattr(module, "CONDITIONS", {}) if module else {}
    return conditions.get(condition_id) if isinstance(condition_id, str) else None


def match_link(url: str, sources: tuple[str, ...] = SOURCES) -> tuple[str, str, str] | None:
    """(source, item type, item key) of the item a link's URL is, among `sources`, or None when it is none's item."""
    for source_id in sources:
        module = source(source_id)
        found = module.match_link(url) if module else None
        if found:
            return source_id, found[0], found[1]
    return None


def match_key(source_id: str, item_type: str, text: str) -> str | None:
    """An item's key in its source's spelling (a Jira key upper case), or None when `source_id` is no source, it has no
    such item type, or `text` is not one of its keys. A source without `match_key` takes any key that is not empty."""
    module = source(source_id)
    if module is None or item_type not in getattr(module, "ITEMS", {}):
        return None
    text = text.strip()
    matcher: Callable[[str, str], str | None] | None = getattr(module, "match_key", None)
    return matcher(item_type, text) if matcher else text or None


def site_of(source_id: str, url: str) -> str | None:
    """The host a site source's item lives on (lower case), or None for a source whose keys need no site."""
    module = source(source_id)
    site: Callable[[str], str | None] | None = getattr(module, "site", None) if module else None
    return site(url) if site else None


def _host(text: str) -> str:
    text = text.strip().lower()
    if "://" in text:
        return urlsplit(text).hostname or ""
    return text.split("/")[0]


def hosts(base_url: object, sites: object) -> set[str]:
    """The hosts a tool is on: its base URL's and each of its sites' (`sites` as stored, JSON)."""
    try:
        listed: object = json.loads(str(sites or "[]"))
    except ValueError:
        listed = []
    names: list[str] = [str(site) for site in cast("list[object]", listed)] if isinstance(listed, list) else []
    return {_host(str(base_url or "")), *(_host(name) for name in names)} - {""}


def serving(conn: sqlite3.Connection, host: str) -> list[str]:
    """The ids of the tools bound to `host` alone (their base URL and every site on it, and on no other host), sorted. A tool on several hosts serves none: its keys could be either site's.
    A tool's hosts stay as they are once outside references name it (api_tools.keep_sites)."""
    found: list[str] = []
    for tool_id, base_url, sites in conn.execute("SELECT id, base_url, sites FROM tools ORDER BY id").fetchall():
        if hosts(base_url, sites) == {host}:
            found.append(str(tool_id))
    return found


def outside_item(
    conn: sqlite3.Connection,
    url: str,
    connection_id: str = "",
    sources: tuple[str, ...] = SOURCES,
) -> tuple[str, str, str, str] | None:
    """(source, item type, key, connection) of the item a link's URL is, among `sources`, or None when it is none's
    item. A site source's item takes the connection that serves its site: `connection_id` when given (ValueError when
    it serves another site), else the one tool on that host (ValueError for several; None for none, a plain link)."""
    found = match_link(url, sources)
    if not found:
        return None
    host = site_of(found[0], url)
    if host is None:
        return (*found, connection_id)
    tools = serving(conn, host)
    if connection_id:
        if connection_id not in tools:
            raise ValueError(f"That connection is not the one for {host}")
        return (*found, connection_id)
    if len(tools) > 1:
        raise ValueError(f"More than one connection serves {host}: name the one this link came through")
    return (*found, tools[0]) if tools else None


def parse_ref(conn: sqlite3.Connection, text: str) -> tuple[str, str, str, str]:
    """(source, item type, key, connection) of an outside item named as `<source>:<type>:<key>[@<connection>]`, or as a
    link's URL (its site's connection, `outside_item`). ValueError, saying what is wrong, for anything else."""
    text = text.strip()
    if text.startswith(("http://", "https://")):
        item = outside_item(conn, text)
        if not item:
            raise ValueError(f"{text} is not a link to an item of a connected source")
        return item
    head, at, connection = text.rpartition("@")
    if not at:
        head, connection = text, ""
    source_id, _, rest = head.partition(":")
    item_type, _, key = rest.partition(":")
    module = source(source_id)
    if module is None:
        raise ValueError(f"No source {source_id or '(none)'}; sources: {', '.join(SOURCES)}")
    items: dict[str, Item] = getattr(module, "ITEMS", {})
    if item_type not in items:
        raise ValueError(f"{source_id} has no item type {item_type or '(none)'}; its types: {', '.join(items)}")
    spelled = match_key(source_id, item_type, key)
    if not spelled:
        raise ValueError(f"{key or '(none)'} is not a {source_id} {item_type} key")
    if getattr(module, "site", None) and not connection.strip():
        raise ValueError(f"A {source_id} item is one connection's: add @<connection id>")
    return source_id, item_type, spelled, connection.strip()


ALREADY_FILED = "This item is already filed"  # a primary link held by a task the caller may not read: never named
PRIMARY_SOURCES = ("jira",)  # the sources a task may be filed for (`primary_of`); GitHub PRs link the ordinary way


def primary_of(conn: sqlite3.Connection, text: str) -> tuple[tuple[str, str, str, str], str]:
    """(item, URL to link) of a task create's primary link (POST /api/v2/tasks `primary_link`): a Jira issue as
    `parse_ref` reads it, linked by its URL when given as one, else by the page its connection's site has for it
    (jira.item_url). ValueError, saying what is wrong, for anything else."""
    from . import jira

    item = parse_ref(conn, text)
    if item[0] not in PRIMARY_SOURCES:
        raise ValueError("A primary link is a Jira issue: jira:issue:<key>@<connection>, or its URL")
    text = text.strip()
    if text.startswith(("http://", "https://")):
        return item, text
    return item, jira.item_url(conn, item[1], item[2], item[3])


def lookup(conn: sqlite3.Connection, text: str) -> tuple[tuple[tuple[str, str, str, str], str], str | None]:
    """A primary link (`primary_of`) and the id of the task holding it already, or None: what a create and its dry run
    read before anything else. ValueError for a primary link that names no Jira issue."""
    primary = primary_of(conn, text)
    return primary, primary_task(conn, primary[0])


def primary_task(conn: sqlite3.Connection, item: tuple[str, str, str, str]) -> str | None:
    """The id of the task holding the item's primary link (the partial unique index task_links_primary_item lets one
    task hold it), whatever its status, or None."""
    found = conn.execute(
        "SELECT task_id FROM task_links WHERE is_primary=1 AND source=? AND item_type=? AND item_key=? "
        "AND connection_id=?",
        item,
    ).fetchone()
    return str(found[0]) if found else None


def claim(conn: sqlite3.Connection, task_id: str, item: tuple[str, str, str, str]) -> None:
    """Make the task's link to the item its primary link. IntegrityError when another task holds it."""
    conn.execute(
        "UPDATE task_links SET is_primary=1 WHERE task_id=? AND source=? AND item_type=? AND item_key=? "
        "AND connection_id=?",
        (task_id, *item),
    )
