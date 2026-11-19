"""The skills Tico ships in its bot catalog (templates/catalog), listed on Library › Skills beside the directory
(docs/skills.md).

A role template gives its bots skills in their repositories: its own (`skills/<name>`, `.claude/skills/<name>`) and the
library's that its card names in `shared_files` (`_shared/.claude/skills/<name>`). They never pass through the
directory, so it never listed them. This index holds one row per name: its description and job function from SKILL.md's
frontmatter, the templates that ship it, and the folder its files are read from (the library's copy first, else the
first template's). It is built once per catalog folder and process: the catalog ships with the server and changes only
with it. Only one skill's own page reads its files.

Which bots have one is what the catalog says: the live bots made from a template that ships it (`bot_settings.template`).
A bot may have changed its copy since; that is not known here, and nothing below claims it.
"""

import os
import threading
from typing import Any

from clients import catalog
from clients import skills as bundles

_INDEX: dict[str, dict[str, dict[str, Any]]] = {}  # catalog folder -> {name: entry}
_ROLES: dict[str, dict[str, str]] = {}  # catalog folder -> {template: the role's name}
_LOCK = threading.Lock()


def shipped(directory: Any = None) -> dict[str, dict[str, Any]]:
    """{name: {name, description, function, templates, folder}} of every skill the catalog ships, read once."""
    root = str(catalog.catalog_dir(directory))
    with _LOCK:
        if root not in _INDEX:
            _INDEX[root], _ROLES[root] = _build(directory)
        return _INDEX[root]


def forget():
    """Drop the index, so the next read builds it again (a test that writes its own catalog)."""
    with _LOCK:
        _INDEX.clear()
        _ROLES.clear()


def _build(directory):
    shared = catalog.catalog_dir(directory) / catalog.SHARED
    found, roles = {}, {}
    for card in catalog.cards(directory):
        roles[card["template"]] = str(card.get("name") or card["template"])
        try:
            folders = catalog.skill_folders(card["template"], directory, card)
        except (OSError, ValueError):
            continue  # a card the catalog cannot lay out ships nothing
        for name, folder in folders.items():
            if not bundles.NAME.match(name) or name in bundles.RESERVED:
                continue
            entry = found.setdefault(name, {"name": name, "templates": [], "folder": None, "rank": None})
            entry["templates"].append(card["template"])
            rank = (not folder.is_relative_to(shared), card["template"])
            if entry["rank"] is None or rank < entry["rank"]:
                entry["folder"], entry["rank"] = folder, rank
    out = {}
    for name in sorted(found):
        entry = found[name]
        try:
            text = (entry["folder"] / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out[name] = {
            "name": name,
            "description": str(bundles.frontmatter(text).get("description", ""))[: bundles.MAX_DESCRIPTION],
            "function": bundles.function_of([{"path": "SKILL.md", "content": text}]),
            "templates": sorted(entry["templates"]),
            "folder": entry["folder"],
        }
    return out, roles


def role_names(directory=None):
    """{template: the role's name} from the cards, read with the index."""
    shipped(directory)
    with _LOCK:
        return _ROLES[str(catalog.catalog_dir(directory))]


def files(entry):
    """The skill's text files as [{path, content}], SKILL.md included, within the bundle limits: what its page shows.
    A file that is not UTF-8 text is left out, and reading stops at the limits rather than cutting a file."""
    folder, out, total = entry["folder"], [], 0
    for root, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in bundles.SKIP_DIRS)
        for name in sorted(names):
            path = os.path.join(root, name)
            if name in bundles.SKIP_FILES or os.path.islink(path):
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
    return bundles.normalize(out)


def bots_by_template(c):
    """{template: [{id, name}]} of the live bots made from each template, by name."""
    out = {}
    for row in c.execute(
        "SELECT b.id, b.slug, b.display_name, s.template FROM bot_settings s JOIN bots b ON b.id = s.bot_id "
        "WHERE COALESCE(s.template, '') <> '' AND COALESCE(b.state, 'active') <> 'archived' "
        "ORDER BY COALESCE(b.display_name, b.slug, b.id) COLLATE NOCASE"
    ):
        out.setdefault(row["template"], []).append({"id": row["id"], "name": row["display_name"] or row["slug"]})
    return out


def bots_with(entry, by_template):
    """The live bots made from a template that ships this skill, each once, by name."""
    seen, out = set(), []
    for template in entry["templates"]:
        for bot in by_template.get(template, []):
            if bot["id"] not in seen:
                seen.add(bot["id"])
                out.append(bot)
    return sorted(out, key=lambda bot: str(bot["name"] or bot["id"]).lower())
