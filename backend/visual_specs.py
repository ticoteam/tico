# pyright: strict
"""Visual specs: a product manager's spec is a Tico page filed on the task it describes (`tico page publish --spec
--task <id>`). The styles are inlined into the stored HTML when it is published (inline_styles), so the page reads the
same wherever it is opened. A task has no description: tasks filed under it read the spec from its documents. A spec
stage with a person's OK comes with the Product pipeline."""

import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from . import hubdb as H

# The hub core is not typed yet; this alias leaves its calls to be checked where they are made.
hub: Any = H

# Read in this order: tokens define the :root variables the rest use.
STYLES = ("tokens.css", "base.css", "components.css", "brand.css")
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
