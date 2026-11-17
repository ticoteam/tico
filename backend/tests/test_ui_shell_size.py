"""The JavaScript a cold open of Home downloads stays under its ceiling (backend/ui_build.py SHELL_JS_CEILING, P05b).

scripts/pr_checks.py runs this file for every pull request that changes the UI or its build (rule 9, UI_SHELL_SIZE), so
a change that pushes the shell past the ceiling fails before it merges. It measures this checkout's build (build/ui,
which conftest builds when it is stale and this checkout can)."""

import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

from backend import ui_build

ROOT = Path(__file__).resolve().parents[2]


def test_a_cold_open_of_home_stays_within_its_ceiling():
    ui_build.check(ROOT / "ui")
    assert ui_build.shell_over(ROOT / "ui") is None, ui_build.shell_over(ROOT / "ui")
    # The budget is 300 KB gzip (P05b): the ceiling only ever comes down to it.
    assert ui_build.SHELL_JS_CEILING <= 300 * 1024


def test_the_measure_is_every_script_a_cold_open_downloads():
    ui = ROOT / "ui"
    page = (ui_build.out_dir(ui) / "index.html").read_text()
    sizes = ui_build.cold_js(ui)
    # Each script tag the page runs (not the JSON parts list), and nothing the page fetches only later.
    named = re.findall(r'<script src="/tico/ui/([^"]+)"', page)
    assert set(sizes) == set(named)
    parts = json.loads(re.search(r'id="ui-parts">(.*?)</script>', page).group(1))["parts"]
    assert not {p["url"].removeprefix("/tico/ui/") for p in parts.values()} & set(sizes)
    # As the server sends them: the build's gzip copies.
    for name, size in sizes.items():
        assert size == (ui_build.bundle_dir(ui) / f"{name}.gz").stat().st_size


def _noise(n, seed):
    """n bytes of JavaScript that gzip cannot shrink much: a string literal of base64."""
    raw = b"".join(hashlib.sha256(f"{seed}:{i}".encode()).digest() for i in range(n // 32 + 1))
    return f"const PAD_{seed}='{base64.b64encode(raw).decode()[:n]}';"


def _build(tmp_path, pad=0):
    """A build as scripts/build-ui.cjs leaves it: one shell script holding a.ts (padded by `pad` bytes) and b.ts, and
    marked."""
    ui = tmp_path / "ui"
    folder = ui_build.bundle_dir(ui)
    folder.mkdir(parents=True)
    shell = f"'use strict';\n// file: app/a.ts\n'use strict';{_noise(pad, 'a') if pad else ''}\n\n// file: app/b.ts\n'use strict';const B=1;\n\n"
    for name, text in (
        ("app.bundle.0123456789abcdef.js", shell),
        ("app.bundle.marked.0123456789abcdef.js", _noise(4000, "m")),
    ):
        (folder / name).write_text(text)
        (folder / f"{name}.gz").write_bytes(gzip.compress(text.encode(), 9, mtime=0))
    shell_names = ["app.bundle.0123456789abcdef.js", "app.bundle.marked.0123456789abcdef.js"]
    (folder.parent / "manifest.json").write_text(json.dumps({"build": "b", "shell": shell_names, "inputs": {}}))
    return ui


def test_the_ceiling_passes_at_its_size_and_fails_one_byte_under(tmp_path):
    ui = _build(tmp_path, pad=3000)
    total = sum(ui_build.cold_js(ui).values())
    assert ui_build.shell_over(ui, total) is None
    message = ui_build.shell_over(ui, total - 1)
    assert message and f"{total / 1024:.1f} KB" in message and f"{(total - 1) / 1024:.1f} KB" in message
    # The message names the file that grew, largest first, and where code a click runs belongs.
    assert re.search(r"largest shell files: app/a\.ts [\d.]+ KB, app/b\.ts", message) and "ui/parts.json" in message


def test_the_size_report_prints_each_script_and_fails_over_the_ceiling(tmp_path, capsys, monkeypatch):
    ui = _build(tmp_path, pad=3000)
    monkeypatch.setattr(ui_build, "ROOT", tmp_path)
    total = sum(ui_build.cold_js(ui).values())
    assert ui_build.main(["--shell-size"]) == 0
    out = capsys.readouterr().out
    assert (
        "app.bundle.0123456789abcdef.js" in out
        and "app.bundle.marked." in out
        and f"{total / 1024:.1f} KB  cold open" in out
    )
    monkeypatch.setattr(ui_build, "SHELL_JS_CEILING", total - 1)
    assert ui_build.main(["--shell-size"]) == 1
