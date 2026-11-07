"""The UI test map: its schema, the selector's lookups, and how a measured run becomes the map."""

import json

import pytest

from scripts import ui_test_map as umap

SOURCE = {"main": "a" * 40, "run": "https://example.invalid/actions/runs/1"}
RUN = "https://example.invalid/actions/runs/2"


def make_root(tmp_path, scripts=("alpha", "beta", "gamma"), files=("app/a.js", "app/b.js", "app/c.js")):
    root = tmp_path / "repo"
    (root / "ui" / "tests").mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in scripts:
        (root / "ui" / "tests" / f"{name}.cjs").write_text("", encoding="utf-8")
    for rel in files:
        (root / "ui" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "ui" / rel).write_text("", encoding="utf-8")
    html = "".join(f'<script src="/tico/ui/{rel}"></script>\n' for rel in files)
    (root / "ui" / "index.html").write_text(html, encoding="utf-8")
    return root


def a_map(**fields):
    """A valid map of three scripts, with the given fields replaced."""
    data = {
        "version": umap.VERSION,
        "source": dict(SOURCE),
        "scripts": 3,
        "shared_share": 0.9,
        "shared": [],
        "files": {},
        "unexercised": [],
        "partial": [],
    }
    data.update(fields)
    return data


def write_map(tmp_path, data, name="ui-test-map.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_record(measured, name, files, pages=1, loads=None):
    """A coverage record as scripts/ui-coverage.cjs writes it: files maps a UI file to {function key: calls}."""
    coverage = measured / "coverage"
    coverage.mkdir(parents=True, exist_ok=True)
    record = {"script": name, "pages": pages, "loads": pages if loads is None else loads, "files": files}
    (coverage / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")


def write_boot(measured, files, loads=1):
    write_record(measured, "boot", files, pages=1, loads=loads)


def test_ui_scripts_for_reads_a_file_its_own_scripts_and_shared_ones_with_the_core(tmp_path):
    root = make_root(tmp_path)
    map_path = write_map(
        tmp_path,
        a_map(
            files={"ui/app/a": ["alpha"], "ui/app/b": ["alpha", "beta"], "ui/app/c": ["alpha", "gamma"]},
            shared=["ui/app/c"],
        ),
    )
    assert umap.ui_scripts_for("ui/app/a.js", core=["gamma"], root=root, map_path=map_path) == ["alpha"]
    assert umap.ui_scripts_for("ui/app/b.js", core=["gamma"], root=root, map_path=map_path) == ["alpha", "beta"]
    # A shared file runs the core scripts too, and its own list.
    assert umap.ui_scripts_for("ui/app/c.js", core=["gamma", "beta"], root=root, map_path=map_path) == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_a_file_without_an_entry_has_no_answer_so_the_selector_keeps_its_rule(tmp_path):
    root = make_root(tmp_path)
    map_path = write_map(tmp_path, a_map(files={"ui/app/a": ["alpha"]}, unexercised=["ui/app/c"]))
    for path in ("ui/app/c.js", "ui/app/unknown.js", "ui/styles/site.css"):
        assert umap.ui_scripts_for(path, core=["beta"], root=root, map_path=map_path) is None


def test_no_map_or_a_missing_map_file_has_no_answer(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.delenv(umap.MAP_ENV, raising=False)
    assert umap.ui_scripts_for("ui/app/a.js", core=["gamma"], root=root) is None
    absent = tmp_path / "absent.json"
    assert umap.ui_scripts_for("ui/app/a.js", core=["gamma"], root=root, map_path=absent) is None
    monkeypatch.setenv(umap.MAP_ENV, str(absent))
    assert umap.ui_scripts_for("ui/app/a.js", core=["gamma"], root=root) is None


def test_the_environment_names_the_map(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    monkeypatch.setenv(umap.MAP_ENV, str(write_map(tmp_path, a_map(files={"ui/app/a": ["beta"]}))))
    assert umap.ui_scripts_for("ui/app/a.js", core=["gamma"], root=root) == ["beta"]


def test_a_browser_script_runs_itself_and_a_deleted_one_has_no_entry(tmp_path):
    root = make_root(tmp_path)
    none = tmp_path / "none.json"
    assert umap.ui_scripts_for("ui/tests/alpha.cjs", core=["gamma"], root=root, map_path=none) == ["alpha"]
    assert umap.ui_scripts_for("ui/tests/gone.cjs", core=["gamma"], root=root, map_path=none) is None


def test_a_path_outside_the_ui_is_an_error(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(umap.MapError, match="not a UI file"):
        umap.ui_scripts_for("scripts/x.py", core=["gamma"], root=root, map_path=tmp_path / "none.json")


def test_a_renamed_typescript_file_keeps_the_entry_measured_for_its_javascript(tmp_path):
    # A file converted from .js to .ts (the same path without its extension) is found under its new name, with no edit.
    root = make_root(tmp_path, files=("app/a.js",))
    built, _ = umap.build(root, _measured(tmp_path), "a" * 40, RUN)
    map_path = write_map(tmp_path, built, name="built.json")
    (root / "ui" / "app" / "a.js").rename(root / "ui" / "app" / "a.ts")
    (root / "ui" / "index.html").write_text('<script src="/tico/ui/app/a.ts"></script>\n', encoding="utf-8")
    assert umap.ui_scripts_for("ui/app/a.ts", core=["gamma"], root=root, map_path=map_path) == ["alpha"]
    assert umap.served_files(root) == ["ui/app/a"]


def test_a_typescript_source_and_its_compiled_javascript_are_one_file_in_a_measured_run(tmp_path):
    # The page loads a .ts file as its compiled .js or as .ts: records keyed either way are one file, and the .ts source
    # answers for it.
    root = make_root(tmp_path, files=("app/a.ts",))
    measured = tmp_path / "measured"
    write_boot(measured, {"ui/app/a.js": {"1-10": 1}})
    write_record(measured, "alpha", {"ui/app/a.js": {"1-10": 2}})
    write_record(measured, "beta", {"ui/app/a.ts": {"1-10": 2}})
    write_record(measured, "gamma", {})
    data, _ = umap.build(root, measured, "a" * 40, RUN)
    assert data["files"] == {"ui/app/a": ["alpha", "beta"]}
    assert data["unexercised"] == []
    assert umap.ui_scripts_for("ui/app/a.ts", core=["gamma"], root=root, map_path=write_map(tmp_path, data)) == [
        "alpha",
        "beta",
    ]


def test_loading_prunes_names_that_no_longer_exist(tmp_path):
    root = make_root(tmp_path)
    map_path = write_map(
        tmp_path,
        a_map(
            files={"ui/app/a": ["alpha", "ghost"], "ui/app/gone": ["beta"], "ui/app/b": ["ghost"]},
            shared=["ui/app/a", "ui/app/b"],
            unexercised=["ui/app/c", "ui/app/gone2"],
            partial=["beta", "ghost"],
        ),
    )
    data = umap.load_map(root, map_path)
    # gone has no source file and b lost its only script, so neither has an entry any more; b is no longer shared either.
    assert data["files"] == {"ui/app/a": ["alpha"]}
    assert data["shared"] == ["ui/app/a"]
    assert data["unexercised"] == ["ui/app/c"]
    assert data["partial"] == ["beta"]
    assert umap.validate(data) == []


def test_load_map_is_none_for_a_missing_file_and_an_error_for_a_bad_one(tmp_path):
    root = make_root(tmp_path)
    assert umap.load_map(root, tmp_path / "none.json") is None
    bad = write_map(tmp_path, a_map(version=1))
    with pytest.raises(umap.MapError, match="does not validate"):
        umap.load_map(root, bad)


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"version": 1}, f"version must be {umap.VERSION}"),
        ({"shared": ["ui/app/b", "ui/app/a"]}, "shared must be sorted and without repeats"),
        ({"shared": ["app/a"]}, "shared must be a list of valid names"),
        ({"partial": ["alpha/x"]}, "partial must be a list of valid names"),
        ({"files": {"app/a": ["alpha"]}}, "is not a UI file"),
        ({"files": {"ui/app/a.js": ["alpha"]}}, "is not a UI file"),
        ({"files": {"ui/app/a": []}}, "must not be empty"),
        ({"shared": ["ui/app/a"]}, "shared ui/app/a has no list of scripts in files"),
        ({"files": {"ui/app/a": ["alpha"]}, "unexercised": ["ui/app/a"]}, "is in both files and unexercised"),
        ({"source": {"main": "abc", "run": "x"}}, "source holds main"),
        ({"shared_share": 0}, "shared_share is a number"),
        ({"scripts": 0}, "scripts is the number"),
        ({"extra": 1}, "a map holds exactly these fields"),
    ],
)
def test_validate_names_each_problem(fields, message):
    found = umap.validate(a_map(**fields))
    assert any(message in line for line in found), found


def test_a_valid_map_has_no_problems():
    assert umap.validate(a_map(files={"ui/app/a": ["alpha"]}, shared=["ui/app/a"])) == []


def _measured(tmp_path):
    """A measured run in which ui/app/a.js is run only by alpha, and beta and gamma run nothing."""
    measured = tmp_path / "measured"
    write_boot(measured, {})
    write_record(measured, "alpha", {"ui/app/a.js": {"1-10": 2}})
    write_record(measured, "beta", {})
    write_record(measured, "gamma", {})
    return measured


def test_build_classifies_shared_files_unexercised_and_partial_ones(tmp_path):
    root = make_root(tmp_path)
    measured = tmp_path / "measured"
    write_boot(measured, {"ui/app/a.js": {"1-10": 1}})
    write_record(measured, "alpha", {"ui/app/a.js": {"1-10": 2}, "ui/app/b.js": {"1-10": 1}})
    write_record(measured, "beta", {"ui/app/a.js": {"1-10": 2}, "ui/app/b.js": {"1-10": 1}})
    write_record(measured, "gamma", {"ui/app/a.js": {"1-10": 2}})
    (measured / "timings-0.json").write_text(json.dumps({"alpha": 1.2, "beta": 2.0}), encoding="utf-8")
    data, no_page = umap.build(root, measured, "a" * 40, RUN)
    # a is exercised by all three scripts (at least 0.9 of them), so it is shared, and it keeps its own list of scripts.
    assert data["shared"] == ["ui/app/a"]
    assert data["files"] == {"ui/app/a": ["alpha", "beta", "gamma"], "ui/app/b": ["alpha", "beta"]}
    assert data["unexercised"] == ["ui/app/c"]
    assert data["partial"] == ["gamma"]
    assert data["scripts"] == 3
    assert data["source"] == {"main": "a" * 40, "run": RUN}
    assert no_page == []
    assert umap.validate(data) == []


def test_a_call_the_boot_load_makes_does_not_exercise_its_file(tmp_path):
    root = make_root(tmp_path, files=("app/a.js",))
    measured = tmp_path / "measured"
    write_boot(measured, {"ui/app/a.js": {"1-10": 1, "20-30": 1}})
    write_record(measured, "alpha", {"ui/app/a.js": {"1-10": 1, "20-30": 1}})  # the same calls as boot: nothing
    write_record(measured, "beta", {"ui/app/a.js": {"1-10": 1, "20-30": 2}})  # one call more than boot: exercised
    write_record(measured, "gamma", {})
    data, _ = umap.build(root, measured, "a" * 40, RUN)
    assert data["files"] == {"ui/app/a": ["beta"]}
    assert data["shared"] == []


def test_a_function_the_boot_load_never_runs_exercises_its_file(tmp_path):
    root = make_root(tmp_path, files=("app/a.js",))
    measured = tmp_path / "measured"
    write_boot(measured, {})
    write_record(measured, "alpha", {"ui/app/a.js": {"5-9": 1}})
    write_record(measured, "beta", {})
    write_record(measured, "gamma", {})
    data, _ = umap.build(root, measured, "a" * 40, RUN)
    assert data["files"] == {"ui/app/a": ["alpha"]}


def test_the_boot_count_scales_with_the_page_loads(tmp_path):
    root = make_root(tmp_path, files=("app/a.js",))
    measured = tmp_path / "measured"
    write_boot(measured, {"ui/app/a.js": {"1-10": 2}}, loads=2)  # the boot load ran twice, so one call per load
    write_record(measured, "alpha", {"ui/app/a.js": {"1-10": 3}}, pages=3)  # three loads, three calls: nothing
    write_record(measured, "beta", {"ui/app/a.js": {"1-10": 4}}, pages=3)  # three loads, four calls: exercised
    write_record(measured, "gamma", {"ui/app/a.js": {"1-10": 1}}, pages=1)  # one load, one call: nothing
    data, _ = umap.build(root, measured, "a" * 40, RUN)
    assert data["files"] == {"ui/app/a": ["beta"]}


def test_shared_is_a_share_of_the_scripts(tmp_path):
    root = make_root(tmp_path, files=("app/b.js",))
    measured = tmp_path / "measured"
    write_boot(measured, {})
    write_record(measured, "alpha", {"ui/app/b.js": {"1-2": 1}})
    write_record(measured, "beta", {"ui/app/b.js": {"1-2": 1}})
    write_record(measured, "gamma", {})
    half, _ = umap.build(root, measured, "a" * 40, RUN, share=0.5)
    assert half["shared"] == ["ui/app/b"]
    assert half["files"] == {"ui/app/b": ["alpha", "beta"]}
    strict, _ = umap.build(root, measured, "a" * 40, RUN)  # two of three is short of 0.9
    assert strict["shared"] == []
    assert strict["files"] == {"ui/app/b": ["alpha", "beta"]}


def test_build_refuses_a_script_with_no_record(tmp_path):
    root = make_root(tmp_path)
    measured = tmp_path / "measured"
    write_boot(measured, {})
    write_record(measured, "alpha", {})
    write_record(measured, "beta", {})
    with pytest.raises(umap.MapError, match="no coverage record for gamma"):
        umap.build(root, measured, "a" * 40, RUN)


def test_build_refuses_a_missing_or_empty_boot_baseline(tmp_path):
    root = make_root(tmp_path)
    measured = tmp_path / "measured"
    for name in ("alpha", "beta", "gamma"):
        write_record(measured, name, {})
    with pytest.raises(umap.MapError, match="no coverage record for boot"):
        umap.build(root, measured, "a" * 40, RUN)
    write_boot(measured, {}, loads=0)
    with pytest.raises(umap.MapError, match="loaded no page"):
        umap.build(root, measured, "a" * 40, RUN)


def test_shards_cover_every_script_once(tmp_path):
    root = make_root(tmp_path, scripts=("a1", "a2", "a3", "a4", "a5"))
    parts = [umap.shard(index, 3, root=root) for index in range(3)]
    assert sorted(name for part in parts for name in part) == ["a1", "a2", "a3", "a4", "a5"]
    with pytest.raises(umap.MapError):
        umap.shard(3, 3, root=root)


def test_served_files_are_the_page_scripts_that_exist_as_keys(tmp_path):
    root = make_root(tmp_path, files=("app/a.js", "app/b.ts"))
    (root / "ui" / "index.html").write_text(
        '<script src="/tico/ui/app/a.js"></script>\n<script src="/tico/ui/app/gone.js"></script>\n'
        '<script src="/tico/ui/app/b.ts"></script>\n<script src="/tico/ui/app/a.js"></script>\n',
        encoding="utf-8",
    )
    assert umap.served_files(root) == ["ui/app/a", "ui/app/b"]


def test_summary_names_partial_and_no_page_scripts():
    data = {
        "scripts": 3,
        "partial": ["gamma"],
        "shared": ["ui/app/a"],
        "shared_share": 0.9,
        "files": {"ui/app/b": ["alpha", "beta"]},
        "unexercised": ["ui/app/c"],
    }
    text = umap.summary(data, ["gamma"])
    assert "did not pass in the measured run: 1 (gamma)" in text
    assert "median scripts per file: 2" in text
    assert "Scripts that opened no page: gamma" in text


def test_check_passes_a_valid_map_and_fails_a_bad_one(tmp_path, capsys):
    assert umap.main(["check", "--map", str(write_map(tmp_path, a_map()))]) == 0
    assert "matches the schema" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(a_map(version=1)), encoding="utf-8")
    assert umap.main(["check", "--map", str(bad)]) == 1
    assert f"version must be {umap.VERSION}" in capsys.readouterr().err
