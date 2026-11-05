"""The market notes behind `/api/company-docs`, which the Market page reads (they live in market_pages,
backend/market.py). Company docs are `backend/docs.py`, served from `/api/v2/docs`.
"""

from fastapi import Request

from . import market
from .store import Problem


def document(c, auth, who, doc_id):
    auth.domain(who)
    page = market.read_page(c, doc_id)
    if not page:
        raise Problem("not_found", "Document not found or unavailable to this account", 404)
    return page


def install_documents(app, store, auth, mutate):
    @app.get("/api/company-docs")
    def catalog(request: Request, collection: str = "market"):
        """The market page's notes. Company docs are `/api/v2/docs`."""
        who = request.state.identity
        auth.domain(who)
        if collection != "market":
            raise Problem("collection", "Company docs moved to /api/v2/docs; this lists the market notes only", 422)
        with store.read() as c:
            pages = market.list_pages(c)
            return {"collection": collection, "collection_counts": {"market": len(pages)},
                    "documents": [{k: v for k, v in page.items() if k != "content"} for page in pages],
                    "proposals": [], "errors": [], "sync": {"running": False, "state": None, "error": None}}

    @app.get("/api/company-docs/{doc_id:path}")
    def detail(request: Request, doc_id: str):
        with store.read() as c:
            return document(c, auth, request.state.identity, doc_id)
