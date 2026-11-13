# pyright: strict
"""Pages attached to a page requirement (docs/pipelines-v3.md "The nine requirement kinds", 3; spec S3, S4).

The owner attaches a page (a Tico file, `page:<file id>`, at a version) to one page requirement of the task's open visit:
`tico task attach <task> page:<file id>[@<version>] --req "<requirement>"`, the MCP tool `tico_task_attach` with `req`,
or `POST /api/v2/tasks/{tid}/pages` {requirement, page, version}. With no version, the file's newest one is pinned.

- One page counts per requirement and visit: attaching again replaces the last one (`replaced_at`). A new version is a
  new attach, and it starts the verdict and any OK over.
- A requirement `at_head` keeps the linked items' versions (`item_versions`: item_sources.github.heads) as they were
  when the page was attached.
- Attaching owes Jev a verdict (page_verdicts.owe) and counts as progress (C4).

Every function takes the caller's open write transaction and never commits.
"""

import json
import re
from typing import TYPE_CHECKING, Any, NoReturn

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


def _refuse(conn: "sqlite3.Connection", by: str, rule: str, detail: str, task_id: str | None = None) -> NoReturn:
    from . import hubdb

    H: Any = hubdb
    H.refuse(conn, by, rule, detail, task=task_id)
    raise AssertionError("refuse() always raises")  # pragma: no cover


# What a page for an OK is refused with when it is not HTML: what to do instead, in a few words the bot can act on.
NOT_HTML = (
    "A page for an OK must be HTML, and this version is not (it reads as Markdown or plain text). Write it as HTML: "
    "two or three lines at the top saying what it is and what to decide, then the sections, with simple inline CSS and "
    "screenshots as images. A Spec page: skills/build-the-spec-page. A QA page: skills/build-the-qa-page. "
    "Then tico page publish the .html and attach the new version."
)
HEAD_BYTES = 4096  # enough to get past a byte-order mark, blank lines and a long comment-free preamble


def is_html(text: str) -> bool:
    """Whether a page's text is HTML: after a byte-order mark and whitespace it opens with a tag (`<!doctype`,
    `<html>`, any element). The decide view draws text that is not HTML as Markdown; this is the same test."""
    return text.lstrip("\ufeff \t\r\n\f\v").startswith("<")


def _version_is_html(conn: "sqlite3.Connection", file_id: str, version: int) -> bool:
    """Whether a page version's bytes open as HTML. True when they cannot be read here (no blob store on this
    connection, a blob that will not open): the attach is not where an unreadable page is caught, Jev's check is."""
    found = _one(conn, "SELECT digest FROM bot_file_versions WHERE file_id=? AND version=?", (file_id, version))
    blobs = getattr(conn, "blobs", None)
    if not found or not found.get("digest") or blobs is None:
        return True
    try:
        head = b"".join(blobs.open_range(found["digest"], 0, HEAD_BYTES - 1))
    except Exception:  # noqa: BLE001 - an unreadable blob is Jev's to report, never a refusal here
        return True
    return is_html(head.decode("utf-8", errors="replace"))


def requirement_of(conn: "sqlite3.Connection", row: Row, requirement: str) -> Row | None:
    """The page requirement of the task's open visit that `requirement` names: its id, or its name (`name_of`, any
    case). None when there is no such page requirement."""
    from . import task_requirements as R

    wanted = str(requirement or "").strip()
    pages = [r for r in R.for_visit(conn, row["visit"]) if r["kind"] == "page"]
    for found in pages:
        if found["id"] == wanted:
            return found
    named = [r for r in pages if R.name_of(r).casefold() == wanted.casefold()]
    return named[0] if len(named) == 1 else None


def release_tags(conn: "sqlite3.Connection", task_id: str) -> list[str]:
    """The release each task this task filed went out in: for every merged PR of a filed task, the oldest saved release
    whose history holds its merge commit (item_sources.github.included). Sorted, each once. What a Validate page is checked
    against (`with_release`)."""
    cursor = conn.execute(
        "SELECT r.tag, MIN(r.published_at) FROM links l "
        "JOIN task_links tl ON tl.task_id = l.to_task_id AND tl.source = 'github' AND tl.item_type = 'pr' "
        "JOIN item_metadata f ON f.source = tl.source AND f.item_type = tl.item_type AND f.item_key = tl.item_key "
        "JOIN github_release_commits rc ON rc.commit_sha = json_extract(f.fields, '$.merged_sha') AND rc.included = 1 "
        "JOIN github_releases r ON r.id = rc.release_id WHERE l.from_task_id = ? "
        "GROUP BY json_extract(f.fields, '$.merged_sha')",
        (task_id,),
    )
    return sorted({str(found[0]) for found in cursor.fetchall()})


def judged_against(conn: "sqlite3.Connection", row: Row, requirement: Row) -> tuple[str, Row]:
    """What a page attached now is judged against: the rubric (the requirement's `rubric`, else its line, else its
    kind's name) and the context Jev is shown beside it: `task` (Jev is shown this task's own title and spec, so the
    same page on another task is another verdict), `heads` (the newest commit of each PR the task links, so a page that
    says the checks pass at the newest commit can be checked; at_head keeps every linked PR, one not read yet as None,
    and `attach` waits for it; any other page gets only the commits Tico has read, so a PR not read yet never holds a
    page up), `release` (with_release)."""
    from .item_sources import github as F
    from . import task_requirements as R

    settings: Row = requirement.get("settings") or {}
    rubric = str(settings.get("rubric") or "").strip() or R.name_of(requirement)
    context: Row = {"task": row["id"]}
    heads = F.heads(conn, row["id"])
    if not settings.get("at_head"):
        heads = {ref_: sha for ref_, sha in heads.items() if sha}
    if heads or settings.get("at_head"):
        context["heads"] = heads
    if settings.get("with_release"):
        context["release"] = release_tags(conn, row["id"])
    return rubric, context


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
    `task_requirement` (no such page requirement on this visit, not a page, no such version, a PR not read yet),
    `task_changed` (a bot's stale or missing `seen_version`), `task_ended`, and `lint` (a version that is not HTML,
    `NOT_HTML`: a page for an OK is drawn as HTML). The route checks the caller may open the
    file. Returns the `requirement_pages` row."""
    from . import hubdb
    from . import hubdb_core
    from . import page_verdicts as V
    from . import task_engine as E
    from . import waiting_on as W

    H: Any = hubdb
    core: Any = hubdb_core
    row = E.task(conn, task_id)
    if not row:
        _refuse(conn, by, "not-found", "No such task")
    if row["status"] != "open":
        _refuse(conn, by, "task_ended", f"The task is {row['stage']['name']}. Reopen it first.", task_id)
    E.check_version(conn, by, row, seen_version)
    if by not in (row.get("owner_id"), W.holder(conn, row)) and not core.is_human(by):
        _refuse(
            conn, by, "identity", "Only the task's owner, whoever has it now, or a person attaches its page", task_id
        )
    found = requirement_of(conn, row, requirement)
    if found is None:
        _refuse(conn, by, "task_requirement", f'This stage has no page requirement "{requirement}"', task_id)
    try:
        file_id, version = parse_ref(ref)
    except ValueError as exc:
        _refuse(conn, by, "task_requirement", str(exc), task_id)
    file = _one(conn, "SELECT id, current_version, archived FROM bot_files WHERE id=?", (file_id,))
    if not file or file["archived"]:
        _refuse(conn, by, "task_requirement", "No such page", task_id)
    version = version or int(file["current_version"] or 0)
    if not _one(conn, "SELECT 1 AS one FROM bot_file_versions WHERE file_id=? AND version=?", (file_id, version)):
        _refuse(conn, by, "task_requirement", f"That page has no version {version}", task_id)
    if not _version_is_html(conn, file_id, version):
        # A writing correction, as `lint` is everywhere: fixed by publishing HTML, never counted toward quarantine.
        _refuse(conn, by, "lint", NOT_HTML, task_id)
    rubric, context = judged_against(conn, row, found)
    heads: dict[str, str | None] = context.get("heads") or {}
    unread = sorted(ref_ for ref_, sha in heads.items() if not sha)
    if unread:
        _refuse(
            conn, by, "task_requirement", f"Tico is still reading {unread[0]} from GitHub. Try again soon.", task_id
        )
    key = V.rubric_hash(rubric, context)
    visit_id = row["visit"]["id"]
    was = current(conn, visit_id, found["id"])
    if was and (was["file_id"], int(was["version"]), was.get("rubric_hash")) == (file_id, version, key):
        return was
    now = H.now()
    if was:
        conn.execute("UPDATE requirement_pages SET replaced_at=? WHERE id=?", (now, was["id"]))
    page_id = H.new_id()
    conn.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "rubric_hash, judged_against, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            page_id,
            visit_id,
            found["id"],
            file_id,
            version,
            json.dumps(context.get("heads") or {}),
            by,
            key,
            json.dumps({"rubric": rubric, "context": context}),
            now,
        ),
    )
    V.owe(conn, file_id, version, key)
    E._event(  # pyright: ignore[reportPrivateUsage]
        conn,
        task_id,
        by,
        "page_attached",
        f"page:{was['file_id']}@{was['version']}" if was else None,
        f"page:{file_id}@{version}",
        None,
        {"requirement": found["id"], "file_id": file_id, "version": version},
    )
    if E.advance(conn, task_id) is None:
        E.changed(conn, task_id, by)
    made = _one(conn, "SELECT * FROM requirement_pages WHERE id=?", (page_id,))
    assert made is not None
    return made
