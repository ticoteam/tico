# pyright: strict
"""Visual specs: a product manager's spec is a Tico page filed on its Product roadmap card (`hub page publish --spec
--task <card>`). Only a person moves the card from Spec to Approved. That hands the spec to a builder: a frozen copy
of the spec page is filed and pinned on a new Dev ticket under the card, and the ticket's body carries the page link
and its text. Review and QA children of the ticket get the same link and text (child_body).

The styles are inlined into the stored HTML when the spec is published (inline_styles), so the page reads the same
wherever it is opened, and the frozen copy shares the spec's stored bytes: nothing is snapshotted twice."""

import html
import re
from pathlib import Path
from typing import Any, cast

from . import hubdb as H
from . import roadmap_flow as RF

# The hub core and the roadmap are not typed yet; these aliases leave their calls to be checked where they are made.
hub: Any = H
roadmap: Any = RF

SPEC = "Spec"
APPROVED = "Approved"
# Read in this order: tokens define the :root variables the rest use.
STYLES = ("tokens.css", "base.css", "components.css", "brand.css")
TEXT_CAP = 6000
NO_DEV = "No Dev tickets type, so no builder ticket was made"
CARD, FROZEN = "card", "frozen"  # pages.spec values: the live spec on a card, its copy on a Dev ticket

_files: Any = None


def bind(files: Any) -> None:
    """The Files service (backend/files.py), set when the pages are installed: a frozen copy is a version of a file."""
    global _files
    _files = files


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


def migrate(c: Any) -> None:
    """pages.spec: which pages are specs (CARD) or frozen copies (FROZEN). Older rows stay NULL."""
    if "spec" not in {r[1] for r in c.execute("PRAGMA table_info(pages)")}:
        c.execute("ALTER TABLE pages ADD COLUMN spec TEXT")


def _step_name(c: Any, step_id: Any) -> str | None:
    row = c.execute("SELECT name FROM task_steps WHERE id=?", (step_id,)).fetchone() if step_id else None
    return row[0] if row else None


def approval(c: Any, row: Any, step_id: Any) -> bool:
    """Whether this move takes a Product roadmap card from Spec to Approved."""
    product = roadmap.role_type(c, roadmap.PRODUCT)
    return bool(
        product
        and row.get("type_id") == product
        and _step_name(c, row.get("step_id")) == SPEC
        and _step_name(c, step_id) == APPROVED
    )


def spec_page(c: Any, card_id: str) -> Any:
    """The live spec filed on this card, or None."""
    return c.execute(
        "SELECT f.id,f.title,f.bot_id,f.scope,f.task_id,v.blob_id,v.digest,v.size,v.name,v.mime "
        "FROM pages p JOIN bot_files f ON f.id=p.file_id JOIN bot_file_versions v "
        "ON v.file_id=f.id AND v.version=f.current_version "
        "WHERE p.spec=? AND f.task_id=? AND p.archived=0 AND f.state!='deleted' ORDER BY f.last_activity_at DESC LIMIT 1",
        (CARD, card_id),
    ).fetchone()


def _frozen(c: Any, ticket_id: str) -> Any:
    return c.execute(
        "SELECT f.id,f.title,f.task_id FROM pages p JOIN bot_files f ON f.id=p.file_id "
        "WHERE p.spec=? AND f.task_id=? AND f.state!='deleted' ORDER BY f.first_activity_at DESC LIMIT 1",
        (FROZEN, ticket_id),
    ).fetchone()


def check(c: Any, actor: str, row: Any, step_id: Any) -> None:
    """Before the move is written: a bot, or anyone acting for a bot, cannot approve a spec, and a card needs one."""
    if not approval(c, row, step_id):
        return
    if not hub.is_human(actor) or hub.VIA.get():
        hub.refuse(c, actor, "approval", "Only a person moves a spec to Approved.")
    if spec_page(c, row["id"]) is None:
        hub.refuse(c, actor, "spec", "Publish the spec page on this card first")


def check_publish(c: Any, task_id: str) -> None:
    """A spec goes on a Product roadmap card, and nowhere else."""
    row = cast(dict[str, Any] | None, hub.task(c, task_id)) if task_id else None
    if not row or row.get("type_id") != roadmap.role_type(c, roadmap.PRODUCT):
        from .store import Problem

        raise Problem("validation", "A spec is published on a Product roadmap card (--task)", 422)


def _dev_type(c: Any) -> str | None:
    for row in c.execute("SELECT id,name FROM task_types WHERE role=? ORDER BY created_at,id", (roadmap.ENGINEERING,)):
        if roadmap._dev(row):
            return row[0]
    return None


def _id_of(task: Any) -> str:
    """The id of a task as the hub returns it (a row or the bare id)."""
    value: Any = task["id"] if hasattr(task, "keys") else task
    return str(value)


def _block(title: str, file_id: str, text: str) -> str:
    return f"Spec: {title}\nSpec page: {file_id}\n\n{text}"


def handoff(c: Any, actor: str, card: Any) -> None:
    """After a person's move to Approved: the builder ticket with the frozen spec. `card` is the card's row before
    the move; the caller has checked it is a person's move from Spec."""
    spec = spec_page(c, card["id"])
    if spec is None:
        return
    dev = _dev_type(c)
    if not dev:
        hub.event(c, actor, "spec.approved", card["id"], {"note": NO_DEV})
        return
    # The step the allocator hands tickets out at (its flow has an assign pool), else the type's first step.
    first = (
        c.execute(
            "SELECT id FROM task_steps WHERE type_id=? AND flow LIKE '%\"assign\"%' ORDER BY position LIMIT 1", (dev,)
        ).fetchone()
        or c.execute("SELECT id FROM task_steps WHERE type_id=? ORDER BY position LIMIT 1", (dev,)).fetchone()
    )
    # The placeholder owner until the allocator hands the ticket to a pool bot: the type's manager, else the card's owner.
    dev_type: Any = hub.type_get(c, dev) or {}
    owner: str = str(dev_type.get("manager") or hub.actor_id(card.get("owner_id") or ""))
    text = page_text(_files.blobs.get(spec["digest"])) if _files else ""
    title = f"Build: {card.get('title') or spec['title']}"
    ticket = hub.task_create(
        c,
        actor,
        title,
        _block(spec["title"], spec["id"], text),
        owner,
        parent_id=card["id"],
        type=dev,
        step=first[0] if first else None,
    )
    ticket_id = _id_of(ticket)
    if _files is None:
        return
    from . import pages as pages_module

    pages_api: Any = pages_module
    row, _, _ = _files.add_version(
        c,
        bot=spec["bot_id"],
        actor=actor,
        scope=spec["scope"],
        task=ticket_id,
        conversation=None,
        attempt=None,
        identity=f"spec:{spec['id']}:{ticket_id}",
        title=f"Spec: {spec['title']}",
        name="spec.html",
        mime=spec["mime"],
        blob_id=spec["blob_id"],
        digest=spec["digest"],
        size=spec["size"],
        explicit_title=True,
    )
    now = hub.now()
    c.execute(
        "INSERT INTO pages(file_id,cadence,as_of,created_at,updated_at,spec) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(file_id) DO UPDATE SET spec=?,updated_at=?",
        (row["id"], "none", None, now, now, FROZEN, FROZEN, now),
    )
    pages_api.Pages.pin_to_task(c, row, ticket_id, actor=actor)
    hub.event(c, actor, "spec.approved", card["id"], {"ticket": ticket_id, "page": row["id"]})


def child_body(c: Any, ticket_id: Any, body: Any) -> str:
    """A child of a Dev ticket that carries a frozen spec (reviewers, QA) gets the spec's link and text too."""
    if not ticket_id:
        return body
    frozen = _frozen(c, ticket_id)
    if not frozen:
        return body
    stored = c.execute(
        "SELECT v.digest FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id AND v.version=f.current_version "
        "WHERE f.id=?",
        (frozen["id"],),
    ).fetchone()
    text = page_text(_files.blobs.get(stored[0])) if _files and stored else ""
    return (str(body or "").rstrip() + "\n\n" + _block(frozen["title"], frozen["id"], text)).strip()
