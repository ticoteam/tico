# pyright: strict
"""The skills Tico ships in its bot catalog (templates/catalog), listed on Library › Skills beside the directory
(docs/skills.md).

A role template gives its bots skills in their repositories: its own (`skills/<name>`, `.claude/skills/<name>`) and the
library's that its card names in `shared_files` (`_shared/.claude/skills/<name>`). They never pass through the
directory, so it never listed them. This index holds one row per name: its description and job function from SKILL.md's
frontmatter, the templates that ship it, and each template's copy (the library's first, else the first template's). It
is built once per catalog folder and process: the catalog ships with the server and changes only with it. Only one
skill's own page reads its files.

Nothing outside the catalog folder is ever read: a skill folder that is a symbolic link, or one whose real path leaves
the catalog, is not indexed, and a file whose real path leaves it (or that is a link) is not served.

Which bots have one is what the catalog says: the provisioned bots (not planned, not archived) made from a template that
ships it (`bot_settings.template`). A bot may have changed its copy since; that is not known here, and nothing below
claims it.
"""

import os
import sqlite3
import threading
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from clients import catalog as _catalog
from clients import skills as _bundles

# The clients are plain, untyped code shared with the CLI and the runner.
catalog: Any = _catalog
bundles: Any = _bundles

Entry = dict[str, Any]  # {name, description, function, templates, folder, copies: {template: folder}}
Bot = dict[str, Any]  # {id, name}

_INDEX: dict[str, dict[str, Entry]] = {}  # catalog folder -> {name: entry}
_ROLES: dict[str, dict[str, str]] = {}  # catalog folder -> {template: the role's name}
_SHIPS: dict[str, dict[str, frozenset[str]]] = {}  # catalog folder -> {template: the names it ships}
_LOCK = threading.Lock()


def shipped(directory: Any = None) -> dict[str, Entry]:
    """{name: {name, description, function, templates, folder, copies}} of every skill the catalog ships, read once."""
    root = str(catalog.catalog_dir(directory))
    with _LOCK:
        if root not in _INDEX:
            _INDEX[root], _ROLES[root], _SHIPS[root] = _build(directory)
        return _INDEX[root]


def forget() -> None:
    """Drop the index, so the next read builds it again (a test that writes its own catalog)."""
    with _LOCK:
        _INDEX.clear()
        _ROLES.clear()
        _SHIPS.clear()


def role_names(directory: Any = None) -> dict[str, str]:
    """{template: the role's name} from the cards, read with the index."""
    shipped(directory)
    with _LOCK:
        return _ROLES[str(catalog.catalog_dir(directory))]


def ships(directory: Any = None) -> dict[str, frozenset[str]]:
    """{template: the names of the skills it ships}, read with the index."""
    shipped(directory)
    with _LOCK:
        return _SHIPS[str(catalog.catalog_dir(directory))]


def _inside(path: Path, root: Path) -> bool:
    """Whether `path`'s real path is under the catalog's."""
    try:
        return Path(os.path.realpath(path)).is_relative_to(root)
    except OSError:
        return False


def _build(directory: Any) -> tuple[dict[str, Entry], dict[str, str], dict[str, frozenset[str]]]:
    base = Path(catalog.catalog_dir(directory))
    root = Path(os.path.realpath(base))
    shared = base / catalog.SHARED
    found: dict[str, dict[str, Any]] = {}
    roles: dict[str, str] = {}
    by_template: dict[str, set[str]] = {}
    for card in cast(list[dict[str, Any]], catalog.cards(directory)):
        template = str(card["template"])
        roles[template] = str(card.get("name") or template)
        try:
            folders = cast(dict[str, Path], catalog.skill_folders(template, directory, card))
        except (OSError, ValueError):
            continue  # a card the catalog cannot lay out ships nothing
        for name, folder in folders.items():
            if not bundles.NAME.match(name) or name in bundles.RESERVED:
                continue
            if folder.is_symlink() or not _inside(folder, root) or not _inside(folder / "SKILL.md", root):
                continue  # never read outside the catalog
            entry = found.setdefault(name, {"name": name, "copies": {}})
            entry["copies"][template] = folder
            by_template.setdefault(template, set()).add(name)
    out: dict[str, Entry] = {}
    for name in sorted(found):
        copies = cast(dict[str, Path], found[name]["copies"])
        # The library's copy first, else the first template's (by name).
        ranked = sorted(copies, key=lambda t: (not copies[t].is_relative_to(shared), t))
        folder = copies[ranked[0]]
        try:
            text = (folder / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out[name] = {
            "name": name,
            "description": str(bundles.frontmatter(text).get("description", ""))[: bundles.MAX_DESCRIPTION],
            "function": bundles.function_of([{"path": "SKILL.md", "content": text}]),
            "templates": sorted(copies),
            "folder": folder,
            "ranked": ranked,
            "copies": copies,
        }
    return out, roles, {t: frozenset(n for n in names if n in out) for t, names in by_template.items()}


def folder_for(entry: Entry, with_bots: Collection[str] = ()) -> Path:
    """The copy a skill's page shows: one from a role that has bots here (in the index's order), else the index's own."""
    copies = cast(dict[str, Path], entry["copies"])
    for template in cast(list[str], entry["ranked"]):
        if template in with_bots:
            return copies[template]
    return cast(Path, entry["folder"])


def files(entry: Entry, with_bots: Collection[str] = (), directory: Any = None) -> list[dict[str, str]]:
    """The skill's text files as [{path, content}], SKILL.md included, within the bundle limits: what its page shows.
    A file that is not UTF-8 text, is a link, or whose real path leaves the catalog is left out, and reading stops at
    the limits rather than cutting a file."""
    root = Path(os.path.realpath(catalog.catalog_dir(directory)))
    folder = folder_for(entry, with_bots)
    if folder.is_symlink() or not _inside(folder, root):
        return []
    out: list[dict[str, str]] = []
    total = 0
    for here, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in bundles.SKIP_DIRS and not os.path.islink(os.path.join(here, d)))
        for name in sorted(names):
            path = os.path.join(here, name)
            if name in bundles.SKIP_FILES or os.path.islink(path) or not _inside(Path(path), root):
                continue
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
                text = data.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "\0" in text or len(out) >= bundles.MAX_FILES or total + len(text) > bundles.MAX_CHARS:
                continue
            total += len(text)
            out.append({"path": os.path.relpath(path, folder).replace(os.sep, "/"), "content": text})
    return cast(list[dict[str, str]], bundles.normalize(out))


def bots_by_template(c: sqlite3.Connection) -> dict[str, list[Bot]]:
    """{template: [{id, name}]} of the provisioned bots (not planned, not archived) made from each template, by name."""
    out: dict[str, list[Bot]] = {}
    for row in c.execute(
        "SELECT b.id, b.slug, b.display_name, s.template FROM bot_settings s JOIN bots b ON b.id = s.bot_id "
        "WHERE COALESCE(s.template, '') <> '' AND COALESCE(b.state, 'active') NOT IN ('planned', 'archived') "
        "ORDER BY COALESCE(b.display_name, b.slug, b.id) COLLATE NOCASE"
    ):
        out.setdefault(row["template"], []).append({"id": row["id"], "name": row["display_name"] or row["slug"]})
    return out


def bots_with(entry: Entry, by_template: Mapping[str, Iterable[Bot]]) -> list[Bot]:
    """The provisioned bots made from a template that ships this skill, each once, by name."""
    seen: set[str] = set()
    out: list[Bot] = []
    for template in cast(list[str], entry["templates"]):
        for bot in by_template.get(template, []):
            if bot["id"] not in seen:
                seen.add(bot["id"])
                out.append(bot)
    return sorted(out, key=lambda bot: str(bot["name"] or bot["id"]).lower())


def shipped_to(c: sqlite3.Connection, directory: Any = None) -> dict[str, frozenset[str]]:
    """{bot id: the names its template ships}, for every bot made from a template (whatever its state: a use it made
    before it was archived was still of the shipped skill). A bot's own skill of the same name is not one of these."""
    by_template = ships(directory)
    out: dict[str, frozenset[str]] = {}
    for row in c.execute("SELECT bot_id, template FROM bot_settings WHERE COALESCE(template, '') <> ''"):
        names = by_template.get(row["template"])
        if names:
            out[row["bot_id"]] = names
    return out
