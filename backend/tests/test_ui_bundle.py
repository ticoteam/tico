"""The UI is served as one script and one stylesheet (backend/ui_bundle.py), in the order index.html lists them."""
import os
import re
from pathlib import Path

import pytest

from backend import ui_bundle
from backend.tests.test_api import api  # noqa: F401

AUTH = {"Authorization": "Bearer ana-test"}
# TICO_UI_BUNDLE=off runs the suite on the separate files: what is about the served bundle does not apply then.
served = pytest.mark.skipif(not ui_bundle.enabled(), reason="TICO_UI_BUNDLE=off")


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
    # The bundle is one strict script, and neither it nor the page is served without a session.
    assert api.get("/tico/ui/app.bundle.js", headers=AUTH).text.startswith("'use strict';\n")
    assert api.get("/tico/ui/app.bundle.js").status_code == 401 and api.get("/").status_code == 401
    uncompressed = api.get("/tico/ui/app.bundle.js", headers={**AUTH, "Accept-Encoding": "gzip;q=0"})
    assert "content-encoding" not in uncompressed.headers


def test_standalone_scripts_are_compressed_and_still_revalidate_privately(api):
    path = "/tico/ui/health.js"
    plain = api.get(path, headers={**AUTH, "Accept-Encoding": "identity"})
    zipped = api.get(path, headers={**AUTH, "Accept-Encoding": "gzip"})
    assert zipped.content == plain.content
    assert zipped.headers["content-encoding"] == "gzip"
    assert int(zipped.headers["content-length"]) < len(zipped.content) // 2
    assert zipped.headers["etag"].startswith("W/")
    assert zipped.headers["cache-control"] == "private, no-cache"
    assert api.get(path, headers={**AUTH, "If-None-Match": zipped.headers["etag"]}).status_code == 304
    assert api.get(path, headers={"Accept-Encoding": "gzip"}).status_code == 401


def test_a_versioned_font_is_kept_and_an_unversioned_one_revalidates(api):
    # Inter's font-display is optional: a font the browser must revalidate first can miss the first paint.
    url = re.search(r'href="(/vendor/fonts/inter-variable\.woff2\?v=[0-9a-f]{12})"',
                    (Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text()).group(1)
    kept = api.get(url, headers=AUTH)
    assert kept.status_code == 200 and kept.headers["content-type"] == "font/woff2"
    assert kept.headers["cache-control"] == ui_bundle.IMMUTABLE
    assert api.get(url.split("?")[0], headers=AUTH).headers["cache-control"] == "private, no-cache"


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


def test_no_two_files_declare_the_same_top_level_name():
    # The listed files share one scope (bundled or not), so a top-level const, let or class whose name another file
    # also declares is a SyntaxError that leaves the whole app blank.
    from pathlib import Path
    decl = re.compile(r"^(?:async\s+)?(function\*?|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M)
    ui = Path(ui_bundle.__file__).resolve().parent.parent / "ui"
    _, paths = ui_bundle.build(ui)
    seen = {}
    for path in paths:
        if path.suffix == ".js":
            for kind, name in decl.findall(path.read_text()):
                seen.setdefault(name, []).append((path.name, kind))
    clashes = {n: d for n, d in seen.items() if len(d) > 1 and any(k in ("const", "let", "class") for _, k in d)}
    assert clashes == {}
