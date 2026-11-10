# pyright: strict
"""Pages attached to a page requirement (docs/pipelines-v3.md "The nine requirement kinds", 3; spec S3, S4).

The owner attaches a page (a Tico file, `page:<file id>`, at a version) to one page requirement of the task's open visit:
`tico task attach <task> page:<file id>[@<version>] --req "<requirement>"`, the MCP tool `hub_task_attach` with `req`,
or `POST /api/v2/tasks/{tid}/pages` {requirement, page, version}. With no version, the file's newest one is pinned.

- One page counts per requirement and visit: attaching again replaces the last one (`replaced_at`). A new version is a
  new attach, and it starts the verdict and any OK over.
- A requirement `at_head` keeps `heads` (task_facts.heads) as they were when the page was attached.
- Attaching owes Jev a verdict (page_verdicts.owe) and counts as progress (C4).

Every function takes the caller's open write transaction and never commits.
"""

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

REF = re.compile(r"^page:([^@\s]+)(?:@([1-9][0-9]*))?$")


def parse_ref(text: str) -> tuple[str, int | None]:
    """`page:<file id>` or `page:<file id>@<version>` as (file id, version or None). Raises ValueError for anything
    else, with a sentence that says the shape."""
    found = REF.match(str(text or "").strip())
    if not found:
        raise ValueError("A page is page:<file id> or page:<file id>@<version>")
    return found.group(1), int(found.group(2)) if found.group(2) else None


def _one(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> Row | None:
    cursor = conn.execute(sql, args)
    found = cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description], found, strict=True)) if found else None


def current(conn: "sqlite3.Connection", visit_id: str, requirement_id: str) -> Row | None:
    """The page that counts for a requirement on a visit (a `requirement_pages` row), or None when none is attached."""
    return _one(
        conn,
        "SELECT * FROM requirement_pages WHERE visit_id=? AND requirement_id=? AND replaced_at IS NULL",
        (visit_id, requirement_id),
    )


def newest_for_task(conn: "sqlite3.Connection", task_id: str, stage_id: str) -> Row | None:
    """The page that counted on the task's newest visit to a stage that has one: what the Product task's Spec stage
    had when it moved on, for the Engineering task it files (D10)."""
    return _one(
        conn,
        "SELECT p.* FROM requirement_pages p JOIN visits v ON v.id = p.visit_id "
        "WHERE v.task_id=? AND v.stage_id=? AND p.replaced_at IS NULL ORDER BY v.entered_at DESC, p.created_at DESC "
        "LIMIT 1",
        (task_id, stage_id),
    )


def attach(
    conn: "sqlite3.Connection",
    task_id: str,
    requirement: str,
    ref: str,
    by: str,
    *,
    seen_version: int | None = None,
) -> Row:
    """Attach a page to a page requirement of the task's open visit. `requirement` is its id or its name (`name_of`),
    `ref` a `parse_ref` page. Who may: the task's owner, whoever holds it now, or any person. Refuses
    `task_requirement` (no such page requirement on this visit, not a page, a file the task's readers can't open, no
    such version), `task_changed` (a bot's stale or missing `seen_version`), `task_ended`. Returns the
    `requirement_pages` row. Track B."""
    raise NotImplementedError("task_pages.attach: track B")
