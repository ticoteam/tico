# pyright: strict
"""Sources: places outside Tico a task can wait on (docs/pipelines-v3.md "Sources and items"). One module each, named in
SOURCES; GitHub (`github.py`) is the only one.

A source module has `SOURCE` (its id), `NAME`, `ITEMS` (its item types) and `CONDITIONS` (what a requirement of kind
`item` can ask of one of its items), `match_link(url)` (the item a link is), and the functions that read, save and act on
its items.
"""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Any

SOURCES = ("github",)


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
    """A source's module, or None for an unknown source."""
    if source_id not in SOURCES:
        return None
    return import_module(f"{__name__}.{source_id}")


def condition(source_id: object, condition_id: object) -> Condition | None:
    """A source's condition, or None for an unknown source or condition."""
    module = source(source_id) if isinstance(source_id, str) else None
    conditions: dict[str, Condition] = getattr(module, "CONDITIONS", {}) if module else {}
    return conditions.get(condition_id) if isinstance(condition_id, str) else None


def match_link(url: str) -> tuple[str, str, str] | None:
    """(source, item type, item key) of the item a link's URL is, or None when it is no source's item."""
    for source_id in SOURCES:
        module = source(source_id)
        found = module.match_link(url) if module else None
        if found:
            return source_id, found[0], found[1]
    return None
