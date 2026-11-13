"""The server's icons (backend/brand_icons.py): the two root icons and the brand files its pages link are public to a browser
that isn't signed in, nothing else under /assets is, and each kind of page names the icons it should."""

from pathlib import Path


UI = Path(__file__).resolve().parents[2] / "ui" / "assets"


def test_the_root_icons_answer_a_signed_out_browser_with_the_app_brand(api):
    for path, rel in (("/favicon.ico", "favicon-32.png"), ("/apple-touch-icon.png", "apple-touch-icon.png")):
        answer = api.get(path)
        assert answer.status_code == 200 and answer.content == (UI / rel).read_bytes(), path
        assert answer.headers["content-type"] == "image/png"
        assert answer.headers["cache-control"] == "public, max-age=86400"
        assert answer.headers["x-content-type-options"] == "nosniff"
        assert api.head(path).status_code == 200


def test_the_brand_files_a_page_links_are_public_and_nothing_else_under_assets_is(api):
    for path, rel in (
        ("/assets/favicon-32.png", "favicon-32.png"),
        ("/assets/apple-touch-icon.png", "apple-touch-icon.png"),
        ("/assets/tico/tico-mark.svg", "tico/tico-mark.svg"),
    ):
        answer = api.get(path)
        assert answer.status_code == 200 and answer.content == (UI / rel).read_bytes(), path
        assert answer.headers["x-content-type-options"] == "nosniff"
    assert api.get("/assets/tico/tico-mark.svg").headers["content-type"] == "image/svg+xml"
    for path in ("/assets/observability-vendors.js", "/api/v2/me"):
        assert api.get(path).status_code == 401, path
