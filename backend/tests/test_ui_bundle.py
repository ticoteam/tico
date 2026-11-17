"""The UI as the server sends it: the files scripts/build-ui.cjs wrote to build/ui, and nothing it builds itself
(backend/ui_bundle_routes.py, backend/ui_build.py). The build's own rules (order, parts, maps) are checked in
scripts/tests/test_build_ui.py; here the real build of this checkout is served (conftest builds it when it can)."""

import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest

from backend import ui_build

AUTH = {"Authorization": "Bearer ana-test"}
ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
PART_BYTES = 512 * 1024
BUNDLE_URL = r"/tico/ui/(app\.bundle\.(?:[a-z][a-z0-9-]*\.)?[0-9a-f]{16}\.(?:js|css))"


def page_files(page):
    """The bundle files a page names in its tags (not the page parts' list), in order."""
    return re.findall(r'(?:src|href)="' + BUNDLE_URL + '"', page)


def parts_table(page):
    return json.loads(re.search(r'<script type="application/json" id="ui-parts">(.*?)</script>', page).group(1))


def test_the_page_names_the_bundle_files_by_their_content(api):
    r = api.get("/", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.text == (ui_build.out_dir(UI) / "index.html").read_text() and "bundle:" not in r.text
    files = page_files(r.text)
    assert [f for f in files if f.endswith(".css")] and len([f for f in files if f.endswith(".js")]) > 2
    assert "/tico/ui/app/" not in r.text and "/tico/ui/styles/" not in r.text
    assert 'src="/tico/ui/theme.ts"' not in r.text and 'src="/vendor/marked.min.js"' not in r.text
    assert r.headers["cache-control"] == "private, no-cache"  # the page is revalidated on every load
    assert (
        "script-src 'self';" in r.headers["content-security-policy"]
        and "sha256-" not in r.headers["content-security-policy"]
    )
    assert "<script>" not in r.text  # no inline code (the parts list is a JSON block, which runs nothing)
    assert api.get("/", headers={**AUTH, "If-None-Match": r.headers["etag"]}).status_code == 304
    assert api.get("/", headers={**AUTH, "If-None-Match": "W/" + r.headers["etag"]}).status_code == 304  # Cloudflare's
    assert api.get("/tico/ui/", headers=AUTH).text == r.text
    # An open tab compares the build it loaded with the config's (ui/app/notices.ts).
    assert parts_table(r.text)["build"] == api.get("/api/v2/config", headers=AUTH).json()["ui_build"] != ""
    assert api.get("/").status_code == 401


def test_bundle_files_are_kept_for_a_year_and_sent_as_their_gzip_copies(api):
    page = api.get("/", headers=AUTH).text
    first_script = True
    for name in page_files(page):
        url = f"/tico/ui/{name}"
        plain = api.get(url, headers={**AUTH, "Accept-Encoding": "identity"})
        assert plain.status_code == 200 and plain.headers["cache-control"] == ui_build.IMMUTABLE, name
        assert "content-encoding" not in plain.headers and plain.headers["vary"] == "Accept-Encoding"
        assert plain.content == (ui_build.bundle_dir(UI) / name).read_bytes()
        # Each one cut between files to what WebKit's cache keeps (the desktop app downloaded 4.3 MB on every open).
        assert len(plain.content) <= PART_BYTES + 64 * 1024, name
        zipped = api.get(url, headers={**AUTH, "Accept-Encoding": "gzip"})
        assert zipped.headers["content-encoding"] == "gzip" and zipped.content == plain.content  # TestClient decodes
        assert zipped.headers["etag"] != plain.headers["etag"]
        again = api.get(url, headers={**AUTH, "Accept-Encoding": "gzip", "If-None-Match": zipped.headers["etag"]})
        assert again.status_code == 304
        assert api.get(url).status_code == 401
        if name.endswith(".js") and ".theme." not in name and ".marked." not in name:
            # Each part is a strict script of its own; the first marks where the bundle's run starts (boot-timing.ts).
            assert plain.text.startswith("'use strict';\n"), name
            if first_script:
                assert plain.text.startswith("'use strict';\ntry { performance.mark('tico:bundle-start');")
                first_script = False
    # An older build's file is gone, never this build's code under its name: the page reloads (ui/app/parts.ts).
    old = api.get(f"/tico/ui/app.bundle.{'0' * 16}.js", headers=AUTH)
    assert old.status_code == 404 and old.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert api.get("/tico/ui/app.bundle.js", headers=AUTH).status_code == 404
    assert api.get("/tico/ui/app.bundle.../manifest.json", headers=AUTH).status_code == 404


def test_theme_and_marked_load_on_their_own_named_by_content(api):
    page = api.get("/", headers=AUTH).text
    theme = re.search(r'src="/tico/ui/(app\.bundle\.theme\.[0-9a-f]{16}\.js)"', page).group(1)
    marked = re.search(r'src="/tico/ui/(app\.bundle\.marked\.[0-9a-f]{16}\.js)"', page).group(1)
    # theme.ts holds up the first paint: in the head, before the stylesheets and the bundle.
    assert page.index(theme) < page.index('<link rel="stylesheet" href="/tico/ui/app.bundle.')
    assert page.index(theme) < page.index("</head>") < page.index(marked)
    body = api.get(f"/tico/ui/{marked}", headers=AUTH)
    assert body.headers["cache-control"] == ui_build.IMMUTABLE
    assert body.text.strip() == (UI / "vendor/marked.min.js").read_text().strip()
    assert "ticoTheme" in api.get(f"/tico/ui/{theme}", headers=AUTH).text


def test_a_versioned_font_is_kept_and_an_unversioned_one_revalidates(api):
    # The text font's font-display is optional: a font the browser must revalidate first can miss the first paint.
    url = re.search(
        r'href="(/vendor/fonts/atkinson-hyperlegible-next-plain-zero\.woff2\?v=[0-9a-f]{12})"',
        (UI / "index.html").read_text(),
    ).group(1)
    kept = api.get(url, headers=AUTH)
    assert kept.status_code == 200 and kept.headers["content-type"] == "font/woff2"
    assert kept.headers["cache-control"] == ui_build.IMMUTABLE
    assert api.get(url.split("?")[0], headers=AUTH).headers["cache-control"] == "private, no-cache"


def test_vendor_scripts_are_sent_compressed_and_fonts_as_they_are(api):
    zipped = api.get("/vendor/marked.min.js", headers={**AUTH, "Accept-Encoding": "gzip"})
    assert zipped.status_code == 200 and zipped.headers["content-encoding"] == "gzip"
    assert zipped.content == (UI / "vendor" / "marked.min.js").read_bytes()
    assert "Accept-Encoding" in zipped.headers["vary"]
    assert (
        api.get("/vendor/marked.min.js", headers={**AUTH, "If-None-Match": zipped.headers["etag"]}).status_code == 304
    )
    plain = api.get("/vendor/marked.min.js", headers={**AUTH, "Accept-Encoding": "gzip;q=0"})
    assert plain.status_code == 200 and "content-encoding" not in plain.headers
    # A font is compressed already: it goes as it is (whichever text font the page preloads).
    font = re.search(
        r'href="(/vendor/fonts/[a-z0-9-]+\.woff2\?v=[0-9a-f]{12})"', (UI / "index.html").read_text()
    ).group(1)
    assert "content-encoding" not in api.get(font, headers={**AUTH, "Accept-Encoding": "gzip"}).headers


def test_a_typescript_file_outside_the_bundle_is_served_compiled_with_its_map(api):
    # Every TypeScript file under ui/ is also compiled on its own at its own path (ui/app/core.ts).
    r = api.get("/tico/ui/app/core.ts", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.content == (ui_build.out_dir(UI) / "app/core.js").read_bytes()
    assert r.text.rstrip().endswith("//# sourceMappingURL=core.js.map")
    m = api.get("/tico/ui/app/core.js.map", headers=AUTH)
    assert m.status_code == 200 and m.json()["sources"] == ["core.ts"]
    assert m.json()["sourcesContent"] == [(UI / "app/core.ts").read_text()]
    assert (
        api.get("/tico/ui/app/core.ts", headers={**AUTH, "If-None-Match": r.headers["etag"]}).status_code == 304
    )
    assert api.get("/tico/ui/app/core.ts").status_code == 401
    assert api.get("/tico/ui/app/no-such-file.ts", headers=AUTH).status_code == 404
    assert api.get("/tico/ui/app/no-such-file.js.map", headers=AUTH).status_code == 404


def test_the_served_bundle_is_minified_and_maps_to_its_source(api):
    page = api.get("/", headers=AUTH).text
    names = [n for n in page_files(page) if ".marked." not in n]
    names += [p["url"].removeprefix("/tico/ui/") for p in parts_table(page)["parts"].values()]
    assert len(names) > len(page_files(page))  # the page parts, each under its own address
    served = 0
    for name in names:
        body = api.get(f"/tico/ui/{name}", headers={**AUTH, "Accept-Encoding": "identity"}).text
        served += len(body) if ".theme." not in name else 0
        tail = re.search(r"sourceMappingURL=(\S+?\.map)(?: \*/)?\n\Z", body)
        assert tail and tail.group(1) == f"{name}.map", name  # every file holds minified files, each with its map
        r = api.get(f"/tico/ui/{tail.group(1)}", headers=AUTH)
        assert r.status_code == 200 and r.json()["sections"], name
        assert api.get(f"/tico/ui/{tail.group(1)}").status_code == 401
    html = (UI / "index.html").read_text()
    sources = sum(
        (UI / ref).stat().st_size
        for region in re.findall(r"<!-- bundle:(?:js|css):start -->(.*?)<!-- bundle", html, re.S)
        for ref in re.findall(r'(?:src|href)="/tico/ui/([^"]+)"', region)
    )
    assert served < sources * 0.85, (served, sources)  # minified: the sources shrink by about a quarter
    # A Sentry frame in a minified file names its source file and line (python -m backend.ui_build --where).
    name = [n for n in page_files(page) if n.endswith(".js") and ".theme." not in n and ".marked." not in n][1]
    text = (ui_build.bundle_dir(UI) / name).read_text().split("\n")
    line = next(i for i, t in enumerate(text) if not t.startswith("//") and re.search(r"function \w{6,}\(", t))
    m = re.search(r"function (\w{6,})\(", text[line])
    source, at, _ = ui_build.where(UI, f"https://x.example/tico/ui/{name}:{line + 1}:{m.start(1) + 1}").rsplit(":", 2)
    assert re.search(rf"function {m.group(1)}[(<]", (UI / source).read_text().split("\n")[int(at) - 1])
    with pytest.raises(ValueError):
        ui_build.where(UI, f"app.bundle.{'0' * 16}.js:1:1")


def test_a_page_part_is_served_at_its_address_and_an_old_address_is_gone(api):
    table = parts_table(api.get("/", headers=AUTH).text)
    assert table["parts"]
    for name, part in table["parts"].items():
        assert re.fullmatch(rf"/tico/ui/app\.bundle\.{re.escape(name)}\.[0-9a-f]{{16}}\.js", part["url"]), name
        assert name in part["load"]  # with the parts it needs, in ui/parts.json's order
    name, part = next(iter(table["parts"].items()))
    r = api.get(part["url"], headers={**AUTH, "Accept-Encoding": "identity"})
    assert r.status_code == 200 and r.headers["cache-control"] == ui_build.IMMUTABLE
    assert r.headers["content-type"].startswith("text/javascript") and r.text.startswith("'use strict';\n")
    assert api.get(f"/tico/ui/app.bundle.{name}.{'0' * 16}.js", headers=AUTH).status_code == 404


def test_the_service_worker_is_served_at_the_root_as_its_compiled_classic_script(api):
    # ui/app/boot.ts registers /sw.js?v=… (scope /): the URL stays, the body is ui/sw.ts compiled.
    compiled = (ui_build.out_dir(UI) / "sw.js").read_bytes()
    r = api.get("/sw.js?v=41", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "text/javascript; charset=utf-8"
    assert r.content == compiled and b"ServiceWorkerGlobalScope" not in r.content  # JavaScript, not the .ts source
    assert not re.search(rb"^\s*(?:import|export)\b", r.content, re.M)  # a classic script, not a module
    assert r.text.rstrip().endswith("//# sourceMappingURL=sw.js.map")
    # Revalidated on every update check, whatever its ?v=: never kept as immutable.
    assert r.headers["cache-control"] == "private, no-cache" and r.headers["cdn-cache-control"] == "no-store"
    again = api.get("/sw.js?v=41", headers={**AUTH, "If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "private, no-cache"
    head = api.head("/sw.js?v=41", headers=AUTH)
    assert head.status_code == 200 and head.content == b"" and head.headers["content-length"] == str(len(compiled))
    m = api.get("/sw.js.map", headers=AUTH)
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/json")
    assert m.json()["sources"] == ["sw.ts"] and m.json()["sourcesContent"] == [(UI / "sw.ts").read_text()]
    assert api.get("/sw.js.map", headers={**AUTH, "If-None-Match": m.headers["etag"]}).status_code == 304


def _built(tmp_path, files):
    """A ui/ holding `files` and a build/ui whose manifest records them, as scripts/build-ui.cjs leaves it."""
    ui = tmp_path / "ui"
    for name, text in files.items():
        (ui / name).parent.mkdir(parents=True, exist_ok=True)
        (ui / name).write_text(text)
    out = ui_build.out_dir(ui)
    out.mkdir(parents=True)
    (out / "index.html").write_text("<p>built</p>\n")
    inputs = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}
    (out / "manifest.json").write_text(json.dumps({"build": "b1", "shell": [], "inputs": inputs}))
    return ui


def test_a_build_is_stale_when_any_input_changes_or_a_typescript_file_is_new(tmp_path):
    assert ui_build.stale(tmp_path / "none") == ["index.html"]
    ui = _built(
        tmp_path, {"index.html": "<p>page</p>\n", "app/a.ts": "const A: number = 1;\n", "styles/a.css": "a{}\n"}
    )
    assert ui_build.stale(ui) == [] and ui_build.build_id(ui) == "b1"
    ui_build.check(ui)
    (ui / "styles/a.css").write_text("a{color:red}\n")
    (ui / "app/b.ts").write_text("const B: number = 2;\n")
    (ui / "types").mkdir()
    (ui / "types/x.d.ts").write_text("declare const X: number;\n")  # types only: nothing to build
    assert ui_build.stale(ui) == ["app/b.ts", "styles/a.css"]
    with pytest.raises(ui_build.StaleBuild, match=r"ui/app/b\.ts, ui/styles/a\.css\): run `npm run build:ui`"):
        ui_build.check(ui)
    (ui / "app/a.ts").unlink()
    assert "app/a.ts" in ui_build.stale(ui)
    # The config's build id follows the manifest a rebuild writes.
    manifest = ui_build.out_dir(ui) / "manifest.json"
    manifest.write_text(json.dumps({"build": "b2", "inputs": {}}) + " ")
    assert ui_build.build_id(ui) == "b2"


def test_a_server_builds_a_stale_ui_with_the_node_on_path_or_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("VOLTA_HOME", raising=False)
    monkeypatch.delenv("NVM_BIN", raising=False)
    monkeypatch.setattr(ui_build, "NODE_DIRS", ("~/.volta/bin",))  # none of this computer's own Nodes
    ui = _built(tmp_path, {"index.html": "<p>page</p>\n"})
    (ui / "index.html").write_text("<p>edited</p>\n")
    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        inputs = {"index.html": hashlib.sha256(b"<p>edited</p>\n").hexdigest()}
        (ui_build.out_dir(ui) / "manifest.json").write_text(json.dumps({"build": "b2", "inputs": inputs}))

    with pytest.raises(ui_build.StaleBuild, match="build-ui.cjs is missing"):
        ui_build.prepare(ui, run, lambda _: "/usr/bin/node")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build-ui.cjs").write_text("")
    home = tmp_path / "home"
    with pytest.raises(ui_build.StaleBuild, match="no Node on PATH"):
        ui_build.prepare(ui, run, lambda _: None, home=home)
    with pytest.raises(ui_build.StaleBuild, match="npm ci"):
        ui_build.prepare(ui, run, lambda _: "/usr/bin/node")
    (tmp_path / "node_modules/esbuild").mkdir(parents=True)
    ui_build.prepare(ui, run, lambda _: "/usr/bin/node")
    assert ran == [["/usr/bin/node", str(tmp_path / "scripts/build-ui.cjs")]] and ui_build.stale(ui) == []
    ui_build.prepare(ui, run, lambda _: "/usr/bin/node")  # current: nothing runs
    assert len(ran) == 1


def test_the_bundle_names_a_browser_event_may_give_are_this_builds_scripts(tmp_path):
    from backend import support

    ui = _built(tmp_path, {"index.html": "<p>page</p>\n"})
    folder = ui_build.bundle_dir(ui)
    folder.mkdir()
    for name in ("app.bundle.0123456789abcdef.js", "app.bundle.task.0123456789abcdef.js", "app.bundle.a.css"):
        (folder / name).write_bytes(gzip.compress(b""))
    assert support.bundle_files(ui) == {"app.bundle.0123456789abcdef.js", "app.bundle.task.0123456789abcdef.js"}
    assert support.bundle_files(tmp_path / "none") == set()


def test_a_server_whose_path_has_no_node_finds_one_where_it_is_usually_installed(tmp_path, monkeypatch):
    # A launchd or systemd job keeps the PATH it was installed with: after an update makes the UI stale it must still
    # find the Node that Homebrew, Volta or nvm put elsewhere, or the server exits and stays down.
    monkeypatch.delenv("VOLTA_HOME", raising=False)
    monkeypatch.delenv("NVM_BIN", raising=False)
    monkeypatch.setattr(ui_build, "NODE_DIRS", ("~/.volta/bin",))
    home = tmp_path / "home"
    assert ui_build.node(lambda _: None, home) is None
    nvm = home / ".nvm/versions/node"
    for version in ("v20.11.0", "v22.18.0", "v9.0.0"):
        (nvm / version / "bin").mkdir(parents=True)
        (nvm / version / "bin/node").write_text("")
        (nvm / version / "bin/node").chmod(0o755)
    assert ui_build.node(lambda _: None, home) == str(nvm / "v22.18.0/bin/node")  # nvm's newest
    volta = home / ".volta/bin"
    volta.mkdir(parents=True)
    (volta / "node").write_text("")
    (volta / "node").chmod(0o755)
    assert ui_build.node(lambda _: None, home) == str(volta / "node")
    assert ui_build.node(lambda _: "/usr/bin/node", home) == "/usr/bin/node"  # PATH's first
    # And prepare builds with it.
    ui = _built(tmp_path, {"index.html": "<p>page</p>\n"})
    (ui / "index.html").write_text("<p>edited</p>\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build-ui.cjs").write_text("")
    (tmp_path / "node_modules/esbuild").mkdir(parents=True)
    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd[0])
        inputs = {"index.html": hashlib.sha256(b"<p>edited</p>\n").hexdigest()}
        (ui_build.out_dir(ui) / "manifest.json").write_text(json.dumps({"build": "b2", "inputs": inputs}))

    ui_build.prepare(ui, run, lambda _: None, home=home)
    assert ran == [str(volta / "node")] and ui_build.stale(ui) == []
