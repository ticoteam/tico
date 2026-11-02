"""Read access to the `documents` table that holds the market notes (backend/market.py).

The repository mirror that once filled it (a worker importing linked repositories, "Bot Notes",
proposed documentation changes) is gone: company docs are `backend/docs.py`. Old mirrored rows stay
in the table untouched and are not shown anywhere; only the market collection is served.
"""

import json

from fastapi import Request

from .store import Problem


def visible(who, row):
    return who.role == "owner" or row["visibility"] == "external"


def document(c, auth, who, doc_id):
    auth.domain(who)
    row = c.execute("SELECT * FROM documents WHERE id=? AND collection='market'", (doc_id,)).fetchone()
    if not row or not visible(who, row):
        raise Problem("not_found", "Document not found or unavailable to this account", 404)
    return json.loads(row["payload"])


def install_documents(app, store, auth, mutate):
    @app.get("/api/company-docs")
    def catalog(request: Request, collection: str = "market"):
        """The market page's notes. Company docs are `/api/v2/docs`."""
        who = request.state.identity
        auth.domain(who)
        if collection != "market":
            raise Problem("collection", "Company docs moved to /api/v2/docs; this lists the market notes only", 422)
        with store.read() as c:
            rows = [r for r in c.execute("SELECT * FROM documents WHERE collection='market' ORDER BY id")
                    if visible(who, r)]
            return {"collection": collection, "collection_counts": {"market": len(rows)},
                    "documents": [{k: v for k, v in json.loads(r["payload"]).items() if k != "content"} for r in rows],
                    "proposals": [], "errors": [], "sync": {"running": False, "state": None, "error": None}}

    @app.get("/api/company-docs/{doc_id:path}")
    def detail(request: Request, doc_id: str):
        with store.read() as c:
            return document(c, auth, request.state.identity, doc_id)
