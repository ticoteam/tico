# pyright: strict
"""Sources: places outside Tico a task can wait on (docs/pipelines-v3.md "Sources and items"). One module each, named in
SOURCES; GitHub (`github.py`) is the only one.

A source module has `ID`, `NAME`, `ITEMS` (its item types) and `CONDITIONS` (what a requirement of kind `item` can ask
of one of its items), `match_link(url)` (the item a link is), and the functions that read, save and act on its items.
"""

from importlib import import_module
from types import ModuleType

SOURCES = ("github",)


def source(source_id: str) -> ModuleType | None:
    """A source's module, or None for an unknown source."""
    if source_id not in SOURCES:
        return None
    return import_module(f"{__name__}.{source_id}")


def match_link(url: str) -> tuple[str, str, str] | None:
    """(source, item type, item key) of the item a link's URL is, or None when it is no source's item."""
    for source_id in SOURCES:
        module = source(source_id)
        found = module.match_link(url) if module else None
        if found:
            return source_id, found[0], found[1]
    return None
