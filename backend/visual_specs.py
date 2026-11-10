# pyright: strict
"""Visual specs: a product manager's spec is a Tico page filed on the task it describes (`tico page publish --spec
--task <id>`). The styles are inlined into the stored HTML when it is published (inline_styles), so the page reads the
same wherever it is opened. Tasks filed under that task, and their own sub-tasks, get the spec's link and its text in
their body (child_body). A spec stage with a person's OK comes with the Product pipeline."""

import html
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from . import hubdb as H
from . import task_relations as TR

# The hub core and the relations are not typed yet; these aliases leave their calls to be checked where they are made.
hub: Any = H
relations: Any = TR

# Read in this order: tokens define the :root variables the rest use.
STYLES = ("tokens.css", "base.css", "components.css", "brand.css")
TEXT_CAP = 6000
CARD = "card"  # pages.spec: the value that marks a page as the live spec on its task

_files: Any = None
_base = ""  # this server's address, so a ticket's page link opens from anywhere


def bind(files: Any, public_url: str | None = None) -> None:
    """The Files service (backend/files.py), to read a spec's stored bytes, and this server's address, set when the pages
    are installed: a ticket links its spec page by route."""
    global _files, _base
    _files = files
    _base = str(public_url or "").rstrip("/")


def page_link(file_id: str) -> str:
    """The route the Tico UI opens a page at."""
    return f"{_base}/#/page/{quote(file_id, safe='')}"


# The app shell lays body out as a grid with a left rail (base.css); a page is a plain document.
PAGE_BODY = "body{display:block;min-height:0}"


def inline_styles(data: bytes) -> bytes:
    """The page with the shared styles in a <style> at the start of its head, read from ui/styles at publish time, so
    the page's own styles come after them and win."""
    root = Path(__file__).resolve().parents[1] / "ui" / "styles"
    css = "\n".join((root / name).read_text(encoding="utf-8") for name in STYLES) + "\n" + PAGE_BODY
    style = "<style>" + css.replace("</style", "<\\/style") + "</style>"
    text = data.decode("utf-8", "replace")
    if re.search(r"<head\b[^>]*>", text, re.I):
        text = re.sub(r"<head\b[^>]*>", lambda m: m.group(0) + style, text, count=1, flags=re.I)
    else:
        text = style + text
    return text.encode("utf-8")


def page_text(data: bytes) -> str:
    """The words of a spec page, for a ticket body: no markup, styles or scripts, capped."""
    text = data.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", text)
    text = re.sub(r"(?is)</?(b|i|em|strong|span|a|code|u|small|sup|sub)\b[^>]*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()[:TEXT_CAP]


def spec_page(c: Any, task_id: str) -> Any:
    """The live spec filed on this task, or None."""
    return c.execute(
        "SELECT f.id,f.title,v.digest FROM pages p JOIN bot_files f ON f.id=p.file_id JOIN bot_file_versions v "
        "ON v.file_id=f.id AND v.version=f.current_version "
        "WHERE p.spec=? AND f.task_id=? AND p.archived=0 AND f.archived=0 AND p.replaced_by_id IS NULL "
        "AND f.state!='deleted' ORDER BY f.last_activity_at DESC LIMIT 1",
        (CARD, task_id),
    ).fetchone()


def check_publish(c: Any, task_id: str) -> None:
    """A spec goes on the task it describes."""
    row = cast(dict[str, Any] | None, hub.task(c, task_id)) if task_id else None
    if not row:
        from .store import Problem

        raise Problem("validation", "A spec is published on the task it describes (--task)", 422)


def _block(title: str, file_id: str, text: str) -> str:
    """The spec's lines in a task's body. A restricted spec (no text) is named by its link only, never its title."""
    if not text:
        return f"Spec page: {page_link(file_id)}"
    return f"Spec: {title}\nSpec page: {page_link(file_id)}\n\n{text}"


def _task_with_spec(c: Any, parent_id: str) -> tuple[dict[str, Any], Any] | None:
    """The nearest of the new task's parent and grandparent that has a live spec, with that spec."""
    task_id: str | None = parent_id
    for _ in range(2):
        if not task_id:
            return None
        row = cast(dict[str, Any] | None, hub.task(c, task_id))
        if not row:
            return None
        spec = spec_page(c, task_id)
        if spec is not None:
            return row, spec
        task_id = relations.parent_of(c, task_id)
    return None


def _restricted(c: Any, task: dict[str, Any], file_id: str) -> bool:
    """Whether the spec's text stays out of the bodies of tasks filed under it. The text goes down only when everyone
    who can read the new task can read the spec: the new task's owner and privacy are not settled when its body is
    written, so it may be read by anyone in the company, and the spec's text goes down only when anyone may read it
    too. Kept out: a private task, a company task whose owner or requester is a bot not everyone may read (auth
    task_row: a company task involving that bot is hidden from them), and a spec page narrowed to some readers
    (pages.audience). Its link still goes in; opening it is checked as any page is."""
    if hub.task_private(c, task):
        return True
    from . import teammate_access

    access: Any = teammate_access
    for party in (task.get("owner_id"), task.get("requester_id")):
        if hub.is_bot(party) and not access.document(c, str(party))["read"]["everyone"]:
            return True
    audience = c.execute("SELECT audience FROM pages WHERE file_id=?", (file_id,)).fetchone()
    return bool(audience and audience[0])


def child_body(c: Any, parent_id: Any, body: Any) -> Any:
    """A task filed under a task with a spec, or under one of its sub-tasks, gets the spec's link and text."""
    if not parent_id:
        return body
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'").fetchone():
        return body  # a bare hub database without the server's pages
    found = _task_with_spec(c, str(parent_id))
    if found is None:
        return body
    task, spec = found
    restricted = _restricted(c, task, spec["id"])
    text = page_text(_files.blobs.get(spec["digest"])) if _files and not restricted else ""
    return (str(body or "").rstrip() + "\n\n" + _block(spec["title"], spec["id"], text)).strip()
