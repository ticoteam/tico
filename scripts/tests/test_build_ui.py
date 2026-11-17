"""scripts/build-ui.cjs, the web UI's build: run on a copy in a temporary tree (esbuild from this checkout's
node_modules). What it writes is all the server serves of the UI (backend/ui_bundle_routes.py), so the page's behaviour
rests on it: the scripts in index.html's order sharing one strict scope, the page parts cut out with the shell's
stand-ins for their entries, every file named by its content, and a manifest backend/ui_build.py reads."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import ui_build

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None or not (ROOT / "node_modules/esbuild").is_dir(), reason="needs node and the repository's npm ci"
)
PAGE = """<head>
<script src="/tico/ui/theme.ts"></script>
<!-- bundle:css:start -->
<link rel="stylesheet" href="/tico/ui/styles/a.css">
<link rel="stylesheet" href="/tico/ui/styles/b.css">
<!-- bundle:css:end -->
</head>
<body>
<script src="/vendor/marked.min.js"></script>
<!-- bundle:js:start -->
{scripts}<!-- bundle:js:end -->
</body>
"""
FILES = {
    "theme.ts": "const THEME: string = 'dark';\ndocument.documentElement.dataset.theme = THEME;\n",
    "vendor/marked.min.js": "/* marked */\n!function(){window.marked={}}();\n",
    "styles/a.css": "a {\n  color: red;\n}\n",
    "styles/b.css": "b {\n  color: blue;\n}\n",
    "app/b.ts": "'use strict';\nconst BETA: number = ALPHA + 1;\nfunction pageBeta(): number { return BETA; }\nfunction betaStop(): void {}\n",
    "app/a.ts": "'use strict';\nconst ALPHA: number = 1;\nfunction alphaGo(): void { pageBeta(); betaStop(); }\n",
    "app/c.ts": "'use strict';\nfunction gammaHelp(): number {\n  return BETA + 1;\n}\n",
    "overview/bridge.ts": "export const BRIDGE: number = 1;\n",
    "sw.ts": "const worker = self;\n",
}
PARTS = {
    "//": "a comment",
    "pb": {"files": ["app/b.ts"], "pages": ["pageBeta"], "idle": ["betaStop"]},
    "pc": {"files": ["app/c.ts"], "deps": ["pb"], "calls": ["gammaHelp"], "events": ["[data-gamma]"]},
}


def tree(tmp_path, files=None, parts=PARTS, order=("app/a.ts", "app/b.ts", "app/c.ts")):
    files = {**FILES, **(files or {})}
    scripts = "".join(f'<script src="/tico/ui/{name}"></script>\n' for name in order)
    files["index.html"] = PAGE.format(scripts=scripts)
    for rel, text in files.items():
        (tmp_path / "ui" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "ui" / rel).write_text(text)
    if parts is not None:
        (tmp_path / "ui/parts.json").write_text(json.dumps(parts))
    (tmp_path / "scripts").mkdir(exist_ok=True)
    shutil.copy(ROOT / "scripts/build-ui.cjs", tmp_path / "scripts/build-ui.cjs")
    return tmp_path / "ui"


def build(ui, check=True):
    env = {**os.environ, "NODE_PATH": str(ROOT / "node_modules")}
    done = subprocess.run(
        [NODE, str(ui.parent / "scripts/build-ui.cjs")], capture_output=True, text=True, env=env, check=False
    )
    assert not check or done.returncode == 0, done.stderr
    return done


def page(ui):
    return (ui_build.out_dir(ui) / "index.html").read_text()


def table(ui):
    return json.loads(re.search(r'<script type="application/json" id="ui-parts">(.*?)</script>', page(ui)).group(1))


def scripts(ui):
    """The bundle scripts the page runs, in order, each as its text."""
    names = re.findall(r'<script src="/tico/ui/(app\.bundle\.[0-9a-f]{16}\.js)"', page(ui))
    return [(ui_build.bundle_dir(ui) / n).read_text() for n in names]


def test_the_shell_runs_the_files_in_index_html_order_with_stand_ins_for_the_page_parts(tmp_path):
    ui = tree(tmp_path, order=("app/b.ts", "app/a.ts", "app/c.ts"), parts={})
    build(ui)
    (shell,) = scripts(ui)
    assert shell.index("// file: app/b.ts") < shell.index("// file: app/a.ts") < shell.index("// file: app/c.ts")
    assert "BETA: number" not in shell and "const BETA=ALPHA+1" in shell  # types stripped, minified
    assert shell.startswith("'use strict';\ntry { performance.mark('tico:bundle-start');")
    assert table(ui) == {"build": table(ui)["build"], "parts": {}}
    # With page parts: their files leave the shell, and the shell declares each entry before any file runs.
    ui = tree(tmp_path)
    build(ui)
    (shell,) = scripts(ui)
    assert "// file: app/a.ts" in shell and "// file: app/b.ts" not in shell and "// file: app/c.ts" not in shell
    assert 'function pageBeta(...a) { return uiPartCall("pb", "pageBeta", a, true); }' in shell
    assert 'function gammaHelp(...a) { return uiPartCall("pc", "gammaHelp", a, false); }' in shell
    assert "function betaStop() {}" in shell and "const UI_PART_STUBS = new Set([pageBeta, gammaHelp]);" in shell
    assert shell.index("function pageBeta") < shell.index("// file: app/a.ts")
    parts = table(ui)["parts"]
    assert parts["pc"]["load"] == ["pb", "pc"] and parts["pc"]["events"] == ["[data-gamma]"]
    assert "events" not in parts["pb"]
    name = parts["pb"]["url"].removeprefix("/tico/ui/")
    assert re.fullmatch(r"app\.bundle\.pb\.[0-9a-f]{16}\.js", name)
    body = (ui_build.bundle_dir(ui) / name).read_text()
    assert body.startswith("'use strict';\n\n// file: app/b.ts\n") and body.endswith(
        f"//# sourceMappingURL={name}.map\n"
    )
    # theme.ts and marked load where the page named them, by their content's names.
    html = page(ui)
    assert re.search(r'<head>\n<script src="/tico/ui/app\.bundle\.theme\.[0-9a-f]{16}\.js"></script>', html)
    assert re.search(r'<body>\n<script src="/tico/ui/app\.bundle\.marked\.[0-9a-f]{16}\.js"></script>', html)
    assert html.count('<link rel="stylesheet"') == 1 and "bundle:" not in html
    css = re.search(r'href="/tico/ui/(app\.bundle\.[0-9a-f]{16}\.css)"', html).group(1)
    text = (ui_build.bundle_dir(ui) / css).read_text()
    assert text.index("/* file: styles/a.css */") < text.index("/* file: styles/b.css */")
    # The compiled files at their own paths (the overview's modules, the worker, the unit tests read them).
    assert (ui_build.out_dir(ui) / "overview/bridge.js").read_text().startswith("export const BRIDGE = 1;")
    assert (ui_build.out_dir(ui) / "sw.js.map").is_file()


def test_each_file_is_named_by_its_content_and_the_manifest_tells_a_stale_build(tmp_path):
    ui = tree(tmp_path)
    build(ui)
    first, shell, parts = page(ui), scripts(ui), table(ui)
    manifest = json.loads((ui_build.out_dir(ui) / "manifest.json").read_text())
    assert manifest["build"] == parts["build"] and ui_build.build_id(ui) == parts["build"]
    assert set(manifest["inputs"]) == {*FILES, "index.html", "parts.json"}
    assert ui_build.stale(ui) == []
    for name in os.listdir(ui_build.bundle_dir(ui)):
        if not name.endswith(".gz"):
            assert (ui_build.bundle_dir(ui) / f"{name}.gz").is_file(), name
    # A page part's file changed: that part's name changes, the shell and the other part keep theirs.
    (ui / "app/b.ts").write_text(FILES["app/b.ts"].replace("ALPHA + 1", "ALPHA + 2"))
    assert ui_build.stale(ui) == ["app/b.ts"]
    build(ui)
    assert table(ui)["parts"]["pb"]["url"] != parts["parts"]["pb"]["url"]
    assert table(ui)["parts"]["pc"]["url"] == parts["parts"]["pc"]["url"] and scripts(ui) == shell
    assert table(ui)["build"] != parts["build"] and page(ui) != first
    # The older build's files are gone: an open tab asking for one reloads to the current build (ui/app/parts.ts).
    assert not (ui_build.bundle_dir(ui) / parts["parts"]["pb"]["url"].removeprefix("/tico/ui/")).exists()
    # A shell change keeps every part's address.
    (ui / "app/a.ts").write_text(FILES["app/a.ts"].replace("= 1", "= 5"))
    build(ui)
    assert scripts(ui) != shell and table(ui)["parts"]["pc"]["url"] == parts["parts"]["pc"]["url"]
    # A new TypeScript file is stale until built, though nothing lists it yet.
    (ui / "app/d.ts").write_text("const D: number = 1;\n")
    assert ui_build.stale(ui) == ["app/d.ts"]


def _noise(n, seed):
    raw = b"".join(hashlib.sha256(f"{seed}:{i}".encode()).digest() for i in range(n // 32 + 1))
    return f"const PAD_{seed} = '{base64.b64encode(raw).decode()[:n]}';\n"


def test_the_shell_is_cut_between_files_and_a_part_over_the_limit_is_refused(tmp_path):
    # WebKit's disk cache keeps no file over an eighth of its size: no part is over 512 KB, and no file is cut.
    big = {"app/a.ts": FILES["app/a.ts"] + _noise(300_000, "a"), "app/x.ts": "'use strict';\n" + _noise(300_000, "x")}
    ui = tree(tmp_path, big, order=("app/a.ts", "app/x.ts", "app/b.ts", "app/c.ts"))
    build(ui)
    first, second = scripts(ui)
    assert "// file: app/a.ts" in first and "// file: app/x.ts" in second and second.startswith("'use strict';\n\n")
    assert "uiPartCall" not in second  # the stand-ins are in the first script only
    # The same in a page part costs a cold open nothing, but one over the limit is refused.
    shell_before = ui_build.cold_js(ui)
    ui = tree(tmp_path, {**big, "app/b.ts": FILES["app/b.ts"] + _noise(600_000, "b")})
    done = build(ui, check=False)
    assert done.returncode == 1 and "pb is over 524288 bytes" in done.stderr
    ui = tree(
        tmp_path,
        {**big, "app/b.ts": FILES["app/b.ts"] + _noise(100_000, "b")},
        order=("app/a.ts", "app/x.ts", "app/b.ts", "app/c.ts"),
    )
    build(ui)
    assert ui_build.cold_js(ui) == shell_before


@pytest.mark.parametrize(
    "files, parts, why",
    [
        ({"app/a.ts": "const ALPHA: number = 1;\n"}, PARTS, "ui/app/a.ts: not strict"),
        ({}, {"pb": {"files": ["app/nope.ts"]}}, "app/nope.ts is not a script index.html bundles"),
        ({}, {"pb": {"files": ["app/b.ts"]}, "pc": {"files": ["app/b.ts"]}}, "two parts"),
        ({}, {"pb": {"files": ["app/b.ts"], "pages": ["page B"]}}, "page B"),
        ({}, {"Bad": {"files": ["app/b.ts"]}}, "Bad"),
        ({}, {"theme": {"files": ["app/b.ts"]}}, "theme"),
        ({}, {"pb": {"files": ["app/b.ts"], "deps": ["px"]}}, "px, which is not a part"),
        ({}, {"pb": {"files": ["app/b.ts"], "events": [""]}}, "CSS selectors"),
        # Listed before a part it needs: its script would run first, and a call in between finds that part missing.
        (
            {},
            {"pb": {"files": ["app/b.ts"], "deps": ["pc"]}, "pc": {"files": ["app/c.ts"]}},
            "pc must be listed before",
        ),
    ],
)
def test_a_file_that_cannot_share_the_script_or_a_wrong_parts_list_is_refused(tmp_path, files, parts, why):
    done = build(tree(tmp_path, files, parts), check=False)
    assert done.returncode == 1 and why in done.stderr, done.stderr
    assert not (tmp_path / "build/ui/manifest.json").exists()


def test_parts_that_need_each_other_load_together_in_their_listed_order(tmp_path):
    ui = tree(
        tmp_path, parts={"pb": {"files": ["app/b.ts"], "deps": ["pc"]}, "pc": {"files": ["app/c.ts"], "deps": ["pb"]}}
    )
    build(ui)
    assert table(ui)["parts"]["pb"]["load"] == table(ui)["parts"]["pc"]["load"] == ["pb", "pc"]


def test_a_frame_in_the_bundle_maps_back_to_its_source(tmp_path):
    ui = tree(tmp_path, parts={})
    build(ui)
    name = re.search(r'<script src="/tico/ui/(app\.bundle\.[0-9a-f]{16}\.js)"', page(ui)).group(1)
    lines = (ui_build.bundle_dir(ui) / name).read_text().split("\n")
    line = next(i for i, t in enumerate(lines) if "function gammaHelp" in t)
    column = lines[line].index("function gammaHelp") + len("function ")
    assert ui_build.where(ui, f"https://x.example/tico/ui/{name}:{line + 1}:{column + 1}") == "app/c.ts:2:10"
    assert ui_build.where(ui, f"{name}:1:1") is None  # the stand-ins and the mark: no source file
