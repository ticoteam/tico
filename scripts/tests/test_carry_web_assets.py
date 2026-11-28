"""The image build carries the previous release's page files forward (scripts/carry_web_assets.py), so a tab opened
before a release keeps working, for KEEP_DAYS after the last release that built a file."""

import json
from pathlib import Path

import pytest

from scripts import carry_web_assets as carry_assets

DAY = 86400.0
K = carry_assets.KEEP_DAYS * DAY  # how long a file is kept


def build(root: Path, name: str, files: dict[str, str]) -> Path:
    """A web build the way Vite writes it: a page and its files under assets/."""
    web = root / name
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(f"<!-- release {name} -->")
    for file, text in files.items():
        (web / "assets" / file).write_text(text)
    return web


def names(web: Path) -> set[str]:
    return {p.name for p in (web / "assets").iterdir()}


def test_a_release_s_files_are_carried_through_later_releases_for_keep_days(tmp_path):
    start = 1_000 * DAY
    a = build(tmp_path, "A", {"page-aaaa.js": "a page", "shared-1111.js": "shared"})
    assert carry_assets.carry(tmp_path / "none", a, start) == {}  # the first release: nothing before it
    b = build(tmp_path, "B", {"page-bbbb.js": "b page", "shared-1111.js": "shared"})
    carry_assets.carry(a, b, start + K / 7)  # A's page last shipped when B replaced it
    assert names(b) == {"page-aaaa.js", "page-bbbb.js", "shared-1111.js"}
    assert (b / "assets" / "page-aaaa.js").read_text() == "a page"
    c = build(tmp_path, "C", {"page-cccc.js": "c page"})
    carry_assets.carry(b, c, start + K)  # A's page: 6/7 of K since B; B's page and shared: now
    assert names(c) == {"page-aaaa.js", "page-bbbb.js", "shared-1111.js", "page-cccc.js"}
    d = build(tmp_path, "D", {"page-dddd.js": "d page"})
    carry_assets.carry(c, d, start + 9 * K / 7)  # A's page: 8/7 of K, dropped; B's, shared and C's kept
    assert names(d) == {"page-bbbb.js", "shared-1111.js", "page-cccc.js", "page-dddd.js"}
    e = build(tmp_path, "E", {"page-eeee.js": "e page"})
    carry_assets.carry(d, e, start + 15 * K / 7)  # B's and shared: 8/7 of K since C replaced B; C's: 6/7 since D
    assert names(e) == {"page-cccc.js", "page-dddd.js", "page-eeee.js"}
    recorded = json.loads((e / carry_assets.CARRIED).read_text())["carried"]
    assert recorded == {"page-cccc.js": start + 9 * K / 7, "page-dddd.js": start + 15 * K / 7}


def test_a_carried_file_never_replaces_one_the_build_made_and_links_are_not_carried(tmp_path):
    a = build(tmp_path, "A", {"page-aaaa.js": "a page", "same-2222.js": "from A"})
    (a / "assets" / "leak-0000.js").symlink_to(tmp_path / "secret.txt")
    (tmp_path / "secret.txt").write_text("not for the web")
    b = build(tmp_path, "B", {"same-2222.js": "from B"})
    carry_assets.carry(a, b, 0.0)
    assert names(b) == {"page-aaaa.js", "same-2222.js"}
    assert (b / "assets" / "same-2222.js").read_text() == "from B"


def test_the_command_refuses_a_build_that_was_not_made(tmp_path):
    a = build(tmp_path, "A", {"page-aaaa.js": "a page"})
    b = build(tmp_path, "B", {"page-bbbb.js": "b page"})
    assert carry_assets.main([str(a), str(b), "--now", "0"]) == 0
    assert names(b) == {"page-aaaa.js", "page-bbbb.js"}
    with pytest.raises(SystemExit) as stop:  # a missing build fails the image build
        carry_assets.main([str(a), str(tmp_path / "missing")])
    assert stop.value.code == 2


def test_written_apart_the_new_build_is_left_as_it_was(tmp_path):
    a = build(tmp_path, "A", {"page-aaaa.js": "a page", "same-2222.js": "from A"})
    b = build(tmp_path, "B", {"same-2222.js": "from B"})
    out = tmp_path / "out"
    assert carry_assets.main([str(a), str(b), "--out", str(out), "--now", "0"]) == 0
    assert names(b) == {"same-2222.js"} and not (b / carry_assets.CARRIED).exists()
    assert names(out) == {"page-aaaa.js"} and (out / carry_assets.CARRIED).is_file()
