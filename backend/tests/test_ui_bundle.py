"""The UI is served as one script and one stylesheet (backend/ui_bundle.py), in the order index.html lists them."""
import os
import re

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import ui_bundle
from backend.app import create_app
from backend.auth import Identity
from backend.config import ROOT, Settings
from backend.tests.test_api import api  # noqa: F401

AUTH = {"Authorization": "Bearer ana-test"}
# TICO_UI_BUNDLE=off runs the suite on the separate files: what is about the served bundle does not apply then.
served = pytest.mark.skipif(not ui_bundle.enabled(), reason="TICO_UI_BUNDLE=off")
UI = ROOT / "ui"


def listed(kind):
    """The files index.html lists inside a bundle region, in order."""
    html = (UI / "index.html").read_text()
    region = re.search(rf"<!-- bundle:{kind}:start -->(.*?)<!-- bundle:{kind}:end -->", html, re.S).group(1)
    return [m[len("/tico/ui/"):] for m in re.findall(r'(?:src|href)="([^"]+)"', region)]


@served
def test_the_page_names_one_versioned_script_and_one_stylesheet(api):
    r = api.get("/", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert re.findall(r'src="/tico/ui/app\.bundle\.js\?v=([0-9a-f]{16})"', r.text) and "bundle:" not in r.text
    assert re.findall(r'href="/tico/ui/app\.bundle\.css\?v=([0-9a-f]{16})"', r.text)
    assert "/tico/ui/app/" not in r.text and "/tico/ui/styles/" not in r.text
    assert r.headers["cache-control"] == "private, no-cache"           # the page is revalidated on every load
    assert "script-src 'self';" in r.headers["content-security-policy"] and "sha256-" not in r.headers["content-security-policy"]
    assert "<script>" not in r.text                                       # no inline code
    again = api.get("/", headers={**AUTH, "If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    assert api.get("/", headers={**AUTH, "If-None-Match": "W/" + r.headers["etag"]}).status_code == 304   # Cloudflare's weak form
    assert api.get("/tico/ui/", headers=AUTH).text == r.text
    # Reuse the page and app fixture to check the build an open tab receives.
    js = re.search(r'app\.bundle\.js\?v=([0-9a-f]{16})', r.text).group(1)
    css = re.search(r'app\.bundle\.css\?v=([0-9a-f]{16})', r.text).group(1)
    assert api.get("/api/v2/config", headers=AUTH).json()["ui_build"] == f"{js}.{css}"


def test_the_config_names_no_build_when_the_files_are_served_as_they_are(api, monkeypatch):
    monkeypatch.setenv("TICO_UI_BUNDLE", "off")
    assert api.get("/api/v2/config", headers=AUTH).json()["ui_build"] == ""


@served
@pytest.mark.parametrize("kind,path,tag,mark", [("js", "/tico/ui/app.bundle.js", "// file: ", "'use strict';"),
                                                 ("css", "/tico/ui/app.bundle.css", "/* file: ", "/* file: styles/tokens.css */")])
def test_a_bundle_is_the_listed_files_in_order_and_kept_for_a_year(api, kind, path, tag, mark):
    files = [f for f in listed(kind) if f.startswith(("app/", "styles/"))]
    r = api.get(path, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert re.fullmatch(r'"[0-9a-f]{32}-gzip"', r.headers["etag"]) and r.headers["content-encoding"] == "gzip"   # the client accepts gzip
    plain = api.get(path, headers={**AUTH, "Accept-Encoding": "identity"})   # a strong ETag per representation
    assert re.fullmatch(r'"[0-9a-f]{32}"', plain.headers["etag"]) and "content-encoding" not in plain.headers
    assert plain.text == r.text and plain.headers["vary"] == "Accept-Encoding"
    assert r.text.lstrip().startswith(mark) or r.text.startswith(mark)
    marks = re.findall(r"^" + re.escape(tag) + r"(\S+?)(?: \*/)?$", r.text, re.M)
    assert marks == files and len(files) > 20
    for name in files:                                                    # each file whole, not summarised
        assert (UI / name).read_text().strip() in r.text
    page = api.get("/", headers=AUTH).text
    assert re.search(re.escape(path) + r"\?v=" + r.headers["etag"][1:17], page)   # the URL carries the content hash
    assert api.get(path, headers={**AUTH, "If-None-Match": r.headers["etag"]}).status_code == 304


@served
def test_the_bundle_is_strict_once_and_needs_a_session(api):
    js = api.get("/tico/ui/app.bundle.js", headers=AUTH).text
    assert js.startswith("'use strict';\n")
    assert api.get("/tico/ui/app.bundle.js").status_code == 401 and api.get("/").status_code == 401


def test_a_change_to_a_listed_file_changes_the_url(tmp_path):
    ui = tmp_path / "ui"
    (ui / "app").mkdir(parents=True)
    (ui / "styles").mkdir()
    (ui / "app/a.js").write_text("'use strict';\nconst A = 1;\n")
    (ui / "app/b.js").write_text("'use strict';\nconst B = A + 1;\n")
    (ui / "styles/a.css").write_text("a{color:red}\n")
    (ui / "index.html").write_text(
        "<head>\n<!-- bundle:css:start -->\n<link rel=\"stylesheet\" href=\"/tico/ui/styles/a.css\">\n<!-- bundle:css:end -->\n</head>\n<body>\n"
        "<!-- bundle:js:start -->\n<script src=\"/tico/ui/app/b.js\"></script>\n<script src=\"/tico/ui/app/a.js\"></script>\n<!-- bundle:js:end -->\n</body>\n")
    bundles = ui_bundle.UiBundle(ui)
    first = bundles.get()
    assert first.js.decode().index("// file: app/b.js") < first.js.decode().index("// file: app/a.js")   # index.html's order
    changed = ui / "app/a.js"
    before = changed.stat()
    changed.write_text("'use strict';\nconst A = 2;\n")
    os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    second = bundles.get()
    assert second.etag[ui_bundle.JS_PATH] != first.etag[ui_bundle.JS_PATH] and second.page != first.page
    (ui / "app/a.js").write_text("const A = 3;\n")                         # not strict: it cannot share a script
    assert bundles.get() is None


def test_bundling_can_be_turned_off_to_serve_the_separate_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TICO_UI_BUNDLE", "off")
    registry = tmp_path / "reg"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example"}))
    app = create_app(Settings(db_path=tmp_path / "hub.db", registry_dir=registry,
                              test_identities={"ana-test": Identity("human:ana", "owner", "ana@acme.example")}))
    # No bundle routes: the static mount serves index.html and the files as they are.
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/tico/ui/app.bundle.js" not in paths and "/" not in paths
    monkeypatch.setenv("TICO_UI_BUNDLE", "on")
    assert "/tico/ui/app.bundle.js" in {getattr(route, "path", "") for route in create_app(Settings(
        db_path=tmp_path / "hub2.db", registry_dir=registry)).routes}
