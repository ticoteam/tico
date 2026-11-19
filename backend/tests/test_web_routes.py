"""The new web app at /next (backend/web_routes.py): its build's files, the page for any other /next path, and today's
UI at / untouched."""

from pathlib import Path

import pytest

from backend import ui_build

AUTH = {"Authorization": "Bearer ana-test"}
ROOT = Path(__file__).resolve().parents[2]
PAGE = '<!doctype html><title>Tico</title><script type="module" src="/next/assets/index-1a2b3c.js"></script>'


@pytest.fixture
def web(api, tmp_path):
    """The api client with a small build in a temporary build/web, and a file beside it the routes must never send."""
    root = tmp_path / "build" / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(PAGE)
    (root / "assets" / "index-1a2b3c.js").write_text("export const ok = 1;\n")
    (root / "boot.js").write_text("document.documentElement.dataset.theme = 'light';\n")
    (tmp_path / "build" / "secret.txt").write_text("not for the web")
    api.app.state.store.settings.web_dir = root
    return api


def test_a_built_file_is_sent_and_hashed_assets_are_kept_for_a_year(web):
    asset = web.get("/next/assets/index-1a2b3c.js", headers=AUTH)
    assert asset.status_code == 200 and asset.text == "export const ok = 1;\n"
    assert asset.headers["content-type"].startswith("text/javascript")
    assert asset.headers["cache-control"] == ui_build.IMMUTABLE
    again = web.get("/next/assets/index-1a2b3c.js", headers={**AUTH, "If-None-Match": asset.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == ui_build.IMMUTABLE
    boot = web.get("/next/boot.js", headers=AUTH)
    assert boot.status_code == 200 and boot.headers["cache-control"] == "private, no-cache"
    # An older build's asset is gone, never the page under a script's name.
    assert web.get("/next/assets/index-000000.js", headers=AUTH).status_code == 404
    assert web.get("/next/assets/index-1a2b3c.js").status_code == 401


def test_every_other_next_path_is_the_page_revalidated_under_the_page_policy(web):
    for path in ("/next", "/next/", "/next/tasks", "/next/tasks/t-1/chat", "/next/assets"):
        r = web.get(path, headers=AUTH)
        assert r.status_code == 200 and r.text == PAGE, path
        assert r.headers["content-type"] == "text/html; charset=utf-8"
        assert r.headers["cache-control"] == "private, no-cache"
        policy = r.headers["content-security-policy"]
        assert "script-src 'self'" in policy and "object-src 'none'" in policy and "base-uri 'none'" in policy
    assert web.head("/next/tasks", headers=AUTH).status_code == 200
    assert web.get("/next/tasks").status_code == 401
    assert web.get("/", headers=AUTH).text == (ui_build.out_dir(ROOT / "ui") / "index.html").read_text()


def test_a_path_outside_the_build_is_never_sent(web, tmp_path):
    # Dot segments are refused before any route (backend/app.py's request guard).
    for path in ("/next/%2e%2e/secret.txt", "/next/..%2fsecret.txt", "/next/assets/..%2f..%2fsecret.txt"):
        r = web.get(path, headers=AUTH)
        assert r.status_code == 404 and "not for the web" not in r.text, path
    # A link in the build that leads out of it is not followed: the path is the page's.
    (tmp_path / "build" / "web" / "leak.txt").symlink_to(tmp_path / "build" / "secret.txt")
    r = web.get("/next/leak.txt", headers=AUTH)
    assert r.status_code == 200 and r.text == PAGE


def test_without_a_build_next_is_a_404_and_today_s_ui_answers_as_before(api, tmp_path):
    api.app.state.store.settings.web_dir = tmp_path / "no-build"
    assert api.get("/next", headers=AUTH).status_code == 404
    assert api.get("/next/tasks", headers=AUTH).status_code == 404
    page = api.get("/", headers=AUTH)
    assert page.status_code == 200 and page.text == (ui_build.out_dir(ROOT / "ui") / "index.html").read_text()
    assert api.get("/tico/ui/", headers=AUTH).text == page.text
    assert api.get("/sw.js", headers=AUTH).status_code == 200
