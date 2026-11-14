"""The JavaScript a cold open of Home downloads stays under its ceiling (backend/ui_bundle.py SHELL_JS_CEILING, P05b).

scripts/pr_checks.py runs this file for every pull request that changes the UI, the bundler or the UI's build
(rule 9, UI_SHELL_SIZE), so a change that pushes the shell past the ceiling fails before it merges."""

import base64
import hashlib
import json
from pathlib import Path

from backend import ui_bundle

ROOT = Path(__file__).resolve().parents[2]


def test_a_cold_open_of_home_stays_within_its_ceiling():
    bundle, _ = ui_bundle.build(ROOT / "ui")
    assert ui_bundle.shell_over(bundle) is None, ui_bundle.shell_over(bundle)
    # The budget is 300 KB gzip (P05b): the ceiling only ever comes down to it.
    assert ui_bundle.SHELL_JS_CEILING <= 300 * 1024


def test_the_measure_is_every_script_a_cold_open_downloads():
    bundle, _ = ui_bundle.build(ROOT / "ui")
    sizes = ui_bundle.cold_js(bundle)
    page = bundle.page.decode()
    # Each script tag the page runs (not the JSON parts list), and nothing the page fetches only later.
    import re

    named = {src.split("?")[0] for src in re.findall(r'<script src="([^"]+)"', page)}
    assert set(sizes) == named
    assert not any(ui_bundle.is_part(p) and "." in p[len(ui_bundle.PREFIX) + len("app.bundle.") :][:-3] for p in sizes)
    # The parts as the server sends them (Bundle.gz), so the number is what a browser downloads.
    for path in sizes:
        if path in bundle.gz:
            assert sizes[path] == len(bundle.gz[path])


def _noise(n, seed):
    """n bytes of JavaScript that gzip cannot shrink much: a string literal of base64."""
    raw = b"".join(hashlib.sha256(f"{seed}:{i}".encode()).digest() for i in range(n // 32 + 1))
    return f"const PAD_{seed} = '{base64.b64encode(raw).decode()[:n]}';\n"


def _ui(tmp_path, shell_pad=0, part_pad=0):
    """A ui/ with a shell file (a.js, padded by shell_pad bytes), a page part (b.js, padded by part_pad) and marked."""
    ui = tmp_path / "ui"
    (ui / "app").mkdir(parents=True, exist_ok=True)
    (ui / "styles").mkdir(exist_ok=True)
    (ui / "vendor").mkdir(exist_ok=True)
    (ui / "app/a.js").write_text(
        "'use strict';\nfunction aGo() { pageB(); }\n" + (_noise(shell_pad, "a") if shell_pad else "")
    )
    (ui / "app/b.js").write_text(
        "'use strict';\nfunction pageB() { return 1; }\n" + (_noise(part_pad, "b") if part_pad else "")
    )
    (ui / "vendor/marked.min.js").write_text("'use strict';\n" + _noise(4000, "m"))
    (ui / "styles/a.css").write_text("a{color:red}\n")
    (ui / "index.html").write_text(
        '<head>\n<script src="/vendor/marked.min.js"></script>\n<!-- bundle:css:start -->\n'
        '<link rel="stylesheet" href="/tico/ui/styles/a.css">\n<!-- bundle:css:end -->\n</head>\n<body>\n'
        '<!-- bundle:js:start -->\n<script src="/tico/ui/app/a.js"></script>\n<script src="/tico/ui/app/b.js"></script>\n'
        "<!-- bundle:js:end -->\n</body>\n"
    )
    (ui / "parts.json").write_text(json.dumps({"pb": {"files": ["app/b.js"], "pages": ["pageB"]}}))
    return ui


def test_the_ceiling_passes_at_its_size_and_fails_one_byte_under(tmp_path):
    bundle, _ = ui_bundle.build(_ui(tmp_path, shell_pad=3000))
    sizes = ui_bundle.cold_js(bundle)
    # The shell's part and marked (compressed as backend/ui_compression.py sends it); the page part is not downloaded.
    assert set(sizes) == {"/tico/ui/app.bundle.js", "/vendor/marked.min.js"}
    total = sum(sizes.values())
    assert ui_bundle.shell_over(bundle, total) is None
    message = ui_bundle.shell_over(bundle, total - 1)
    assert message and f"{total / 1024:.1f} KB" in message and f"{(total - 1) / 1024:.1f} KB" in message
    assert "app/a.js" in message and "ui/parts.json" in message


def test_a_padded_shell_file_goes_over_and_padding_a_page_part_does_not(tmp_path):
    before, _ = ui_bundle.build(_ui(tmp_path / "before"))
    ceiling = sum(ui_bundle.cold_js(before).values()) + 1024
    # 1 KB under the ceiling: passes.
    under, _ = ui_bundle.build(_ui(tmp_path / "under", shell_pad=600))
    assert ui_bundle.shell_over(under, ceiling) is None
    # 2 KB more code in the shell: over, and the message names the file that grew.
    over, _ = ui_bundle.build(_ui(tmp_path / "over", shell_pad=3000))
    message = ui_bundle.shell_over(over, ceiling)
    assert message and "app/a.js" in message
    # The same code in a page part costs a cold open nothing.
    parted, _ = ui_bundle.build(_ui(tmp_path / "parted", part_pad=3000))
    assert ui_bundle.shell_over(parted, ceiling) is None


def test_a_shell_over_its_ceiling_is_still_served(tmp_path, monkeypatch):
    # The ceiling stops a merge, never a running server: the bundle builds whatever its size.
    monkeypatch.setattr(ui_bundle, "SHELL_JS_CEILING", 1)
    bundle = ui_bundle.UiBundle(_ui(tmp_path, shell_pad=3000)).get()
    assert bundle is not None and b"function aGo" in bundle.js
    assert ui_bundle.shell_over(bundle)


def test_the_size_report_prints_each_script_and_fails_over_the_ceiling(tmp_path, capsys):
    ui = _ui(tmp_path, shell_pad=3000)
    total = sum(ui_bundle.cold_js(ui_bundle.build(ui)[0]).values())
    assert ui_bundle.shell_size_report(ui, total) == 0
    out = capsys.readouterr().out
    assert (
        "/tico/ui/app.bundle.js" in out
        and "/vendor/marked.min.js" in out
        and f"{total / 1024:.1f} KB  cold open" in out
    )
    assert ui_bundle.shell_size_report(ui, total - 1) == 1
