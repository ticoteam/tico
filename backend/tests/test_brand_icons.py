"""The server's icons (backend/brand_icons.py): the two root icons and the brand files its pages link are public to a browser
that isn't signed in, nothing else under /assets is, and each kind of page names the icons it should."""

from pathlib import Path

from backend import oidc
from backend.company_page import render
from backend.tests.helpers.api import api_settings, headers  # noqa: F401  (the api fixture)
from backend.tests.helpers.company_page_release import view_model

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


def test_a_sign_in_page_names_the_tico_mark_and_its_colours(tmp_path):
    body = oidc._page(api_settings(tmp_path), 200, "Signed out", "Bye").body.decode()
    assert '<link rel="icon" href="/assets/tico/tico-mark.svg" type="image/svg+xml">' in body
    assert '<link rel="icon" href="/assets/favicon-32.png" type="image/png" sizes="32x32">' in body
    assert '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">' in body
    assert '<meta name="theme-color" content="#131413" media="(prefers-color-scheme: dark)">' in body
    assert "<title>Signed out - " in body


def test_a_company_page_names_the_root_icons_and_its_own_colours():
    view = view_model()
    for page in (render.page(view), render.signed_out(view)):
        assert '<link rel="icon" href="/favicon.ico" type="image/png" sizes="32x32">' in page
        assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in page
        assert '<meta name="theme-color" content="#0f1217" media="(prefers-color-scheme: dark)">' in page
        assert '<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">' in page
