"""The 2026-09-25 performance pass: the page is revalidated, not re-sent; the chat stream's cheap
mark moves with what its snapshot shows; the goal tree is two queries, not two per goal."""

from backend.tests.test_api import api  # noqa: F401


def test_the_page_and_its_files_are_revalidated_and_the_api_is_never_stored(api):
    auth = {"Authorization": "Bearer ana-test"}
    page = api.get("/", headers=auth)
    assert page.status_code == 200 and page.headers["cache-control"] == "private, no-cache"
    assert page.headers["cdn-cache-control"] == "no-store"
    # Unchanged, it comes back as a 304 with no body; Cloudflare's weak form of the tag matches.
    again = api.get("/", headers={**auth, "If-None-Match": "W/" + page.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    icon = api.get("/assets/bot-symbols.svg", headers=auth)
    assert icon.status_code == 200 and icon.headers["cache-control"] == "private, max-age=3600"
    data = api.get("/api/v2/status", headers=auth)
    assert data.headers["cache-control"] == "no-store, no-cache, must-revalidate"

