# pyright: strict
"""Task documents are pages linked to the task with their document name as role.

A role may name several pages, and a shared page keeps its versions across tasks and writers. The task's read lists
all readable document links with the version that task last approved, plus its Markdown files, newest change first.
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
    if said.lower() in ("writer", "related"):
        raise Problem("validation", "Writer and related are link roles, not document names", 422)
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


def approved_documents(c: Any, task_id: str) -> dict[tuple[str, str], int]:
    """The task's last OK for each (page, document role), including earlier visits."""
    from . import task_pages as P

    out: dict[tuple[str, str], int] = {}
    answers = c.execute(
        "SELECT a.*,json_extract(r.settings,'$.document') AS document FROM visits v "
        "JOIN answers a ON a.visit_id=v.id JOIN requirements r ON r.id=a.requirement_id "
        "WHERE v.task_id=? AND a.answer='ok' AND a.undone_at IS NULL ORDER BY a.created_at DESC,a.rowid DESC",
        (task_id,),
    ).fetchall()
    for answer in answers:
        if not answer["document"]:
            continue
        for file_id, version in P.answer_pages(c, task_id, dict(answer)).items():
            out.setdefault((file_id, str(answer["document"])), version)
    return out


def approved_versions(c: Any, task_id: str, role: str | None = None) -> dict[str, int]:
    """The last approved page versions on a task, optionally for one document role."""
    out: dict[str, int] = {}
    for (file_id, document), version in approved_documents(c, task_id).items():
        if role is None or document == role:
            out.setdefault(file_id, version)
    return out


def approved_tasks(c: Any, page_id: str, role: str | None = None) -> dict[str, int]:
    """Task id -> approved version for a page's document links. Pass the link's role for its own OK mark.

    The map is neutral data; callers still filter task links through their reader's permissions.
    """
    rows = c.execute(
        "SELECT substr(target,6) AS task_id,role FROM page_links WHERE page_id=? "
        "AND target LIKE 'task:%' AND role NOT IN ('writer','related')",
        (page_id,),
    ).fetchall()
    out: dict[str, int] = {}
    for row in rows:
        document = str(row["role"])
        if role is not None and document != role:
            continue
        task_id = str(row["task_id"])
        version = approved_versions(c, task_id, document).get(page_id)
        if version is not None:
            out[task_id] = max(out.get(task_id, 0), version)
    return out


def markdown_name(file_name: str) -> str:
    """A Markdown file's tab: its name without the extension, and without the date a dated copy starts with
    (`2026-11-17-how-ui-works-today.md` is `How ui works today`)."""
    stem = re.sub(r"\.(md|markdown)$", "", file_name.strip(), flags=re.IGNORECASE)
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[-_ ]+", "", stem)
    said = " ".join((stem if " " in stem else re.sub(r"[-_]+", " ", stem)).split())
    return (said[:1].upper() + said[1:])[:80] or file_name


def _is_markdown(name: str, mime: str) -> bool:
    return mime == "text/markdown" or name.lower().endswith((".md", ".markdown"))


def listing(c: Any, files: Any, who: Any, task_id: str) -> list[dict[str, Any]]:
    """The task's readable page documents and Markdown files, each with its versions newest first."""
    from . import task_privacy as privacy
    from . import page_links as L
    from . import files as file_store

    actor_of = cast(Callable[[Any], Any], privacy.actor)
    review = cast(Callable[..., dict[str, Any]], file_store.version_review)
    once = cast(Callable[[list[Any]], list[Any]], file_store.once_per_content)
    actor = actor_of(who)

    def versions_of(file_id: str) -> list[dict[str, Any]]:
        return [
            {
                "n": v["version"],
                "by": v["actor"],
                "at": v["created_at"],
                "note": review(c, file_id, v["version"], actor=actor)["note"],
            }
            for v in c.execute(
                "SELECT version, actor, created_at FROM bot_file_versions WHERE file_id=? ORDER BY version DESC LIMIT ?",
                (file_id, VERSIONS_SHOWN),
            )
        ]

    def entry(name: str, file_id: str, kind: str) -> dict[str, Any] | None:
        versions = versions_of(file_id)
        if not versions:
            return None
        return {
            "name": name,
            "file_id": file_id,
            "kind": kind,
            "version": versions[0]["n"],
            "by": versions[0]["by"],
            "at": versions[0]["at"],
            "versions": versions,
        }

    out: list[dict[str, Any]] = []
    rows: list[Any] = c.execute(
        "SELECT l.role AS name, f.* FROM page_links l JOIN bot_files f ON f.id=l.page_id "
        "JOIN pages p ON p.file_id=f.id WHERE l.target=? AND l.role NOT IN ('writer','related') "
        "AND f.archived=0 AND p.archived=0 AND p.deleted_at IS NULL AND f.current_version>0 "
        "ORDER BY f.last_activity_at DESC, l.role, f.id",
        ("task:" + task_id,),
    ).fetchall()
    approved = approved_documents(c, task_id)
    L.prime(c, rows)
    for row in rows:
        if files.visible(c, who, row, {}) and (found := entry(row["name"], row["id"], "html")):
            approved_version = approved.get((row["id"], row["name"]))
            found.update(
                page_id=row["id"],
                title=row["title"],
                links=L.links_of(c, files.auth, who, row["id"]),
                changed_since_ok=approved_version is not None and approved_version < found["version"],
            )
            if approved_version is not None:
                found["approved_version"] = approved_version
            out.append(found)
    # The task's Markdown files: every other file it holds now (bot_files_task), its current version's name and bytes.
    held: list[Any] = c.execute(
        "SELECT f.*, v.name AS shown, v.mime AS shown_mime, v.digest AS shown_digest FROM bot_files f "
        "JOIN bot_file_versions v ON v.file_id=f.id AND v.version=f.current_version "
        "WHERE f.task_id=? AND f.archived=0 AND f.locator='tico_blob' AND f.current_version>0 "
        "AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.file_id=f.id) ORDER BY f.last_activity_at DESC",
        (task_id,),
    ).fetchall()
    shown = once(
        [
            (row, row["shown_digest"], row)
            for row in held
            if _is_markdown(str(row["shown"]), str(row["shown_mime"])) and files.visible(c, who, row, {})
        ]
    )
    taken = {_key(d["name"]) for d in out}
    # Two of the same name are one tab: an HTML document's, else the file attached by name (a dated copy gives way).
    for row in sorted(shown, key=lambda r: not str(r["identity"]).startswith("task-file:")):
        name = markdown_name(str(row["shown"]))
        if len(out) >= MOST or _key(name) in taken:
            continue
        if found := entry(name, row["id"], "markdown"):
            taken.add(_key(name))
            out.append(found)
    out.sort(key=lambda d: str(d["at"]), reverse=True)
    return out
