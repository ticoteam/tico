# pyright: strict
"""A task's documents: Spec, QA report (which holds the proof), Release report and Invoice, the names a stage
approves (task_requirements.APPROVED), and any other a bot names (Email, Release notes, Research). A task has at most one of each name, and each is a task file (backend/files.py `attach_task`) whose versions are the document's: who wrote each
one, when, its note (`task_file_reviews`) and its HTML, cleaned as a page is (backend/visuals.py) so a reader opens it
sandboxed like any page. `task_documents` only names which task file is which document.

A bot writes one with `tico task attach <id> spec.html --doc Spec` (the `document` field of POST
/api/v2/tasks/{id}/files). The task's read lists them newest change first (`listing`), with the task's Markdown files
beside them (`kind` markdown: a bot's write-up as a .md file, attached or published from its reports/ folder, is as much
a document as an HTML one); the task window shows a tab per document beside Chat, and none at all when there are none
(ui/app/task-docs.ts)."""

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
    names: list[str] = [r["name"] for r in c.execute("SELECT name FROM task_documents WHERE task_id=?", (task_id,))]
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
    """The task file that is this task's document `name`, or None before its first version."""
    row: Any = c.execute(
        "SELECT f.* FROM task_documents d JOIN bot_files f ON f.id=d.file_id WHERE d.task_id=? AND d.name=?",
        (task_id, name),
    ).fetchone()
    if row and row["archived"]:
        c.execute("UPDATE bot_files SET archived=0 WHERE id=?", (row["id"],))  # written again, it is back
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (row["id"],)).fetchone()
    return row


def record(c: Any, task_id: str, name: str, file_id: str) -> None:
    now = H.now()
    c.execute(
        "INSERT INTO task_documents(id,task_id,name,file_id,created_at,updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(task_id,name) DO UPDATE SET file_id=excluded.file_id,updated_at=excluded.updated_at",
        (H.new_id(), task_id, name, file_id, now, now),
    )


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
    """The task's documents `who` may open, newest change first, each with its versions newest first: its HTML
    documents, then its Markdown files (each content once, as the task's files list them), MOST in all."""
    from . import task_privacy as privacy
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
        "SELECT d.name, f.* FROM task_documents d JOIN bot_files f ON f.id=d.file_id "
        "WHERE d.task_id=? AND f.archived=0 AND f.current_version>0 ORDER BY d.updated_at DESC, d.name",
        (task_id,),
    ).fetchall()
    for row in rows:
        if files.visible(c, who, row, {}) and (found := entry(row["name"], row["id"], "html")):
            out.append(found)
    # The task's Markdown files: every other file it holds now (bot_files_task), its current version's name and bytes.
    held: list[Any] = c.execute(
        "SELECT f.*, v.name AS shown, v.mime AS shown_mime, v.digest AS shown_digest FROM bot_files f "
        "JOIN bot_file_versions v ON v.file_id=f.id AND v.version=f.current_version "
        "WHERE f.task_id=? AND f.archived=0 AND f.locator='tico_blob' AND f.current_version>0 "
        "AND NOT EXISTS (SELECT 1 FROM task_documents d WHERE d.file_id=f.id) ORDER BY f.last_activity_at DESC",
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
