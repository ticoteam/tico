"""The measured Python test map: function scopes, drift hashes, the shard plan, the build's refusals, and staleness."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from scripts import py_test_map as pm

SHA = "a" * 40
SOURCE = '''import os

LIMIT = 3


@decorator(
    1,
)
def top(x):
    return x + LIMIT


class Box:
    size = 2

    def grow(self, n):
        def inner(k):
            return k * 2

        return inner(n)


async def fetch():
    return 1
'''


def line_of(source, text):
    """The 1-based line that contains `text` (exactly one line must)."""
    hits = [number for number, line in enumerate(source.splitlines(), 1) if text in line]
    assert len(hits) == 1, text
    return hits[0]


def owner_of(source, text):
    return pm.owners(source)[line_of(source, text)]


def test_owners_split_bodies_from_headers_and_module_code():
    assert owner_of(SOURCE, "return x + LIMIT") == ("function", "top")
    assert owner_of(SOURCE, "LIMIT = 3") == ("module", "")
    assert owner_of(SOURCE, "import os") == ("module", "")
    # A decorator (including its continuation lines) and a signature are headers, never narrowed.
    assert owner_of(SOURCE, "@decorator(") == ("header", "")
    assert owner_of(SOURCE, "1,") == ("header", "")
    assert owner_of(SOURCE, "def top(x):") == ("header", "")
    # A class body outside its methods is module code; a method is its own qualname, nested defs are dotted.
    assert owner_of(SOURCE, "size = 2") == ("module", "")
    assert owner_of(SOURCE, "def grow(self, n):") == ("header", "")
    assert owner_of(SOURCE, "return inner(n)") == ("function", "Box.grow")
    assert owner_of(SOURCE, "return k * 2") == ("function", "Box.grow.inner")
    assert owner_of(SOURCE, "async def fetch") == ("header", "")
    assert owner_of(SOURCE, "return 1") == ("function", "fetch")


def test_owner_names_map_module_lines_to_the_module_key():
    own = pm.owners(SOURCE)
    lines = {line_of(SOURCE, "LIMIT = 3"), line_of(SOURCE, "return x + LIMIT"), line_of(SOURCE, "def top(x):")}
    assert pm.owner_names(own, lines) == {pm.MODULE, "top"}
    assert pm.owner_names(own, {10_000}) == {pm.MODULE}


def test_function_hashes_ignore_lines_and_comments_but_see_the_body():
    shifted = "\n\n# a comment\n" + SOURCE
    assert pm.function_hashes(shifted) == pm.function_hashes(SOURCE)
    changed = SOURCE.replace("return x + LIMIT", "return x + LIMIT + 1")
    before, after = pm.function_hashes(SOURCE), pm.function_hashes(changed)
    assert before["top"] != after["top"]
    assert before["Box.grow"] == after["Box.grow"]


def test_assign_covers_every_file_once_and_balances_seconds():
    files = [f"backend/tests/test_{n:03d}.py" for n in range(40)]
    timings = {name: float(1 + (index * 7) % 13) for index, name in enumerate(files)}
    shards = [pm.assign(files, timings, shard, 4) for shard in range(4)]
    assert sorted(name for shard in shards for name in shard) == sorted(files)
    loads = [sum(timings[name] for name in shard) for shard in shards]
    assert max(loads) - min(loads) <= max(timings.values())
    assert pm.assign(files, timings, 2, 4) == shards[2]


def test_parse_time_accepts_only_utc_stamps():
    assert pm.parse_time("2026-11-07T04:41:00Z") == datetime(2026, 11, 7, 4, 41, tzinfo=timezone.utc)
    for bad in ("2026-11-07 04:41:00", "2026-11-07T04:41:00+00:00", "", None):
        with pytest.raises(ValueError):
            pm.parse_time(bad)


def partial(shard, of, tests, files, hashes=None, collected=("backend/tests/test_a.py", "backend/tests/test_b.py")):
    return {
        "version": pm.VERSION,
        "sha": SHA,
        "shard": shard,
        "of": of,
        "collected": list(collected),
        "tests": tests,
        "files": files,
        "hashes": hashes or {},
    }


def write_partials(directory, *parts):
    directory.mkdir(parents=True, exist_ok=True)
    for part in parts:
        (directory / f"partial-{part['shard']}.json").write_text(json.dumps(part), encoding="utf-8")
    return directory


def two_shards(tmp_path):
    a, b = "backend/tests/test_a.py", "backend/tests/test_b.py"
    digest = pm.function_hashes(SOURCE)["top"]
    first = partial(
        0,
        2,
        {a: {"exit": 0, "partial": False, "seconds": 1.0}},
        {"backend/hubdb.py": {"top": [a], pm.MODULE: [a]}},
        {"backend/hubdb.py": {"top": digest}},
    )
    second = partial(
        1,
        2,
        {b: {"exit": 1, "partial": True, "seconds": 2.0, "tail": "boom"}},
        {"backend/hubdb.py": {"top": [b]}, "backend/app.py": {pm.MODULE: [b]}},
    )
    return write_partials(tmp_path / "partials", first, second)


def test_build_merges_shards_into_indexes_and_flags_partial_tests(tmp_path):
    now = datetime(2026, 11, 7, 4, 41, tzinfo=timezone.utc)
    data = pm.build(two_shards(tmp_path), tmp_path / "map.json", SHA, "https://example.test/run/1", now=now)
    assert data["tests"] == ["backend/tests/test_a.py", "backend/tests/test_b.py"]
    assert data["partial"] == [1]
    assert data["files"]["backend/hubdb.py"]["top"] == [0, 1]
    assert data["files"]["backend/hubdb.py"][pm.MODULE] == [0]
    assert data["files"]["backend/app.py"][pm.MODULE] == [1]
    assert data["hashes"] == {"backend/hubdb.py": {"top": pm.function_hashes(SOURCE)["top"]}}
    assert data["measured_at"] == "2026-11-07T04:41:00Z"
    assert json.loads((tmp_path / "map.json").read_text()) == data
    pm.check_map(data)
    assert "- test files: 2 (1 partial, always treated as unmeasured)" in pm.summary(data)


def test_build_refuses_a_missing_shard_a_skipped_test_or_disagreeing_shards(tmp_path):
    parts = two_shards(tmp_path)
    (parts / "partial-1.json").unlink()
    with pytest.raises(pm.MapError, match="shards"):
        pm.build(parts, tmp_path / "map.json", SHA)

    parts = two_shards(tmp_path / "again")
    with pytest.raises(pm.MapError, match="commit"):
        pm.build(parts, tmp_path / "other.json", "b" * 40)

    unmeasured = write_partials(
        tmp_path / "unmeasured",
        partial(0, 2, {"backend/tests/test_a.py": {"exit": 0, "partial": False}}, {}),
        partial(1, 2, {}, {}),
    )
    with pytest.raises(pm.MapError, match="not measured"):
        pm.build(unmeasured, tmp_path / "map2.json", SHA)


def test_check_map_refuses_a_map_the_selector_would_misread(tmp_path):
    good = pm.build(two_shards(tmp_path), tmp_path / "map.json", SHA)
    pm.check_map(good)
    broken = [
        {**good, "sha": "not-a-sha"},
        {**good, "partial": [5]},
        {**good, "files": {"backend/hubdb.py": {"top": [9]}}},
        {**good, "hashes": {"backend/hubdb.py": {"top": "xyz"}}},
        {**good, "hashes": {"backend/nope.py": {"top": pm.function_hashes(SOURCE)["top"]}}},
        {**good, "tests": ["backend/tests/test_b.py", "backend/tests/test_a.py"]},
        {**good, "extra": 1},
    ]
    for data in broken:
        with pytest.raises(pm.MapError):
            pm.check_map(data)


def test_load_map_uses_a_fresh_map_and_otherwise_says_static(tmp_path, monkeypatch):
    now = datetime(2026, 11, 7, 4, 41, tzinfo=timezone.utc)
    pm.build(two_shards(tmp_path), tmp_path / "map.json", SHA, now=now)
    path = str(tmp_path / "map.json")

    data, note = pm.load_map(path, now=now + timedelta(days=2))
    assert data is not None and note == ""

    data, note = pm.load_map(path, now=now + timedelta(days=pm.MAX_AGE.days, hours=1))
    assert data is None and "days old" in note

    monkeypatch.delenv(pm.MAP_ENV, raising=False)
    assert pm.load_map()[0] is None
    assert pm.load_map(str(tmp_path / "missing.json"))[0] is None

    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    data, note = pm.load_map(str(tmp_path / "bad.json"), now=now)
    assert data is None and "unreadable" in note


def test_coverage_lines_refuses_a_line_under_another_test_context(tmp_path, monkeypatch):
    pytest.importorskip("coverage")
    from coverage import CoverageData

    monkeypatch.setattr(pm, "ROOT", tmp_path)
    data = CoverageData(basename=str(tmp_path / "cov.one"))
    data.set_context("backend/tests/test_other.py")
    data.add_lines({str(tmp_path / "backend" / "hubdb.py"): [3]})
    data.write()
    with pytest.raises(pm.MapError, match="ran under context"):
        pm.coverage_lines(tmp_path, "backend/tests/test_a.py")
