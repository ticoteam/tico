"""Stage approvals get their own document names (task_requirements.APPROVED gains Proposal, Results and Checked in
production). The 20261117061626 migration knew only Spec, QA report, Release report and Invoice, so a page a person
approves that it could not place became the QA report, and bot-checked pages kept no document.

- A live page approval on a stage named Proposal that approves the QA report approves the Proposal, and its line is
  Proposal. An open task in that stage whose document (a page linked to it with that role) is named QA report has it
  renamed Proposal, unless the task already has a Proposal (on any page): then both records keep its QA report, so a
  task never ends up with two Proposals and the page links and the older task_documents rows agree.
- A live page whose line is "Results page" and that names no document approves the Results; its line is Results.
- A live page whose line is "Checked in production" and that names no document approves that document.
- A page already attached to one of those two on an open task's current visit is linked to the task with the
  document's name, as attaching it now would link it: a task already in Running or Validate keeps its page counted
  instead of waiting for it again.

A review of the newest commit (`at_head`) is never a document and is left alone. Running it again changes nothing.
"""

import json
from datetime import datetime, timezone


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _settings(text):
    try:
        found = json.loads(text or "{}")
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _document(stage_name, line, settings):
    """The document this approval should name now, or None to leave it."""
    if settings.get("at_head"):
        return None
    named = settings.get("document")
    if named == "QA report" and (stage_name or "").strip().lower() == "proposal":
        return "Proposal"
    if named:
        return None
    said = " ".join((line or "").split()).lower()
    if said == "results page":
        return "Results"
    if said == "checked in production":
        return "Checked in production"
    return None


def _link_attached(conn, made_documents):
    """Link each page attached to a requirement that just became a document, on an open task's current visit, to
    the task with that document's name (page_links, as task_documents does on attach). Already linked: left."""
    if not made_documents or not all(_table(conn, t) for t in ("page_links", "requirement_pages", "visits", "tasks")):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for requirement_id, document in made_documents:
        conn.execute(
            "INSERT OR IGNORE INTO page_links (id, page_id, target, role, created_by, created_at) "
            "SELECT lower(hex(randomblob(16))), rp.file_id, 'task:' || v.task_id, ?, 'keeper', ? "
            "FROM requirement_pages rp JOIN visits v ON v.id = rp.visit_id JOIN tasks t ON t.id = v.task_id "
            "WHERE rp.requirement_id = ? AND rp.replaced_at IS NULL AND v.left_at IS NULL AND t.status = 'open' "
            "AND EXISTS (SELECT 1 FROM bot_files f WHERE f.id = rp.file_id)",
            (document, now, requirement_id),
        )


def up(conn):
    if not all(_table(conn, name) for name in ("stages", "requirements")):
        return
    rows = conn.execute(
        "SELECT r.id, r.stage_id, s.name, r.line, r.settings FROM requirements r JOIN stages s ON s.id = r.stage_id "
        "WHERE r.withdrawn_at IS NULL AND s.retired_at IS NULL AND r.kind = 'page'"
    ).fetchall()
    proposal_stages = set()
    made_documents = []  # (requirement id, document) for Results and Checked in production
    for requirement_id, stage_id, stage_name, line, text in rows:
        settings = _settings(text)
        document = _document(stage_name, line, settings)
        if not document:
            continue
        conn.execute(
            "UPDATE requirements SET line=?, settings=? WHERE id=?",
            (document, json.dumps({**settings, "document": document}), requirement_id),
        )
        if document == "Proposal":
            proposal_stages.add(stage_id)
        else:
            made_documents.append((requirement_id, document))
    _link_attached(conn, made_documents)
    if not proposal_stages or not _table(conn, "tasks"):
        return
    marks = ",".join("?" * len(proposal_stages))
    open_there = f"SELECT 'task:' || id FROM tasks WHERE status='open' AND stage_id IN ({marks})"
    stages = sorted(proposal_stages)
    if _table(conn, "page_links"):  # a task's document is a page linked to it with the document's name as role
        conn.execute(
            f"UPDATE page_links SET role='Proposal' WHERE role='QA report' AND target IN ({open_there}) "
            "AND NOT EXISTS (SELECT 1 FROM page_links o WHERE o.target = page_links.target AND o.role = 'Proposal')",
            stages,
        )
    if _table(conn, "task_documents"):  # the older record of the same, while it is still kept
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        conn.execute(
            "UPDATE task_documents SET name='Proposal', updated_at=? WHERE name='QA report' "
            f"AND 'task:' || task_id IN ({open_there}) AND NOT EXISTS (SELECT 1 FROM task_documents d "
            "WHERE d.task_id = task_documents.task_id AND d.name = 'Proposal')",
            (now, *stages),
        )
