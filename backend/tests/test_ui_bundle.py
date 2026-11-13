"""The UI is served as one script and one stylesheet (backend/ui_bundle.py), in the order index.html lists them."""

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import ui_build, ui_bundle

AUTH = {"Authorization": "Bearer ana-test"}
# TICO_UI_BUNDLE=off runs the suite on the separate files: what is about the served bundle does not apply then.
served = pytest.mark.skipif(not ui_bundle.enabled(), reason="TICO_UI_BUNDLE=off")


# The routes are registered when the app is built, so the mode is set first: a test that takes bundle_mode before api
# gets an app built in that mode. The service worker routes are served in both modes.
@pytest.fixture(params=["on", "off"])
def bundle_mode(request, monkeypatch):
    monkeypatch.setenv("TICO_UI_BUNDLE", request.param)
    return request.param


@served
def test_the_page_names_one_versioned_script_and_one_stylesheet(api):
    r = api.get("/", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert re.findall(r'src="/tico/ui/app\.bundle\.js\?v=([0-9a-f]{16})"', r.text) and "bundle:" not in r.text
    assert re.findall(r'href="/tico/ui/app\.bundle\.css\?v=([0-9a-f]{16})"', r.text)
    assert "/tico/ui/app/" not in r.text and "/tico/ui/styles/" not in r.text
    assert r.headers["cache-control"] == "private, no-cache"  # the page is revalidated on every load
    assert (
        "script-src 'self';" in r.headers["content-security-policy"]
        and "sha256-" not in r.headers["content-security-policy"]
    )
    assert "<script>" not in r.text  # no inline code
    again = api.get("/", headers={**AUTH, "If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    assert (
        api.get("/", headers={**AUTH, "If-None-Match": "W/" + r.headers["etag"]}).status_code == 304
    )  # Cloudflare's weak form
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


def test_a_versioned_font_is_kept_and_an_unversioned_one_revalidates(api):
    # Inter's font-display is optional: a font the browser must revalidate first can miss the first paint.
    url = re.search(
        r'href="(/vendor/fonts/inter-variable\.woff2\?v=[0-9a-f]{12})"',
        (Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text(),
    ).group(1)
    kept = api.get(url, headers=AUTH)
    assert kept.status_code == 200 and kept.headers["content-type"] == "font/woff2"
    assert kept.headers["cache-control"] == ui_bundle.IMMUTABLE
    assert api.get(url.split("?")[0], headers=AUTH).headers["cache-control"] == "private, no-cache"


def served_file(ui, name):
    """The file the server sends for a standalone script: a TypeScript file's compiled output (backend/ui_build.py)."""
    return ui_build.compiled(ui, name) if name.endswith(".ts") else ui / name


def test_typescript_files_are_served_compiled_at_their_urls(api):
    # The separate-files mode names each script by its own URL, and each one sends the compiled output the bundle is cut from.
    ui = Path(ui_bundle.__file__).resolve().parent.parent / "ui"
    ui_build.prepare(ui)
    for url, name in (
        ("/tico/ui/theme.ts", "theme.ts"),
        ("/tico/ui/overview/bridge.ts", "overview/bridge.ts"),
    ):
        r = api.get(url, headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript"), url
        assert r.content == ui_build.compiled(ui, name).read_bytes(), url


@served
def test_the_standalone_scripts_are_named_by_content_and_kept_at_that_url(api):
    # theme.ts holds up the first paint and marked runs before the bundle: under the page's `?v=` neither needs a
    # round trip to revalidate. Another `v` (a page from before an edit) and no `v` revalidate as before.
    import hashlib

    page = api.get("/", headers=AUTH).text
    ui = Path(ui_bundle.__file__).resolve().parent.parent / "ui"
    for path, name in ui_bundle.STANDALONE.items():
        v = re.search(re.escape(path) + r'\?v=([0-9a-f]{16})"', page).group(1)
        assert v == hashlib.sha256(served_file(ui, name).read_bytes()).hexdigest()[:16]
        kept = api.get(f"{path}?v={v}", headers=AUTH)
        assert kept.status_code == 200 and kept.content == served_file(ui, name).read_bytes()
        assert kept.headers["cache-control"] == ui_bundle.IMMUTABLE
        assert api.get(f"{path}?v={v}", headers={**AUTH, "If-None-Match": kept.headers["etag"]}).status_code == 304
        assert api.get(f"{path}?v={'0' * 16}", headers=AUTH).headers["cache-control"] == "private, no-cache"
        assert api.get(path, headers=AUTH).headers["cache-control"] == "private, no-cache"
        assert api.get(f"{path}?v={v}").status_code == 401
    # The bundle marks where its own run starts, after the one strict directive (ui/app/boot-timing.ts).
    js = api.get("/tico/ui/app.bundle.js", headers=AUTH).text
    assert js.startswith("'use strict';\n" + ui_bundle.BUNDLE_MARK)


def test_a_change_to_a_listed_file_changes_the_url(tmp_path):
    ui = tmp_path / "ui"
    (ui / "app").mkdir(parents=True)
    (ui / "styles").mkdir()
    (ui / "app/a.js").write_text("'use strict';\nconst A = 1;\n")
    (ui / "app/b.js").write_text("'use strict';\nconst B = A + 1;\n")
    (ui / "styles/a.css").write_text("a{color:red}\n")
    (ui / "index.html").write_text(
        "<head>\n<!-- bundle:css:start -->\n<link rel=\"stylesheet\" href=\"/tico/ui/styles/a.css\">\n<!-- bundle:css:end -->\n</head>\n<body>\n"
        "<!-- bundle:js:start -->\n<script src=\"/tico/ui/app/b.js\"></script>\n<script src=\"/tico/ui/app/a.js\"></script>\n<!-- bundle:js:end -->\n</body>\n"
    )
    bundles = ui_bundle.UiBundle(ui)
    first = bundles.get()
    assert first.js.decode().index("// file: app/b.js") < first.js.decode().index(
        "// file: app/a.js"
    )  # index.html's order
    changed = ui / "app/a.js"
    before = changed.stat()
    changed.write_text("'use strict';\nconst A = 2;\n")
    os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    second = bundles.get()
    assert second.etag[ui_bundle.JS_PATH] != first.etag[ui_bundle.JS_PATH] and second.page != first.page
    (ui / "app/a.js").write_text("const A = 3;\n")  # not strict: it cannot share a script
    assert bundles.get() is None


@served
def test_the_bundle_is_cut_into_parts_a_webkit_cache_keeps(api):
    # WebKit's disk cache keeps no response larger than an eighth of its capacity (NetworkResourceLoader
    # maximumCacheBufferSize): the one 4.3 MB script was downloaded again on every open of the desktop app.
    page = api.get("/", headers=AUTH).text
    for kind in ("js", "css"):
        tag = (
            r'src="(/tico/ui/app\.bundle(?:\.\d+)?\.js)\?v=([0-9a-f]{16})"'
            if kind == "js"
            else r'href="(/tico/ui/app\.bundle(?:\.\d+)?\.css)\?v=([0-9a-f]{16})"'
        )
        parts = re.findall(tag, page)
        assert [p for p, _ in parts] == [ui_bundle.part_path(kind, n) for n in range(len(parts))]
        assert len({v for _, v in parts}) == 1  # one build: every part names the whole bundle
        whole = b""
        for path, v in parts:
            r = api.get(f"{path}?v={v}", headers={**AUTH, "Accept-Encoding": "identity"})
            assert r.status_code == 200 and r.headers["cache-control"] == ui_bundle.IMMUTABLE
            assert len(r.content) <= ui_bundle.PART_BYTES + 64 * 1024, path  # a file is never cut
            assert (
                api.get(
                    path, headers={**AUTH, "Accept-Encoding": "identity", "If-None-Match": r.headers["etag"]}
                ).status_code
                == 304
            )
            assert api.get(path).status_code == 401
            if kind == "js":
                assert r.text.startswith("'use strict';\n")  # each part is a strict script of its own
            whole += r.content
        assert v == ui_bundle._etag(whole)[1:17]
    assert len(re.findall(r"app\.bundle(?:\.\d+)?\.js\?v=", page)) > 1  # today's 4 MB script is more than one part
    assert api.get("/tico/ui/app.bundle.99.js", headers=AUTH).status_code == 404


@served
def test_an_old_bundle_url_is_complete_and_never_cached(api, monkeypatch):
    for kind in ("js", "css"):
        for n in (0, 1):
            try:
                an_old_bundle_url_is_complete_and_never_cached(api, monkeypatch, kind, n)
            except AssertionError as exc:
                exc.add_note(f"in the {kind} bundle, n={n}")
                raise


def an_old_bundle_url_is_complete_and_never_cached(api, monkeypatch, kind, n):
    bundle = ui_bundle.Bundle(
        b"",
        [b"'use strict';\nconst A = 1;\n", b"'use strict';\nconst B = A + 1;\n"],
        [b"a{color:red}\n", b"b{color:blue}\n"],
    )
    monkeypatch.setattr(ui_bundle.UiBundle, "get", lambda self: bundle)
    path = ui_bundle.part_path(kind, n)
    old = f"{path}?v=old-build"
    expected = getattr(bundle, kind) if n == 0 else bundle.parts[path]
    with patch.object(ui_bundle.gzip, "compress", wraps=ui_bundle.gzip.compress) as compress:
        plain = api.get(old, headers={**AUTH, "Accept-Encoding": "identity"})
        assert plain.status_code == 200 and plain.content == expected
        assert plain.headers["cache-control"] == "no-store" and "content-encoding" not in plain.headers
        assert plain.headers["etag"].startswith('"') and plain.headers["vary"] == "Accept-Encoding"
        if n == 0:
            assert plain.headers["etag"] != bundle.etag[path]
        # Current and unversioned URLs still serve just this part, with the same cache policy and tags.
        for suffix in (f"?v={bundle.version[kind]}", ""):
            kept = api.get(path + suffix, headers={**AUTH, "Accept-Encoding": "identity"})
            assert kept.status_code == 200 and kept.content == bundle.parts[path]
            assert kept.headers["cache-control"] == ui_bundle.IMMUTABLE and kept.headers["etag"] == bundle.etag[path]
            zipped_part = api.get(path + suffix, headers={**AUTH, "Accept-Encoding": "gzip"})
            assert zipped_part.content == bundle.parts[path] and zipped_part.headers["content-encoding"] == "gzip"
            assert zipped_part.headers["cache-control"] == ui_bundle.IMMUTABLE
            assert zipped_part.headers["etag"] == bundle.gz_etag[path]
        compress.assert_not_called()
        assert bundle.whole_gz == {}
        zipped = api.get(old, headers={**AUTH, "Accept-Encoding": "gzip"})
        assert zipped.status_code == 200 and zipped.content == expected  # TestClient decodes gzip
        assert zipped.headers["cache-control"] == "no-store" and zipped.headers["content-encoding"] == "gzip"
        assert zipped.headers["etag"].startswith('"') and zipped.headers["etag"] != plain.headers["etag"]
        if n == 0:
            assert zipped.headers["etag"] != bundle.gz_etag[path]
        again = api.get(old, headers={**AUTH, "Accept-Encoding": "gzip"})
        assert again.content == expected and again.headers["etag"] == zipped.headers["etag"]
        unchanged = api.get(old, headers={**AUTH, "Accept-Encoding": "gzip", "If-None-Match": zipped.headers["etag"]})
        assert unchanged.status_code == 304 and unchanged.headers["cache-control"] == "no-store"
        assert compress.call_count == (1 if n == 0 else 0)


def test_parts_are_cut_between_files_in_order():
    chunks = [b"a" * 300, b"b" * 300, b"c" * 700, b"d" * 10]
    assert ui_bundle.split(chunks, limit=600) == [b"a" * 300 + b"b" * 300, b"c" * 700, b"d" * 10]
    assert ui_bundle.split([b"x"], limit=600) == [b"x"]
    assert [ui_bundle.part_path("js", n) for n in range(3)] == [
        "/tico/ui/app.bundle.js",
        "/tico/ui/app.bundle.2.js",
        "/tico/ui/app.bundle.3.js",
    ]
    assert ui_bundle.is_part("/tico/ui/app.bundle.12.css") and not ui_bundle.is_part("/tico/ui/app.bundle.1.js")
    assert not ui_bundle.is_part("/tico/ui/app.bundle.js.map")


def test_a_typescript_file_runs_only_as_its_current_compiled_output(tmp_path):
    # A .ts file the page lists is bundled as build/ui's compiled JavaScript, and only while build/ui/manifest.json
    # matches the source and the output: a missing or stale build is refused, never served as old JavaScript.
    import hashlib
    import json

    ui = tmp_path / "ui"
    (ui / "app").mkdir(parents=True)
    (ui / "styles").mkdir()
    (ui / "app/a.js").write_text("'use strict';\nconst A = 1;\n")
    (ui / "app/b.ts").write_text("'use strict';\nconst B: number = A + 1;\n")
    (ui / "styles/a.css").write_text("a{color:red}\n")
    (ui / "index.html").write_text(
        "<head>\n<!-- bundle:css:start -->\n<link rel=\"stylesheet\" href=\"/tico/ui/styles/a.css\">\n<!-- bundle:css:end -->\n</head>\n<body>\n"
        "<!-- bundle:js:start -->\n<script src=\"/tico/ui/app/a.js\"></script>\n<script src=\"/tico/ui/app/b.ts\"></script>\n<!-- bundle:js:end -->\n</body>\n"
    )
    bundles = ui_bundle.UiBundle(ui)
    with pytest.raises(ui_build.StaleBuild, match=r"ui/app/b\.ts .*npm run build:ui"):
        bundles.get()
    out = tmp_path / "build/ui"
    (out / "app").mkdir(parents=True)

    def sha(text):
        return hashlib.sha256(text.encode()).hexdigest()

    def compile_(source, output):  # what scripts/build-ui.cjs writes
        (out / "app/b.js").write_text(output)
        (out / "manifest.json").write_text(
            json.dumps({"files": {"app/b.ts": {"source": sha(source), "output": sha(output)}}})
        )

    compile_((ui / "app/b.ts").read_text(), '"use strict";\nconst B = A + 1;\n//# sourceMappingURL=b.js.map\n')
    first = bundles.get().js.decode()
    assert '// file: app/b.ts\n"use strict";\nconst B = A + 1;' in first and "B: number" not in first
    # The map line is the file served alone's, so a bundle part leaves it out.
    assert "sourceMappingURL" not in first
    assert ui_build.stale(ui) == []
    # The source changed after the build: the old output is refused, and the bundle built from it is not kept.
    edited = ui / "app/b.ts"
    before = edited.stat()
    edited.write_text("'use strict';\nconst B: number = A + 2;\n")
    os.utime(edited, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    with pytest.raises(ui_build.StaleBuild):
        bundles.get()
    assert ui_build.stale(ui) == ["app/b.ts"] and ui_bundle.build_id(ui) == ""
    # An output edited by hand is refused as well.
    compile_(edited.read_text(), '"use strict";\nconst B = A + 2;\n')
    assert "A + 2" in bundles.get().js.decode()
    (out / "app/b.js").write_text('"use strict";\nconst B = 0;\n')
    with pytest.raises(ui_build.StaleBuild):
        ui_build.check(ui)


def test_a_typescript_file_is_served_as_its_compiled_javascript(api):
    # With the files served separately (TICO_UI_BUNDLE=off), the page asks for the .ts name it lists.
    name = next(n for n in ui_build.sources(ui_build.ROOT / "ui") if n.startswith("app/"))
    r = api.get(f"/tico/ui/{name}", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.content == ui_build.compiled(ui_build.ROOT / "ui", name).read_bytes()
    assert api.get(f"/tico/ui/{name}").status_code == 401
    assert api.get("/tico/ui/app/no-such-file.ts", headers=AUTH).status_code == 404
    # Its source map sits beside it under the name its last line gives, and names the .ts with the source text.
    js = r.content.decode()
    assert js.rstrip().endswith(f"//# sourceMappingURL={Path(name).stem}.js.map")
    m = api.get(f"/tico/ui/{name[:-3]}.js.map", headers=AUTH)
    assert m.status_code == 200 and m.json()["sources"] == [Path(name).name]
    assert m.json()["sourcesContent"] == [(ui_build.ROOT / "ui" / name).read_text()]
    assert api.get(f"/tico/ui/{name[:-3]}.js.map").status_code == 401
    assert api.get("/tico/ui/app/no-such-file.js.map", headers=AUTH).status_code == 404


def test_a_stale_map_is_refused(tmp_path):
    import hashlib
    import json

    ui, out = tmp_path / "ui", tmp_path / "build/ui"
    (ui / "app").mkdir(parents=True)
    (out / "app").mkdir(parents=True)
    (ui / "app/b.ts").write_text("const B: number = 1;\n")
    (out / "app/b.js").write_text("const B = 1;\n//# sourceMappingURL=b.js.map\n")
    (out / "app/b.js.map").write_text('{"sources":["b.ts"]}')

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    (out / "manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    "app/b.ts": {
                        "source": sha(ui / "app/b.ts"),
                        "output": sha(out / "app/b.js"),
                        "map": sha(out / "app/b.js.map"),
                    }
                }
            }
        )
    )
    assert ui_build.source_map(ui, "app/b.ts") == out / "app/b.js.map"
    assert ui_build.without_map_line((out / "app/b.js").read_bytes()) == b"const B = 1;\n"
    (out / "app/b.js.map").write_text('{"sources":["other.ts"]}')
    with pytest.raises(ui_build.StaleBuild, match="source map"):
        ui_build.source_map(ui, "app/b.ts")


def test_a_server_finds_node_off_path_or_says_what_is_missing(tmp_path, monkeypatch):
    # A launchd service's PATH has only the system folders: Node is looked for where it is usually installed.
    monkeypatch.delenv("NVM_BIN", raising=False)
    monkeypatch.delenv("VOLTA_HOME", raising=False)
    root = tmp_path / "checkout"
    (root / "ui/app").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "scripts/build-ui.cjs").write_text("")
    (root / "ui/app/b.ts").write_text("const B: number = 1;\n")
    home = tmp_path / "home"
    nvm = home / ".nvm/versions/node"
    for version in ("v20.1.0", "v22.3.0", "v22.11.0", "v24.0.0"):
        (nvm / version / "bin").mkdir(parents=True)
        (nvm / version / "bin/node").write_text("")
        (nvm / version / "bin/node").chmod(0o755)
    (home / ".nvm/alias").mkdir()
    (home / ".nvm/alias/default").write_text("22\n")
    tried = []

    def run(cmd, **_):
        tried.append(cmd[0])

    no_path = lambda name: None  # noqa: E731
    with pytest.raises(ui_build.StaleBuild, match=r"no Node on PATH or in /nowhere"):
        ui_build.prepare(root / "ui", run, no_path, dirs=("/nowhere",), home=tmp_path / "empty")
    with pytest.raises(ui_build.StaleBuild, match=r"node_modules/esbuild is missing: run `npm ci`"):
        ui_build.prepare(root / "ui", run, no_path, dirs=(), home=home)
    assert ui_build.nodes(no_path, (), home) == [str(nvm / "v22.11.0/bin/node")]
    (root / "node_modules/esbuild").mkdir(parents=True)
    with pytest.raises(ui_build.StaleBuild, match=r"npm run build:ui.*building with .*v22\.11\.0/bin/node failed"):
        ui_build.prepare(root / "ui", run, no_path, dirs=(), home=home)
    assert tried == [str(nvm / "v22.11.0/bin/node")]

    # A Node that builds it is enough; the first that does wins.
    def build(cmd, **_):
        import hashlib
        import json

        tried.append(cmd[0])
        out = root / "build/ui"
        (out / "app").mkdir(parents=True, exist_ok=True)
        (out / "app/b.js").write_text("const B = 1;\n")
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
        (out / "manifest.json").write_text(
            json.dumps({"files": {"app/b.ts": {"source": sha(root / "ui/app/b.ts"), "output": sha(out / "app/b.js")}}})
        )

    volta = home / ".volta/bin"
    volta.mkdir(parents=True)
    (volta / "node").write_text("")
    (volta / "node").chmod(0o755)
    tried.clear()
    ui_build.prepare(root / "ui", build, no_path, dirs=("~/.volta/bin",), home=home)
    assert tried == [str(volta / "node")] and ui_build.stale(root / "ui") == []


def test_the_service_worker_is_served_at_the_root_as_its_compiled_classic_script(bundle_mode, api):
    # ui/app/boot.ts registers /sw.js?v=… (scope /): the URL stays, the body is ui/sw.ts compiled, in both bundle modes.
    compiled = ui_build.compiled(ui_build.ROOT / "ui", "sw.ts").read_bytes()
    r = api.get("/sw.js?v=41", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "text/javascript; charset=utf-8"
    assert r.content == compiled and b"ServiceWorkerGlobalScope" not in r.content  # JavaScript, not the .ts source
    assert not re.search(rb"^\s*(?:import|export)\b", r.content, re.M)  # a classic script, not a module
    assert r.text.rstrip().endswith("//# sourceMappingURL=sw.js.map")
    # Revalidated on every update check, whatever its ?v=: never kept as immutable.
    assert r.headers["cache-control"] == "private, no-cache" and r.headers["cdn-cache-control"] == "no-store"
    assert r.headers["etag"].startswith('W/"') and r.headers["vary"] == "Accept-Encoding"
    assert api.get("/sw.js", headers=AUTH).content == compiled
    again = api.get("/sw.js?v=41", headers={**AUTH, "If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "private, no-cache"
    head = api.head("/sw.js?v=41", headers=AUTH)
    assert head.status_code == 200 and head.content == b"" and head.headers["content-length"] == str(len(compiled))
    assert head.headers["etag"] == r.headers["etag"]
    # Its source map, at the root name the last line gives, names sw.ts with its text.
    m = api.get("/sw.js.map", headers=AUTH)
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/json")
    assert m.json()["sources"] == ["sw.ts"]
    assert m.json()["sourcesContent"] == [(ui_build.ROOT / "ui/sw.ts").read_text()]
    assert api.head("/sw.js.map", headers=AUTH).status_code == 200
    # Conditional like sw.js: the map's own ETag answers 304, and its JSON type is kept.
    map_again = api.get("/sw.js.map", headers={**AUTH, "If-None-Match": m.headers["etag"]})
    assert map_again.status_code == 304 and map_again.content == b""
    assert map_again.headers["etag"] == m.headers["etag"]


def test_the_service_worker_gets_a_new_etag_when_its_output_changes(bundle_mode, api, tmp_path, monkeypatch):
    out = tmp_path / "sw.js"
    out.write_text("self.addEventListener('install', () => {});\n")
    monkeypatch.setattr(ui_build, "compiled", lambda ui_dir, name: out)
    first = api.get("/sw.js?v=41", headers=AUTH)
    assert first.status_code == 200 and first.content == out.read_bytes()
    before = out.stat()
    out.write_text("self.addEventListener('install', () => { self.skipWaiting(); });\n")
    os.utime(out, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    second = api.get("/sw.js?v=41", headers={**AUTH, "If-None-Match": first.headers["etag"]})
    assert second.status_code == 200 and second.content == out.read_bytes()
    assert second.headers["etag"] != first.headers["etag"]


def test_a_stale_service_worker_build_is_a_503_never_the_source(bundle_mode, api, monkeypatch):
    def stale(ui_dir, name, manifest=None):
        raise ui_build.StaleBuild(f"ui/{name} has no up-to-date compiled JavaScript in build/ui")

    monkeypatch.setattr(ui_build, "compiled", stale)
    monkeypatch.setattr(ui_build, "source_map", stale)
    for path in ("/sw.js?v=41", "/sw.js", "/sw.js.map"):
        r = api.get(path, headers=AUTH)
        assert r.status_code == 503 and r.json()["error"]["code"] == "ui_not_built", path
        assert "addEventListener" not in r.text, path
