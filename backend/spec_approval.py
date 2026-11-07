# pyright: strict
"""Visual specs: a product manager's spec is a Tico page filed on its Product roadmap card (`hub page publish --spec
--task <card>`). Only a person moves the card from Spec to Approved: by hand, by answering the approval question a bot
asks under the card on the spec topic (after_answer), or through the person's own Assistant on words they wrote in its
chat (_may_approve). That hands the spec to a builder: a frozen copy of the spec page is filed and pinned on a new Dev
ticket under the card, and the ticket's body carries the page link and its text. Review and QA children of the ticket
get the same link and text (child_body).

The styles are inlined into the stored HTML when the spec is published (inline_styles), so the page reads the same
wherever it is opened, and the frozen copy shares the spec's stored bytes: nothing is snapshotted twice."""

import html
import re
from collections.abc import Callable
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

SPEC = "Spec"
APPROVED = "Approved"
# The question topic a bot asks the approval under (`hub task question --topic spec-approval`), and its approving option.
TOPIC = "spec-approval"
APPROVE = "Approve"
# Read in this order: tokens define the :root variables the rest use.
STYLES = ("tokens.css", "base.css", "components.css", "brand.css")
TEXT_CAP = 6000
NO_DEV = "No Dev tickets type, so no builder ticket was made"
CARD, FROZEN = "card", "frozen"  # pages.spec values: the live spec on a card, its copy on a Dev ticket

_files: Any = None
_base = ""  # this server's address, so a ticket's page link opens from anywhere


def bind(files: Any, public_url: str | None = None) -> None:
    """The Files service (backend/files.py) and this server's address, set when the pages are installed: a frozen copy
    is a version of a file, and a ticket links its page by route."""
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


def _may_approve(actor: str) -> bool:
    """A person moves a spec to Approved: directly, or through their own Assistant on words they wrote in its chat (the
    DELEGATION record assistant.instruction makes, for that person, which the move then records as via assistant). A bot,
    an automation or BotOps acting with a person's rights is refused."""
    if not hub.is_human(actor):
        return False
    via = hub.VIA.get()
    if not via:
        return True
    lent: Any = hub.DELEGATION.get() or {}
    return bool(via == "assistant" and lent.get("for") == actor and lent.get("run") == "asked" and lent.get("cited"))


def check(c: Any, actor: str, row: Any, step_id: Any) -> None:
    """Before the move is written: only a person moves a spec to Approved (_may_approve), and the card needs a spec."""
    if not approval(c, row, step_id):
        return
    if not _may_approve(actor):
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
    return f"Spec: {title}\nSpec page: {page_link(file_id)}\n\n{text}"


def _attempt(c: Any, write: Callable[[], Any]) -> str | None:
    """One write in a savepoint: a refusal rolls it back and comes back as its reason, so the caller can say why."""
    c.execute("SAVEPOINT spec_write")
    try:
        write()
    except hub.Refused as exc:
        c.execute("ROLLBACK TO spec_write")
        return str(exc.detail or exc)
    finally:
        c.execute("RELEASE spec_write")
    return None


def _note(c: Any, card_id: str, text: str) -> None:
    """A line from the system on the card: the approval did not go through, and why."""
    hub.task_comment(c, hub.KEEPER, card_id, text, wake=False)


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
    # The ticket links the frozen copy, the page the builder reads; the live spec may change after approval.
    _attempt(c, lambda: hub.task_update(c, actor, ticket_id, body=_block(spec["title"], row["id"], text)))
    hub.event(c, actor, "spec.approved", card["id"], {"ticket": ticket_id, "page": row["id"]})


def after_answer(c: Any, who: Any, row: Any, task_id: str, answers: dict[str, list[str]], dismiss: bool) -> None:
    """A question on the spec topic that the person it asks answers Approve moves its card to Approved, as that person.
    Any other answer leaves the card where it is. The question is closed already (question_tasks.answer), so a refused
    move, or a card no longer in Spec, is a note on the card."""
    if dismiss or hub.question_topic(row.get("question_topic")) != TOPIC:
        return
    picks = {label.casefold() for labels in answers.values() for label in labels}
    if APPROVE.casefold() not in picks:
        return
    card_id = relations.parent_of(c, task_id)
    card = cast(dict[str, Any] | None, hub.task(c, card_id)) if card_id else None
    if not card_id or not card:
        return
    if who.actor != row["owner_id"]:
        _note(c, card_id, f"Only the person it asks approves a spec. The card stays in {SPEC}.")
        return
    target = c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (card["type_id"], APPROVED)).fetchone()
    if not target or not approval(c, card, target[0]):
        _note(c, card_id, f"Nothing approved: the card is not in {SPEC}.")
        return
    reason = _attempt(c, lambda: hub.task_update(c, who.actor, card_id, step=target[0]))
    if reason:
        _note(c, card_id, f"Not approved: {reason}")


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
