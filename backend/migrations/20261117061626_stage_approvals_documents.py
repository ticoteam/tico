"""Stage approvals name a document (spec v3.4 change 7). A stage's approval is a page requirement whose `settings.document`
is one of the fixed names (task_requirements.APPROVED): the bot writing that document in the stage submits it, and the
person approves that version. Pitches are removed: a feature starts as a chat, and its first approval is the Spec.

- A stage's OK a person gives (people only, or naming a person) becomes a page requirement approving the matching
  document: "release" is the Release report, "spec" the Spec, "QA" or "test" the QA report, "invoice", "pay" or "bill"
  the Invoice, anything else the Release report. Its line is the document's name. An OK naming a bot stays as it is.
- A page a person approves (people only, or naming a person) gets the matching document the same way ("Go-live" is the
  Release report; "QA" or "Review" the QA report; anything else the QA report), and its line is the document's name.
  Product's bot-checked "Spec page" approves the Spec too.
- The page pinned now to such a requirement on an open visit becomes the task's document of that name, with its
  versions, unless the task already has one.
- A Pitch stage (a stage OK with `body_max`) is retired: its requirements are withdrawn, and each open task there moves
  on to the next live work stage of its pipeline, entering it as met. Open pitch OKs are dropped with it.
- `body_max` goes from every OK's settings.

Running it again changes nothing.
"""

import json
import uuid
from datetime import datetime, timezone

APPROVED = ("Spec", "QA report", "Release report", "Invoice")
# A converted OK has no rubric of its own: the decision model checks the document against these.
RUBRICS = {
    "Spec": "States the goal, the scope (what is in and what is out), and what done looks like as acceptance criteria "
    "someone can check.",
    "QA report": "Shows what changed with before and after screenshots of the running app (or the output that proves "
    "a change with nothing to see), and the checks at the newest commit.",
    "Release report": "Says what is shipping: the change in a few plain lines, the PRs it merges at their newest "
    "commits, and anything a person should know before it merges (a migration, a setting, what is left out).",
    "Invoice": "Names who is paid, how much, for what, and when it is due.",
}
WHY = "Pitch removed: a feature starts as a chat, and its first approval is the Spec"


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _settings(text):
    try:
        found = json.loads(text or "{}")
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _person_signs(who_oks, person):
    return who_oks == "people_only" or str(person or "").startswith("human:")


def _document_of_ok(line):
    said = (line or "").lower()
    if "spec" in said:
        return "Spec"
    if "qa" in said.split() or "test" in said:
        return "QA report"
    if any(word in said for word in ("invoice", "pay", "bill")):
        return "Invoice"
    return "Release report"


def _document_of_page(line):
    said = (line or "").lower()
    if "spec" in said:
        return "Spec"
    if "go-live" in said or "go live" in said or "release" in said:
        return "Release report"
    if "invoice" in said:
        return "Invoice"
    return "QA report"


def _move_on(conn, task_id, visit_id, stage_id, ts):
    """Leave the Pitch visit and enter `stage_id` as met: the one statement that changes a task's stage goes through the
    move guard (task_engine._write)."""
    conn.execute(
        "UPDATE visits SET left_at=?, how='met', by_id='keeper', why=? WHERE id=? AND left_at IS NULL",
        (ts, WHY, visit_id),
    )
    conn.execute(
        "INSERT INTO visits (id, task_id, stage_id, entered_at, entered_how, last_change_at, next_try_at) "
        "VALUES (?,?,?,?,'met',?,?)",
        (str(uuid.uuid4()), task_id, stage_id, ts, ts, ts),
    )
    conn.execute("INSERT OR REPLACE INTO task_move_guards (id, open, task_id) VALUES (1, 1, ?)", (task_id,))
    try:
        conn.execute("UPDATE tasks SET stage_id=?, version=version+1, updated_at=? WHERE id=?", (stage_id, ts, task_id))
    finally:
        conn.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")


def _retire_pitches(conn, ts):
    pitches = conn.execute(
        "SELECT DISTINCT s.id, s.pipeline_id, s.position FROM stages s JOIN requirements r ON r.stage_id = s.id "
        "WHERE s.retired_at IS NULL AND s.kind='work' AND r.kind='ok' AND r.withdrawn_at IS NULL "
        "AND json_extract(r.settings, '$.body_max') IS NOT NULL"
    ).fetchall()
    for stage_id, pipeline_id, position in pitches:
        after = conn.execute(
            "SELECT id FROM stages WHERE pipeline_id=? AND kind='work' AND retired_at IS NULL AND id<>? "
            "AND position>? ORDER BY position LIMIT 1",
            (pipeline_id, stage_id, position),
        ).fetchone()
        if not after:
            continue  # a pipeline whose only work stage is the pitch keeps it
        tasks = conn.execute(
            "SELECT t.id, v.id FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
            "WHERE t.stage_id=? AND t.status='open'",
            (stage_id,),
        ).fetchall()
        for task_id, visit_id in tasks:
            _move_on(conn, task_id, visit_id, after[0], ts)
        conn.execute("UPDATE requirements SET withdrawn_at=? WHERE stage_id=? AND withdrawn_at IS NULL", (ts, stage_id))
        conn.execute("UPDATE stages SET retired_at=?, updated_at=? WHERE id=?", (ts, ts, stage_id))


def _name_documents(conn):
    """Each live stage approval names its document. Returns the requirement ids that now approve one."""
    rows = conn.execute(
        "SELECT r.id, r.kind, r.who_oks, r.person_id, r.line, r.settings FROM requirements r "
        "JOIN stages s ON s.id = r.stage_id WHERE r.withdrawn_at IS NULL AND s.retired_at IS NULL "
        "AND r.kind IN ('ok', 'page')"
    ).fetchall()
    named = []
    for requirement_id, kind, who_oks, person, line, text in rows:
        settings = _settings(text)
        if settings.get("document") in APPROVED:
            named.append(requirement_id)
            continue
        if kind == "ok":
            if not _person_signs(who_oks, person) or "body_max" in settings:
                continue
            document = _document_of_ok(line)
            settings = {"document": document, "rubric": RUBRICS[document]}
        else:
            bot_spec = (line or "").strip() == "Spec page"
            if not (_person_signs(who_oks, person) or bot_spec):
                continue
            if settings.get("at_head"):
                continue  # a review of the newest commit is a page a bot attaches, never a document
            document = _document_of_page(line)
            settings = {**settings, "document": document}
        conn.execute(
            "UPDATE requirements SET kind='page', line=?, settings=? WHERE id=?",
            (document, json.dumps(settings), requirement_id),
        )
        named.append(requirement_id)
    return named


def _documents_from_pages(conn, named, ts):
    """The page pinned now to a document approval on an open visit is that task's document, unless it has one."""
    if not named or not _table(conn, "task_documents") or not _table(conn, "requirement_pages"):
        return
    marks = ",".join("?" * len(named))
    pages = conn.execute(
        "SELECT v.task_id, json_extract(r.settings, '$.document'), p.file_id FROM requirement_pages p "
        "JOIN visits v ON v.id = p.visit_id AND v.left_at IS NULL JOIN requirements r ON r.id = p.requirement_id "
        f"JOIN bot_files f ON f.id = p.file_id WHERE p.replaced_at IS NULL AND r.id IN ({marks})",
        named,
    ).fetchall()
    for task_id, document, file_id in pages:
        conn.execute(
            "INSERT OR IGNORE INTO task_documents (id, task_id, name, file_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), task_id, document, file_id, ts, ts),
        )


def up(conn):
    if not all(_table(conn, name) for name in ("stages", "requirements", "tasks", "visits", "task_move_guards")):
        return
    ts = _now()
    _retire_pitches(conn, ts)
    named = _name_documents(conn)
    _documents_from_pages(conn, named, ts)
    conn.execute(
        "UPDATE requirements SET settings=json_remove(settings, '$.body_max') "
        "WHERE kind='ok' AND json_extract(settings, '$.body_max') IS NOT NULL"
    )
