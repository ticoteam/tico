# pyright: strict
"""Pages attached to a page requirement (docs/dev/pipelines-v3.md "The nine requirement kinds", 3; spec S3, S4).

The owner attaches a page (a Tico file, `page:<file id>`, at a version) to one page requirement of the task's open visit:
`tico task attach <task> page:<file id>[@<version>] --req "<requirement>"`, the MCP tool `tico_task_attach` with `req`,
or `POST /api/v2/tasks/{tid}/pages` {requirement, page, version}. With no version, the file's newest one is pinned.

- A document requirement counts every page linked with its role; each page keeps its own submitted version.
  Other page requirements keep one page per visit. A new version starts its verdict and any document OK over.
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


# Submission and standing use the same live set. A page can still open from a link after archiving.
LIVE_DOCUMENT = (
    "FROM page_links l JOIN bot_files f ON f.id=l.page_id JOIN pages p ON p.file_id=f.id "
    "WHERE p.archived=0 AND p.deleted_at IS NULL AND f.archived=0 AND f.state<>'deleted' AND f.current_version>0 "
)


def live_documents(conn: "sqlite3.Connection", task_id: str, document: str) -> dict[str, int]:
    return {
        str(file_id): int(version)
        for file_id, version in conn.execute(
            "SELECT f.id,f.current_version " + LIVE_DOCUMENT + "AND l.target=? AND l.role=? ORDER BY f.id",
            ("task:" + task_id, document),
        )
    }


def current_pages(conn: "sqlite3.Connection", visit_id: str, requirement_id: str) -> list[Row]:
    """All submitted pages on a requirement, newest submission first."""
    cursor = conn.execute(
        "SELECT rp.* FROM requirement_pages rp JOIN requirements r ON r.id=rp.requirement_id "
        "JOIN visits v ON v.id=rp.visit_id WHERE rp.visit_id=? AND rp.requirement_id=? AND rp.replaced_at IS NULL "
        "AND (json_extract(r.settings,'$.document') IS NULL OR EXISTS (SELECT 1 "
        + LIVE_DOCUMENT
        + "AND l.page_id=rp.file_id AND l.target='task:'||v.task_id AND l.role=json_extract(r.settings,'$.document'))) "
        "ORDER BY rp.created_at DESC,rp.rowid DESC",
        (visit_id, requirement_id),
    )
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def current(conn: "sqlite3.Connection", visit_id: str, requirement_id: str) -> Row | None:
    """The newest submitted page, for callers displaying the single-page summary."""
    pages = current_pages(conn, visit_id, requirement_id)
    return pages[0] if pages else None


def version_key(pages: list[Row]) -> str:
    """The version key covers the whole submission, so another page changing makes a shown key stale."""
    return ",".join(sorted(f"{p['file_id']}@{p['version']}" for p in pages))


def answer_pages(conn: "sqlite3.Connection", task_id: str, answer: Row) -> dict[str, int]:
    """The exact page versions an answer covered, scoped to its task. Older single-page answers still read."""
    found = conn.execute(
        "SELECT data FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='answer' "
        "AND json_extract(data,'$.answer_id')=? LIMIT 1",
        (task_id, answer["id"]),
    ).fetchone()
    data: Row = json.loads(found[0] or "{}") if found else {}
    if "pages" in data:
        return {str(p["file_id"]): int(p["version"]) for p in data["pages"]}
    old = _one(
        conn,
        "SELECT file_id,version FROM requirement_pages WHERE visit_id=? AND requirement_id=? "
        "AND version=? AND created_at<=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (answer["visit_id"], answer["requirement_id"], answer.get("page_version"), answer["created_at"]),
    )
    return {str(old["file_id"]): int(old["version"])} if old else {}


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


def earlier(conn: "sqlite3.Connection", task_id: str, visit_id: str) -> Row | None:
    """The page that counted last on an earlier visit of the task, any stage: the newest current page of a visit
    other than `visit_id`, with that visit's `stage_id`. What an OK that names nothing to show shows (the QA page at
    OK release, #3784). None when no earlier visit had one."""
    return _one(
        conn,
        "SELECT p.file_id, p.version, p.requirement_id, v.stage_id FROM requirement_pages p "
        "JOIN visits v ON v.id = p.visit_id WHERE v.task_id=? AND v.id<>? AND p.replaced_at IS NULL "
        "ORDER BY v.entered_at DESC, p.created_at DESC, p.id DESC LIMIT 1",
        (task_id, visit_id),
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
    "screenshots as images. Every such page: .claude/skills/build-the-page (a spec, Checked in production, a review, a QA "
    "page). "
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
    """The release the task's work went out in: for every merged PR of the task itself (Product, where one task holds
    its PRs) or of a task it filed (the pair's Product, whose Engineering task holds them), the oldest saved release
    whose history holds its merge commit (item_sources.github.included). Sorted, each once. What a Validate page is
    checked against (`with_release`)."""
    cursor = conn.execute(
        "SELECT r.tag, MIN(r.published_at) FROM task_links tl "
        "JOIN item_metadata f ON f.source = tl.source AND f.item_type = tl.item_type AND f.item_key = tl.item_key "
        "JOIN github_release_commits rc ON rc.commit_sha = json_extract(f.fields, '$.merged_sha') AND rc.included = 1 "
        "JOIN github_releases r ON r.id = rc.release_id WHERE tl.source = 'github' AND tl.item_type = 'pr' "
        "AND (tl.task_id = ?1 OR tl.task_id IN (SELECT l.to_task_id FROM links l WHERE l.from_task_id = ?1)) "
        "GROUP BY json_extract(f.fields, '$.merged_sha')",
        (task_id,),
    )
    return sorted({str(found[0]) for found in cursor.fetchall()})


def judged_against(conn: "sqlite3.Connection", row: Row, requirement: Row) -> tuple[str, Row, list[str]]:
    """What a page attached now is judged against: the rubric (the requirement's `rubric`, else its line, else its
    kind's name), the context Jev is shown beside it: `task` (Jev is shown this task's own title and spec, so the
    same page on another task is another verdict), `heads` (the newest commit of each PR the task links, so a page that
    says the checks pass at the newest commit can be checked; at_head keeps every linked PR, one not read yet as None,
    and `attach` waits for it; any other page gets only the commits Tico has read, so a PR not read yet never holds a
    page up), `release` (with_release); and no added rules: Flow's suggested rules were removed, and a rule added
    there no longer binds its check (nothing showed it or could remove it)."""
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
    rules: list[str] = []
    return rubric, context, rules


def attach(
    conn: "sqlite3.Connection",
    task_id: str,
    requirement: str,
    ref: str,
    by: str,
    *,
    seen_version: int | None = None,
    note: str | None = None,
    changed: list[str] | None = None,
    answered: list[str] | None = None,
) -> Row:
    """Attach a page to a page requirement of the task's open visit. `requirement` is its id or its name (`name_of`),
    `ref` a `parse_ref` page. Who may: the task's owner, whoever holds it now, or any person. Refuses
    `task_requirement` (no such page requirement on this visit, not a page, no such version, a PR not read yet),
    `task_changed` (a bot's stale or missing `seen_version`), `task_ended`, and `lint` (a version that is not HTML,
    `NOT_HTML`: a page for an OK is drawn as HTML; `changed` or `answered` not in their form, or a note or line that
    quotes the person's last send back to them: page_rounds). `note`, `changed` and `answered` are what the bot says
    with this version (page_rounds.said), kept on the `page_attached` event. The route checks the caller may open the
    file. Returns the `requirement_pages` row, with `said` when the new version keeps a person's OK (`ok_kept`): what the
    bot is told, as the task's history says it too."""
    from . import hubdb_core
    from . import page_rounds as R
    from . import task_engine as E
    from . import task_requirements as TR
    from . import waiting_on as W

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
    saying, wrong = R.said(note, changed, answered)
    if wrong:
        _refuse(conn, by, "lint", wrong, task_id)
    if R.quoted_back(R.last_sent_back(conn, task_id, found["id"]), R.parts_of(saying)):
        _refuse(conn, by, "lint", R.QUOTED, task_id)
    document = TR.document_of(found)
    if document:
        _as_document(conn, by, task_id, document, file_id)
        made = _submit_pages(conn, row, found, document, by, ref=(file_id, version), saying=saying)
        if made is None:
            _refuse(conn, by, "task_requirement", "No such page", task_id)
        return made
    return _pin(conn, row, found, file_id, version, by, saying)


def _as_document(conn: "sqlite3.Connection", by: str, task_id: str, document: str, file_id: str) -> None:
    """Attaching to a document approval adds the page's role link; a role may name many pages."""
    from . import task_documents as TD

    TD.record(conn, task_id, document, file_id, by)


# What a bot is told when it attaches another file to a document approval: the document is written, not attached.
WRITE_DOCUMENT = (
    "This stage approves the task's {document}. Write it: tico task attach {task} page.html --doc '{document}'"
)


def submit_linked(conn: "sqlite3.Connection", file_id: str, version: int | None, by: str) -> dict[tuple[str, str], Row]:
    """Refresh each task that uses this page as a document. Without a version, its archive status changed."""
    ref = (file_id, version) if version is not None else None
    submitted: dict[tuple[str, str], Row] = {}
    for target, document in conn.execute(
        "SELECT target,role FROM page_links WHERE page_id=? AND target LIKE 'task:%' "
        "AND role NOT IN ('writer','related') ORDER BY target,role",
        (file_id,),
    ).fetchall():
        task_id = str(target)[5:]
        name = str(document)
        if made := submit(conn, task_id, name, by, ref=ref):
            submitted[(task_id, name)] = made
    return submitted


def submit(
    conn: "sqlite3.Connection", task_id: str, document: str, by: str, *, ref: tuple[str, int] | None = None
) -> Row | None:
    """Refresh the open stage's matching approval. A publish supplies its version; a link or archive edit reconciles
    membership. A publish by a bot that neither owns nor holds the task leaves the submission as it is."""
    from . import hubdb_core
    from . import task_engine as E
    from . import task_requirements as TR
    from . import waiting_on as W

    core: Any = hubdb_core
    row = E.task(conn, task_id)
    if not row or row["status"] != "open" or not row.get("visit"):
        return None
    found = next(
        (r for r in TR.for_visit(conn, row["visit"]) if TR.document_of(r) == document),
        None,
    )
    if found is None:
        return None
    if ref and by not in (row.get("owner_id"), W.holder(conn, row)) and not core.is_human(by):
        return None
    _, context, _ = judged_against(conn, row, found)
    heads: dict[str, str | None] = context.get("heads") or {}
    if not all(heads.values()):
        return None  # a PR not read yet: leave the write in place until the page can be submitted
    return _submit_pages(conn, row, found, document, by, ref=ref)


def _submit_pages(
    conn: "sqlite3.Connection",
    row: Row,
    found: Row,
    document: str,
    by: str,
    *,
    ref: tuple[str, int] | None,
    saying: Row | None = None,
) -> Row | None:
    """Pin the live document set before evaluating the task. Link and archive edits preserve existing submissions."""
    from . import task_engine as E

    pages = live_documents(conn, row["id"], document)
    if ref:
        if ref[0] not in pages:
            return None
        pages[ref[0]] = ref[1]
    else:
        for page in current_pages(conn, row["visit"]["id"], found["id"]):
            pages.pop(page["file_id"], None)

    result: Row | None = None
    for file_id, version in pages.items():
        selected = ref is not None and file_id == ref[0]
        made = _pin(conn, row, found, file_id, version, by, (saying or {}) if selected else {}, advance=False)
        if selected:
            result = made
    if E.advance(conn, row["id"]) is None:
        E.changed(conn, row["id"], by)
    return result


def _pin(
    conn: "sqlite3.Connection",
    row: Row,
    found: Row,
    file_id: str,
    version: int,
    by: str,
    saying: Row,
    *,
    advance: bool = True,
) -> Row:
    """Pin one page version to a page requirement of the task's open visit: the `requirement_pages` row, the decision
    model's check owed on it, the task's history, then the task advances or is told it changed."""
    from . import hubdb
    from . import hubdb_core
    from . import page_verdicts as V
    from . import task_engine as E
    from . import task_requirements as TR

    H: Any = hubdb
    core: Any = hubdb_core
    task_id = row["id"]
    rubric, context, rules = judged_against(conn, row, found)
    heads: dict[str, str | None] = context.get("heads") or {}
    unread = sorted(ref_ for ref_, sha in heads.items() if not sha)
    if unread:
        _refuse(
            conn, by, "task_requirement", f"Tico is still reading {unread[0]} from GitHub. Try again soon.", task_id
        )
    document = TR.document_of(found)
    key = V.rubric_hash(rubric, context, rules)
    visit_id = row["visit"]["id"]
    was = (
        next((p for p in current_pages(conn, visit_id, found["id"]) if p["file_id"] == file_id), None)
        if document
        else current(conn, visit_id, found["id"])
    )
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
            json.dumps({"rubric": rubric, "context": context, **({"rules": rules} if rules else {})}),
            now,
        ),
    )
    V.owe(conn, file_id, version, key)
    made = _one(conn, "SELECT * FROM requirement_pages WHERE id=?", (page_id,))
    assert made is not None
    kept = TR.ok_kept(conn, row, found, made)
    E._event(  # pyright: ignore[reportPrivateUsage]
        conn,
        task_id,
        by,
        "page_attached",
        f"page:{was['file_id']}@{was['version']}" if was else None,
        f"page:{file_id}@{version}",
        None,
        {
            "requirement": found["id"],
            "file_id": file_id,
            "version": version,
            **({"document": document} if document else {}),
            **{k: v for k, v in saying.items() if v},
        },
    )
    if kept:
        # A new version of the page a person OK'd, at the commits they OK'd: their OK stands (task_requirements._page).
        # The task's history and the bot both hear so, or the bot reads the new version as asking them again (#435).
        name, was_version = str(H.actor_name(conn, kept["by_id"])), kept["page_version"]
        E._event(  # pyright: ignore[reportPrivateUsage]
            conn,
            task_id,
            core.KEEPER,
            "ok_kept",
            None,
            f"{name}'s OK (version {was_version}) carried to version {version}: same commits",
            None,
            {"requirement": found["id"], "answer": kept["id"], "from": was_version, "to": version},
        )
        made["said"] = (
            f"{name}'s OK at {TR.short_heads(json.loads(kept.get('heads') or '{}'))} stands. "
            f"This version is kept on the task and doesn't ask {name} again."
        )
    if advance and E.advance(conn, task_id) is None:
        E.changed(conn, task_id, by)
    return made
