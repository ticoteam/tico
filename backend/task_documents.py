# pyright: strict
"""Task documents are pages linked to the task with their document name as role.

A role may name several pages, and a shared page keeps its versions across tasks and writers. The task's read lists
all readable document links with the version that task last approved, plus its Markdown files, newest change first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from itertools import islice
from typing import Any, cast

from clients import bot_files as BF

from . import read_cache
from .store import H, Problem
from .slugs import slugify

# The names a stage approves; spelled as here whatever case or dashes a writer uses.
NAMES: tuple[str, ...] = BF.DOCUMENTS
MOST = 20  # documents on one task
LIST_MAX = 50
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
    return slugify(name, fallback="document") + ".html"


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


def file_of(c: Any, task_id: str, name: str, visible: Callable[[Any], bool]) -> Any:
    """The page continued by a document write; more than one requires an explicit page id."""
    candidates = c.execute(
        "SELECT f.* FROM page_links l JOIN bot_files f ON f.id=l.page_id "
        "WHERE l.target=? AND l.role=? AND f.state<>'deleted' ORDER BY f.id",
        ("task:" + task_id, name),
    ).fetchall()
    rows = [row for row in candidates if visible(row)]
    if len(rows) > 1:
        raise Problem(
            "ambiguous",
            "Several pages have this document role; publish with page",
            409,
            extra={"pages": [r["id"] for r in rows]},
        )
    row = rows[0] if rows else None
    if row and row["archived"]:
        from .files import list_again

        c.execute("UPDATE bot_files SET archived=0 WHERE id=?", (row["id"],))
        list_again(c, row["id"])
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (row["id"],)).fetchone()
    return row


def record(c: Any, task_id: str, name: str, file_id: str, by: str | None = None) -> None:
    """Make a task document a page, linking its task and writer without changing any other links."""
    from . import page_links as L

    now = H.now()
    was = c.execute("SELECT archived FROM pages WHERE file_id=?", (file_id,)).fetchone()
    c.execute(
        "INSERT INTO pages(file_id,cadence,created_at,updated_at) VALUES(?,'none',?,?) "
        "ON CONFLICT(file_id) DO UPDATE SET updated_at=excluded.updated_at,archived=0",
        (file_id, now, now),
    )
    if was and was[0]:
        from .files import list_again

        list_again(c, file_id)  # back from its page's archive on each task it is on
    if by is None:
        row = c.execute("SELECT bot_id FROM bot_files WHERE id=?", (file_id,)).fetchone()
        by = f"bot:{row['bot_id']}"
    L.link(c, file_id, "task:" + task_id, name, by)
    version = c.execute(
        "SELECT actor FROM bot_file_versions WHERE file_id=? ORDER BY version DESC LIMIT 1", (file_id,)
    ).fetchone()
    if version and str(version["actor"]).startswith(("bot:", "human:")):
        L.link(c, file_id, version["actor"], "writer", by)


DocumentApprovals = dict[tuple[str, str], int]

# Latest OK first. The event pins all submitted versions. Older answers only stored the version number on the answer.
# Driving the join from the requested tasks keeps unrelated answers out of the read.
APPROVALS_SQL = """
WITH history AS MATERIALIZED (
    SELECT subject_id, json_extract(data,'$.answer_id') AS answer_id, json_extract(data,'$.pages') AS pages
    FROM history_events WHERE subject_type='task' AND subject_id IN ({marks}) AND kind='answer'
)
SELECT v.task_id,json_extract(r.settings,'$.document') AS document,h.pages,
    CASE WHEN h.pages IS NULL THEN (
        SELECT json_object('file_id',p.file_id,'version',p.version) FROM requirement_pages p
        WHERE p.visit_id=a.visit_id AND p.requirement_id=a.requirement_id
          AND p.version=a.page_version AND p.created_at<=a.created_at
        ORDER BY p.created_at DESC,p.rowid DESC LIMIT 1
    ) END AS legacy_page
FROM visits v INDEXED BY visits_task
CROSS JOIN answers a INDEXED BY answers_visit ON a.visit_id=v.id
JOIN requirements r ON r.id=a.requirement_id
LEFT JOIN history h ON h.subject_id=v.task_id AND h.answer_id=a.id
WHERE v.task_id IN ({marks}) AND a.answer='ok' AND a.undone_at IS NULL
  AND json_extract(r.settings,'$.document') IS NOT NULL
ORDER BY a.created_at DESC,a.rowid DESC
"""


def _read_approvals(c: Any, task_ids: list[str]) -> dict[str, DocumentApprovals]:
    marks = ",".join("?" * len(task_ids))
    out: dict[str, DocumentApprovals] = {task_id: {} for task_id in task_ids}
    for answer in c.execute(APPROVALS_SQL.format(marks=marks), (*task_ids, *task_ids)):
        pages: list[dict[str, Any]] = json.loads(answer["pages"]) if answer["pages"] is not None else []
        if answer["legacy_page"]:
            pages = [json.loads(answer["legacy_page"])]
        for page in pages:
            out[answer["task_id"]].setdefault((page["file_id"], answer["document"]), page["version"])
    return out


def prime_approvals(c: Any, task_ids: set[str]) -> None:
    """Read neutral approval history for all tasks in a page list's snapshot."""
    snapshot = read_cache.cache(c)
    if snapshot is None:
        return
    for chunk in H.chunks(sorted(t for t in task_ids if ("document_approvals", t) not in snapshot)):
        for task_id, approvals in _read_approvals(c, list(chunk)).items():
            snapshot["document_approvals", task_id] = approvals


def approved_documents(c: Any, task_id: str) -> DocumentApprovals:
    """The task's last OK for each (page, document role), including earlier visits."""
    return read_cache.value(
        c, ("document_approvals", task_id), lambda: _read_approvals(c, [task_id])[task_id], shared=True
    )


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
    from . import page_links as L

    out: dict[str, int] = {}
    for row in L.rows_for(c, page_id):
        target, document = str(row["target"]), str(row["role"])
        if not target.startswith("task:") or document in ("writer", "related") or (role and document != role):
            continue
        task_id = target[5:]
        version = approved_documents(c, task_id).get((page_id, document))
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


TASK_DOCUMENTS_SQL = (
    "SELECT l.role AS name, f.* FROM page_links l JOIN bot_files f ON f.id=l.page_id "
    "JOIN pages p ON p.file_id=f.id WHERE l.target=? AND l.role NOT IN ('writer','related') "
    "AND f.archived=0 AND f.state<>'deleted' AND p.archived=0 AND p.deleted_at IS NULL AND f.current_version>0 "
    "ORDER BY f.last_activity_at DESC, l.role, f.id"
)
TASK_MARKDOWN_SQL = (
    "SELECT f.*,v.name AS shown,v.digest AS shown_digest FROM bot_files f "
    "JOIN bot_file_versions v ON v.file_id=f.id AND v.version=f.current_version "
    "WHERE f.task_id=? AND f.archived=0 AND f.state<>'deleted' AND f.locator='tico_blob' AND f.current_version>0 "
    "AND (v.mime='text/markdown' OR lower(v.name) LIKE '%.md' OR lower(v.name) LIKE '%.markdown') "
    "AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.file_id=f.id) "
    "ORDER BY (f.identity NOT LIKE 'task-file:%'), f.last_activity_at DESC, f.id DESC"
)


def listing(c: Any, files: Any, who: Any, task_id: str) -> list[dict[str, Any]]:
    """The task's readable page documents and Markdown files, each with its newest versions."""
    from . import task_privacy as privacy
    from . import page_links as L
    from . import files as file_store

    actor_of = cast(Callable[[Any], Any], privacy.actor)
    candidates = cast(Callable[..., Iterator[Any]], file_store.visible_rows)
    batch_versions = cast(Callable[..., dict[str, list[dict[str, Any]]]], file_store.task_file_versions)
    html = list(islice(candidates(c, files.auth, who, TASK_DOCUMENTS_SQL, ("task:" + task_id,)), LIST_MAX))
    taken = {_key(str(row["name"])) for row in html}
    digests: set[str] = set()
    markdown: list[tuple[str, Any]] = []
    remaining = max(0, MOST - len(html))
    if remaining:
        for row in candidates(c, files.auth, who, TASK_MARKDOWN_SQL, (task_id,)):
            digest = str(row["shown_digest"])
            if digest in digests:
                continue
            digests.add(digest)
            name = markdown_name(str(row["shown"]))
            if _key(name) not in taken:
                taken.add(_key(name))
                markdown.append((name, row))
                if len(markdown) == remaining:
                    break

    history = batch_versions(
        c, actor_of(who), [row["id"] for row in html] + [row["id"] for _, row in markdown], limit=VERSIONS_SHOWN
    )
    L.prime(c, html)
    approved = approved_documents(c, task_id) if html else {}
    out: list[dict[str, Any]] = []
    for name, row, kind in [(r["name"], r, "html") for r in html] + [(n, r, "markdown") for n, r in markdown]:
        fid = row["id"]
        versions = [
            {"n": v["version"], "by": v["actor"], "at": v["created_at"], "note": v["note"]}
            for v in history.get(fid, [])
        ]
        if not versions:
            continue
        latest = versions[0]
        entry = {
            "name": name,
            "file_id": fid,
            "kind": kind,
            "version": latest["n"],
            "by": latest["by"],
            "at": latest["at"],
            "created": row["first_activity_at"],
            "versions": versions,
        }
        if kind == "html":
            approved_version = approved.get((fid, name))
            entry.update(
                page_id=fid,
                title=row["title"],
                links=L.links_of(c, files.auth, who, fid),
                changed_since_ok=approved_version is not None and approved_version < latest["n"],
            )
            if approved_version is not None:
                entry["approved_version"] = approved_version
        out.append(entry)
    out.sort(key=lambda d: str(d["at"]), reverse=True)
    return out
