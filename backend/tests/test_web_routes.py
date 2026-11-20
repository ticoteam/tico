"""The web app at / (backend/web_routes.py): its build's files under /next/, its page for every app address, and an
address from when it lived at /next sent on to the same page without it."""

import pytest

from backend import web_routes

AUTH = {"Authorization": "Bearer ana-test"}
PAGE = '<!doctype html><title>Tico</title><script type="module" src="/next/assets/index-1a2b3c.js"></script>'


@pytest.fixture
def web(api, tmp_path):
    """The api client with a small build in a temporary build/web, and a file beside it the routes must never send."""
    root = tmp_path / "build" / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(PAGE)
    (root / "assets" / "index-1a2b3c.js").write_text("export const ok = 1;\n")
    (root / "boot.js").write_text("document.documentElement.dataset.theme = 'light';\n")
    (root / "sw.js").write_text("self.addEventListener('push', () => {});\n")
    (tmp_path / "build" / "secret.txt").write_text("not for the web")
    api.app.state.store.settings.web_dir = root
    return api


def test_a_built_file_is_sent_and_hashed_assets_are_kept_for_a_year(web):
    asset = web.get("/next/assets/index-1a2b3c.js", headers=AUTH)
    assert asset.status_code == 200 and asset.text == "export const ok = 1;\n"
    assert asset.headers["content-type"].startswith("text/javascript")
    assert asset.headers["cache-control"] == web_routes.IMMUTABLE
    again = web.get("/next/assets/index-1a2b3c.js", headers={**AUTH, "If-None-Match": asset.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == web_routes.IMMUTABLE
    boot = web.get("/next/boot.js", headers=AUTH)
    assert boot.status_code == 200 and boot.headers["cache-control"] == "private, no-cache"
    # An older build's asset is gone, never the page under a script's name.
    assert web.get("/next/assets/index-000000.js", headers=AUTH).status_code == 404
    assert web.get("/next/assets/index-1a2b3c.js").status_code == 401


def test_every_app_address_is_the_page_revalidated_under_the_page_policy(web):
    for path in ("/", "/tasks", "/tasks/engineering/12/chat", "/library/skills", "/no-such-page"):
        r = web.get(path, headers=AUTH)
        assert r.status_code == 200 and r.text == PAGE, path
        assert r.headers["content-type"] == "text/html; charset=utf-8"
        assert r.headers["cache-control"] == "private, no-cache"
        policy = r.headers["content-security-policy"]
        assert "script-src 'self'" in policy and "object-src 'none'" in policy and "base-uri 'none'" in policy
    assert web.head("/tasks", headers=AUTH).status_code == 200
    assert (
        web.get("/", headers={**AUTH, "If-None-Match": web.get("/", headers=AUTH).headers["etag"]}).status_code == 304
    )
    assert web.get("/").status_code == 401 and web.get("/tasks").status_code == 401


def test_the_server_s_own_addresses_never_fall_through_to_the_page(web):
    for path in ("/api", "/api/v2/no-such-thing", "/auth/no-such-thing", "/tico/ui/no-such-file", "/assets/none.png"):
        r = web.get(path, headers=AUTH)
        assert r.status_code == 404 and PAGE not in r.text, path
    # A route that takes only a write is still refused as one, not answered with the page.
    assert PAGE not in web.get("/api/v2/tasks/t-1/archive", headers=AUTH).text
    assert web.get("/api/v2/me", headers=AUTH).json()["actor"] == "human:ana"
    assert web.get("/assets/tico/tico-mark.svg").headers["content-type"] == "image/svg+xml"
    assert web.get("/manifest.webmanifest", headers=AUTH).json()["start_url"] == "/"


def test_an_address_under_next_goes_to_the_same_page_at_the_root(web):
    for old, new in (
        ("/next", "/"),
        ("/next/", "/"),
        ("/next/tasks", "/tasks"),
        ("/next/tasks/engineering/12/chat?view=list&q=a%20b", "/tasks/engineering/12/chat?view=list&q=a%20b"),
        ("/next/library/caf%C3%A9", "/library/caf%C3%A9"),
        ("/next/assets", "/assets"),
    ):
        r = web.get(old, headers=AUTH, follow_redirects=False)
        assert r.status_code == 308 and r.headers["location"] == new, old
    assert web.get("/next/tasks", headers=AUTH).text == PAGE
    assert web.head("/next/tasks", headers=AUTH, follow_redirects=False).status_code == 308
    # Signed out, the sign-in comes first; it returns to the /next address, which is sent on from there.
    assert web.get("/next/tasks", follow_redirects=False).status_code == 401


def test_a_next_address_never_leads_off_the_server(web):
    for old in ("/next//evil.example/x", "/next/\\evil.example", "/next/%5Cevil.example", "/next///evil.example"):
        r = web.get(old, headers=AUTH, follow_redirects=False)
        location = r.headers["location"]
        assert r.status_code == 308 and location.startswith("/") and location[1:2] not in ("/", "\\"), old


def test_a_path_outside_the_build_is_never_sent(web, tmp_path):
    # Dot segments are refused before any route (backend/app.py's request guard).
    for path in ("/next/%2e%2e/secret.txt", "/next/..%2fsecret.txt", "/next/assets/..%2f..%2fsecret.txt"):
        r = web.get(path, headers=AUTH)
        assert r.status_code == 404 and "not for the web" not in r.text, path
    # A link in the build that leads out of it is not followed: the address is the app's.
    (tmp_path / "build" / "web" / "leak.txt").symlink_to(tmp_path / "build" / "secret.txt")
    r = web.get("/next/leak.txt", headers=AUTH, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/leak.txt"
    assert "not for the web" not in web.get("/next/leak.txt", headers=AUTH).text


def test_the_service_worker_installed_at_the_root_gets_the_build_s_for_its_update_check(web):
    worker = web.get("/sw.js?v=1", headers=AUTH)
    assert worker.status_code == 200 and worker.text == "self.addEventListener('push', () => {});\n"
    assert worker.headers["content-type"] == "text/javascript; charset=utf-8"
    assert worker.headers["cache-control"] == "private, no-cache"
    assert web.get("/sw.js").status_code == 401


def test_without_a_build_the_page_is_a_404_and_the_old_page_is_gone(api, tmp_path):
    api.app.state.store.settings.web_dir = tmp_path / "no-build"
    assert api.get("/", headers=AUTH).status_code == 404
    assert api.get("/tasks", headers=AUTH).status_code == 404
    assert api.get("/sw.js", headers=AUTH).status_code == 404
    assert api.get("/next/tasks", headers=AUTH, follow_redirects=False).status_code == 308
    for old in ("/tico/ui/", "/tico/ui/index.html", "/vendor/marked.min.js", "/styles/tokens.css"):
        assert api.get(old, headers=AUTH).status_code == 404, old
