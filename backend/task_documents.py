# pyright: strict
"""Task documents are pages linked to the task with their document name as role.

A role may name several pages, and a shared page keeps its versions across tasks and writers. The task's read lists
all readable document links, newest change first, alongside the version that task last approved.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, cast

from clients import bot_files as BF

from .store import H, Problem

# The names a stage approves; spelled as here whatever case or dashes a writer uses.
NAMES: tuple[str, ...] = BF.DOCUMENTS
MOST = 20  # documents on one task
VERSIONS_SHOWN = 50  # newest first; a document rewritten more often than this lists its newest


def _key(name: str) -> str:
    return " ".join(re.sub(r"[-_]", " ", name).split()).lower()  # `qa-report` on a command line is `QA report`


def canonical(name: str | None) -> str:
    """The document's name: a fixed name in its own spelling (`qa report` is `QA report`), else `name` tidied, or
    Problem."""
    said = " ".join(str(name or "").split())
    found = next((n for n in NAMES if n.lower() == _key(said)), None)
    if found:
        return found
    if not said or len(said) > 40 or not any(ch.isalnum() for ch in said) or any(ord(ch) < 32 for ch in said):
        raise Problem("validation", "A document's name is 1 to 40 characters, such as Spec or QA report", 422)
    return said


def named(c: Any, task_id: str, name: str) -> str:
    """`name` as this task already spells it (`release notes` is its `Release notes`); a new name while the task has
    room for one more document, or Problem."""
    names: list[str] = [
        r["role"]
        for r in c.execute(
            "SELECT DISTINCT role FROM page_links WHERE target=? AND role NOT IN ('writer','related')",
            ("task:" + task_id,),
        )
    ]
    same = next((n for n in names if _key(n) == _key(name)), None)
    if same:
        return same
    if len(names) >= MOST:
        raise Problem("validation", f"A task has at most {MOST} documents: write a new version of one", 422)
    return name


def file_name(name: str) -> str:
    """The name its versions carry in the task's files: `qa-report.html`."""
    return (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "document") + ".html"


def clean(text: str | None) -> bytes:
    """The HTML a version stores: cleaned as a page is, so nothing in it loads or runs from elsewhere."""
    from . import visuals

    if text is None or not text.strip():
        raise Problem("validation", "A document is HTML text: send it as text", 422)
    cleaner = cast(Callable[..., tuple[bytes, list[Any]]], visuals.clean)
    cleaned, _ = cleaner(text.encode("utf-8"), "page", base=False)
    if len(cleaned) > visuals.LIMITS["page"]:
        raise Problem("file_refused", "A document is at most 10 MB", 422)
    return cleaned


def file_of(c: Any, task_id: str, name: str) -> Any:
    """The page continued by a document write; more than one requires an explicit page id."""
    rows = c.execute(
        "SELECT f.* FROM page_links l JOIN bot_files f ON f.id=l.page_id WHERE l.target=? AND l.role=? ORDER BY f.id",
        ("task:" + task_id, name),
    ).fetchall()
    if len(rows) > 1:
        raise Problem(
            "ambiguous",
            "Several pages have this document role; publish with page",
            409,
            extra={"pages": [r["id"] for r in rows]},
        )
    row = rows[0] if rows else None
    if row and row["archived"]:
        c.execute("UPDATE bot_files SET archived=0 WHERE id=?", (row["id"],))
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (row["id"],)).fetchone()
    return row


def record(c: Any, task_id: str, name: str, file_id: str, by: str | None = None) -> None:
    """Make a task document a page, linking its task and writer without changing any other links."""
    from . import page_links as L

    now = H.now()
    c.execute(
        "INSERT INTO pages(file_id,cadence,created_at,updated_at) VALUES(?,'none',?,?) "
        "ON CONFLICT(file_id) DO UPDATE SET updated_at=excluded.updated_at,archived=0",
        (file_id, now, now),
    )
    if by is None:
        row = c.execute("SELECT bot_id FROM bot_files WHERE id=?", (file_id,)).fetchone()
        by = f"bot:{row['bot_id']}"
    L.link(c, file_id, "task:" + task_id, name, by)
    version = c.execute(
        "SELECT actor FROM bot_file_versions WHERE file_id=? ORDER BY version DESC LIMIT 1", (file_id,)
    ).fetchone()
    if version and str(version["actor"]).startswith(("bot:", "human:")):
        L.link(c, file_id, version["actor"], "writer", by)


def approved_versions(c: Any, task_id: str) -> dict[str, int]:
    """The last OK'd version of every page on this task, including approvals from earlier visits."""
    from . import task_pages as P

    out: dict[str, int] = {}
    answers = c.execute(
        "SELECT a.* FROM visits v JOIN answers a ON a.visit_id=v.id "
        "WHERE v.task_id=? AND a.answer='ok' AND a.undone_at IS NULL ORDER BY a.created_at DESC,a.rowid DESC",
        (task_id,),
    ).fetchall()
    for answer in answers:
        for file_id, version in P.answer_pages(c, task_id, dict(answer)).items():
            out.setdefault(file_id, version)
    return out


def listing(c: Any, files: Any, who: Any, task_id: str) -> list[dict[str, Any]]:
    """The task's documents `who` may open, newest change first, each with its versions newest first."""
    from . import task_privacy as privacy
    from . import page_links as L
    from . import files as file_store

    actor_of = cast(Callable[[Any], Any], privacy.actor)
    review = cast(Callable[..., dict[str, Any]], file_store.version_review)
    out: list[dict[str, Any]] = []
    rows: list[Any] = c.execute(
        "SELECT l.role AS name, f.* FROM page_links l JOIN bot_files f ON f.id=l.page_id "
        "JOIN pages p ON p.file_id=f.id WHERE l.target=? AND l.role NOT IN ('writer','related') "
        "AND f.archived=0 AND p.archived=0 AND p.deleted_at IS NULL AND f.current_version>0 "
        "ORDER BY f.last_activity_at DESC, l.role, f.id",
        ("task:" + task_id,),
    ).fetchall()
    actor = actor_of(who)
    approved = approved_versions(c, task_id)
    for row in rows:
        if not files.visible(c, who, row, {}):
            continue
        versions: list[dict[str, Any]] = [
            {
                "n": v["version"],
                "by": v["actor"],
                "at": v["created_at"],
                "note": review(c, row["id"], v["version"], actor=actor)["note"],
            }
            for v in c.execute(
                "SELECT version, actor, created_at FROM bot_file_versions WHERE file_id=? ORDER BY version DESC LIMIT ?",
                (row["id"], VERSIONS_SHOWN),
            )
        ]
        if not versions:
            continue
        out.append(
            {
                "name": row["name"],
                "file_id": row["id"],
                "page_id": row["id"],
                "links": L.links_of(c, files.auth, who, row["id"]),
                "changed_since_ok": row["id"] in approved and approved[row["id"]] < versions[0]["n"],
                **({"approved_version": approved[row["id"]]} if row["id"] in approved else {}),
                "version": versions[0]["n"],
                "by": versions[0]["by"],
                "at": versions[0]["at"],
                "versions": versions,
            }
        )
    return out
