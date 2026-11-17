"""A task's documents: Spec, QA report (which holds the proof), Release report, Invoice and Email, the names a stage
approves, and any other a bot names (Release notes, Research). A task has at most one of each name, and each is a task file (backend/files.py `attach_task`) whose versions are the document's: who wrote each
one, when, its note (`task_file_reviews`) and its HTML, cleaned as a page is (backend/visuals.py) so a reader opens it
sandboxed like any page. `task_documents` only names which task file is which document.

A bot writes one with `tico task attach <id> spec.html --doc Spec` (the `document` field of POST
/api/v2/tasks/{id}/files). The task's read lists them newest change first (`listing`); the task window shows a tab per
document beside Chat, and none at all when there are none (ui/app/task-docs.ts)."""

from __future__ import annotations

import re

from clients import bot_files as BF

from .store import H, Problem

NAMES = BF.DOCUMENTS  # the names a stage approves; spelled as here whatever case or dashes a writer uses
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


def named(c, task_id: str, name: str) -> str:
    """`name` as this task already spells it (`release notes` is its `Release notes`); a new name while the task has
    room for one more document, or Problem."""
    names = [r["name"] for r in c.execute("SELECT name FROM task_documents WHERE task_id=?", (task_id,))]
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
    cleaned, _ = visuals.clean(text.encode("utf-8"), "page", base=False)
    if len(cleaned) > visuals.LIMITS["page"]:
        raise Problem("file_refused", "A document is at most 10 MB", 422)
    return cleaned


def file_of(c, task_id: str, name: str):
    """The task file that is this task's document `name`, or None before its first version."""
    row = c.execute(
        "SELECT f.* FROM task_documents d JOIN bot_files f ON f.id=d.file_id WHERE d.task_id=? AND d.name=?",
        (task_id, name),
    ).fetchone()
    if row and row["archived"]:
        c.execute("UPDATE bot_files SET archived=0 WHERE id=?", (row["id"],))  # written again, it is back
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (row["id"],)).fetchone()
    return row


def record(c, task_id: str, name: str, file_id: str) -> None:
    now = H.now()
    c.execute(
        "INSERT INTO task_documents(id,task_id,name,file_id,created_at,updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(task_id,name) DO UPDATE SET file_id=excluded.file_id,updated_at=excluded.updated_at",
        (H.new_id(), task_id, name, file_id, now, now),
    )


def listing(c, files, who, task_id: str) -> list[dict]:
    """The task's documents `who` may open, newest change first, each with its versions newest first."""
    from . import task_privacy as privacy
    from .files import version_review

    out = []
    rows = c.execute(
        "SELECT d.name, f.* FROM task_documents d JOIN bot_files f ON f.id=d.file_id "
        "WHERE d.task_id=? AND f.archived=0 AND f.current_version>0 ORDER BY d.updated_at DESC, d.name",
        (task_id,),
    ).fetchall()
    actor = privacy.actor(who)
    for row in rows:
        if not files.visible(c, who, row, {}):
            continue
        versions = [
            {
                "n": v["version"],
                "by": v["actor"],
                "at": v["created_at"],
                "note": version_review(c, row["id"], v["version"], actor=actor)["note"],
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
                "version": versions[0]["n"],
                "by": versions[0]["by"],
                "at": versions[0]["at"],
                "versions": versions,
            }
        )
    return out
