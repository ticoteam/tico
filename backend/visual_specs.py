# pyright: strict
"""Visual specs: a product manager's spec is a Tico page filed on its Product roadmap card (`hub page publish --spec
--task <card>`). The styles are inlined into the stored HTML when it is published (inline_styles), so the page reads the
same wherever it is opened. Tickets filed under the card (the builder ticket the pipeline's spawn link files) and their
own children (review, QA) get the spec's link and its text in their body (child_body).

Approval is the pipeline's: Review's "Spec approved" requirement is a person's approval (pipeline_setup), and the
Review -> Engineering spawn link files the builder ticket (pipeline_links.spawn)."""

import html
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from . import hubdb as H
from . import roadmap_flow as RF
from . import task_relations as TR

# The hub core, the roadmap and the relations are not typed yet; these aliases leave their calls to be checked where
# they are made.
hub: Any = H
roadmap: Any = RF
relations: Any = TR

# Read in this order: tokens define the :root variables the rest use.
STYLES = ("tokens.css", "base.css", "components.css", "brand.css")
TEXT_CAP = 6000
CARD = "card"  # pages.spec: the live spec on a Product roadmap card

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


def inline_styles(data: bytes) -> bytes:
    """The page with the shared styles in a <style> at the end of its head, read from ui/styles at publish time."""
    root = Path(__file__).resolve().parents[1] / "ui" / "styles"
    css = "\n".join((root / name).read_text(encoding="utf-8") for name in STYLES)
    style = "<style>" + css.replace("</style", "<\\/style") + "</style>"
    text = data.decode("utf-8", "replace")
    if re.search(r"</head>", text, re.I):
        text = re.sub(r"</head>", lambda m: style + m.group(0), text, count=1, flags=re.I)
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


def spec_page(c: Any, card_id: str) -> Any:
    """The live spec filed on this card, or None."""
    return c.execute(
        "SELECT f.id,f.title,v.digest FROM pages p JOIN bot_files f ON f.id=p.file_id JOIN bot_file_versions v "
        "ON v.file_id=f.id AND v.version=f.current_version "
        "WHERE p.spec=? AND f.task_id=? AND p.archived=0 AND f.state!='deleted' ORDER BY f.last_activity_at DESC LIMIT 1",
        (CARD, card_id),
    ).fetchone()


def check_publish(c: Any, task_id: str) -> None:
    """A spec goes on a Product roadmap card, and nowhere else."""
    row = cast(dict[str, Any] | None, hub.task(c, task_id)) if task_id else None
    if not row or row.get("type_id") != roadmap.role_type(c, roadmap.PRODUCT):
        from .store import Problem

        raise Problem("validation", "A spec is published on a Product roadmap card (--task)", 422)


def _block(title: str, file_id: str, text: str) -> str:
    return f"Spec: {title}\nSpec page: {page_link(file_id)}\n\n{text}".rstrip()


def _card_with_spec(c: Any, parent_id: str) -> tuple[dict[str, Any], Any] | None:
    """The nearest of the new ticket's parent and grandparent that is a card with a live spec, with that spec. A parent
    filed with neutral text (its readers may not open its source) passes nothing further down."""
    from . import pipeline_links

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
        if pipeline_links.NEUTRAL_BODY in str(row.get("body") or ""):
            return None
        task_id = relations.parent_of(c, task_id)
    return None


def _restricted(c: Any, card: dict[str, Any], file_id: str) -> bool:
    """Whether the spec's text stays out of ticket bodies: a private card, a card on a members-only pipeline, or a spec page
    narrowed to some readers (pages.audience). Its link still goes in; opening it is checked as any page is."""
    if hub.task_private(c, card):
        return True
    visibility = c.execute("SELECT pipeline_visibility FROM task_types WHERE id=?", (card.get("type_id"),)).fetchone()
    if visibility and str(visibility[0] or "company") == "members":
        return True
    audience = c.execute("SELECT audience FROM pages WHERE file_id=?", (file_id,)).fetchone()
    return bool(audience and audience[0])


def child_body(c: Any, parent_id: Any, body: Any) -> Any:
    """A ticket filed under a card with a spec, or under one of the card's tickets, gets the spec's link and text."""
    from . import pipeline_links

    if not parent_id or pipeline_links.NEUTRAL_BODY in str(body or ""):
        return body  # a spawn whose readers may not open the source carries none of it
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'").fetchone():
        return body  # a bare hub database without the server's pages
    found = _card_with_spec(c, str(parent_id))
    if found is None:
        return body
    card, spec = found
    restricted = _restricted(c, card, spec["id"])
    text = page_text(_files.blobs.get(spec["digest"])) if _files and not restricted else ""
    return (str(body or "").rstrip() + "\n\n" + _block(spec["title"], spec["id"], text)).strip()
