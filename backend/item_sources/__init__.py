# pyright: strict
"""Sources: places outside Tico a task can wait on or link to (docs/dev/pipelines-v3.md "Sources and items"). One module
each, named in SOURCES: GitHub (`github.py`) and Jira (`jira.py`, links only so far).

A source module has `SOURCE` (its id), `NAME`, `ITEMS` (its item types) and `CONDITIONS` (what a requirement of kind
`item` can ask of one of its items), `match_link(url)` (the item a link is), optionally `match_key(item_type, text)` (an
item named by its key, in the source's spelling), and the functions that read, save and act on its items.

An item is (connection, source, item type, key): the connection is the `tools` row it came through, '' for none (every
GitHub item: the company's one GitHub App), so two connections holding the same key are two items.
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

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


def parse_ref(text: str) -> tuple[str, str, str, str]:
    """(source, item type, key, connection) of an outside item named as `<source>:<type>:<key>[@<connection>]`, or as a
    link's URL (connection ''). ValueError, saying what is wrong, for anything else."""
    text = text.strip()
    if text.startswith(("http://", "https://")):
        found = match_link(text)
        if not found:
            raise ValueError(f"{text} is not a link to an item of {', '.join(SOURCES)}")
        return (*found, "")
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
    return source_id, item_type, spelled, connection.strip()
