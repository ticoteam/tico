"""Shared rows for the page requirement's tests (backend/tests/test_page_requirement.py, test_page_rounds.py): a
pipeline with one page requirement, a page (a Tico file) at a new version, a task, Jev's recorded verdict. Synthetic rows
only; Jev is never a model."""

import uuid

from backend import page_verdicts as V
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
WRITER = "bot:writer"


def _stages(page_settings=None, person=None, line="Spec page", who_oks="none"):
    return (
        {
            "name": "Spec",
            "owner": WRITER,
            "requirements": [
                {"kind": "page", "line": line, "person": person, "settings": page_settings or {}, "who_oks": who_oks}
            ],
        },
    )


def _pipeline(c, **kw):
    made = X.pipeline(c, "product", "Product", _stages(**kw), manager="bot:manager")
    return made["requirements"]["Spec"][0]


def _page(c, store, text="<h1>Spec</h1><p>Build the importer.</p>", fid=None):
    """A page (a Tico file) with one more version holding `text`. Returns (file id, version)."""
    data = text.encode()
    digest = store.blobs.put(data, "text/html")
    now = H.now()
    blob = H.new_id()
    c.execute(
        "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) VALUES (?,?,?,?,?,?,?)",
        (blob, "bot:writer", digest, len(data), "spec.html", "text/html", now),
    )
    if fid is None:
        fid = "file-" + uuid.uuid4().hex[:24]
        c.execute(
            "INSERT INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, first_activity_at, "
            "last_activity_at) VALUES (?,?,?,?,?,'file','text/html','tico_blob',?,?)",
            (fid, "writer", "bot", fid, "Spec", now, now),
        )
    version = c.execute("SELECT current_version FROM bot_files WHERE id=?", (fid,)).fetchone()[0] + 1
    c.execute(
        "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, version, blob, digest, len(data), "spec.html", "text/html", "bot:writer", now),
    )
    c.execute("UPDATE bot_files SET current_version=? WHERE id=?", (version, fid))
    return fid, version


def _task(c):
    return X.new_task(c, ANA, "Importer spec", "product", assignee=WRITER)


def _say(c, page_row, verdict, reasons="", failed=False):
    """Jev's verdict on an attached page, as the Jev step records it."""
    key = page_row["rubric_hash"]
    owed = V.owe(c, page_row["file_id"], page_row["version"], key)
    result = {"verdict": verdict, "p": {"met": 0.9, "not_met": 0.1}.get(verdict, 0.5), "reason": reasons}
    for _ in range(V.TRIES if failed else 1):
        V.record(c, owed["id"], result, failed=failed)
    return V.get(c, page_row["file_id"], page_row["version"], key)


def _v(c, row):
    return E.task(c, row["id"])["version"]
