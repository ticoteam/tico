"""The UI test map: its lookups, its checks, and how a measured run becomes the map."""

import json

import pytest

from scripts import ui_test_map as umap


def make_root(tmp_path, scripts=("alpha", "beta", "gamma"), files=("app/a.js", "app/b.js", "app/c.js"), tags=None):
    root = tmp_path / "repo"
    (root / "ui" / "tests").mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in scripts:
        (root / "ui" / "tests" / f"{name}.cjs").write_text("", encoding="utf-8")
    for rel in files:
        (root / "ui" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "ui" / rel).write_text("", encoding="utf-8")
    listed = files if tags is None else tags
    html = "".join(f'<script src="/tico/ui/{rel}"></script>\n' for rel in listed)
    (root / "ui" / "index.html").write_text(html, encoding="utf-8")
    return root


def write_map(root, **fields):
    data = {"files": {}, "partial": [], "scripts": 3, "shared": [], "shared_share": 0.9, "smoke": [], "unexercised": []}
    data.update(fields)
    umap.write(data, root)


def write_record(measured, name, ran_files, pages=1):
    """A coverage record as scripts/ui-coverage.cjs writes it: ran_files are the UI files the script ran code in."""
    coverage = measured / "coverage"
    coverage.mkdir(parents=True, exist_ok=True)
    files = {rel: {"functions": 2, "ran": 1} for rel in ran_files}
    (coverage / f"{name}.json").write_text(
        json.dumps({"script": name, "pages": pages, "files": files}), encoding="utf-8"
    )


def test_ui_scripts_for_reads_files_shared_and_unknown(tmp_path):
    root = make_root(tmp_path)
    write_map(
        root,
        files={"ui/app/a.js": ["alpha"], "ui/app/b.js": ["alpha", "beta"]},
        shared=["ui/app/c.js"],
        smoke=["alpha", "beta"],
    )
    assert umap.ui_scripts_for("ui/app/a.js", root=root) == ["alpha"]
    assert umap.ui_scripts_for("ui/app/b.js", root=root) == ["alpha", "beta"]
    assert umap.ui_scripts_for("ui/app/c.js", root=root) == ["alpha", "beta"]
    assert umap.ui_scripts_for("ui/app/c.js", core=["gamma", "alpha"], root=root) == ["alpha", "gamma"]
    assert umap.ui_scripts_for("ui/app/zzz.js", root=root) is None


def test_no_map_means_no_entry(tmp_path):
    root = make_root(tmp_path)
    assert umap.ui_scripts_for("ui/app/a.js", root=root) is None


def test_empty_map_is_clean_and_has_no_entries(tmp_path):
    root = make_root(tmp_path)
    umap.empty(root)
    assert umap.problems(root) == []
    assert umap.ui_scripts_for("ui/app/a.js", root=root) is None


def test_problems_name_missing_files_and_scripts(tmp_path):
    root = make_root(tmp_path)
    write_map(root, files={"ui/app/a.js": ["alpha", "ghost"], "ui/app/gone.js": ["alpha"]}, smoke=["alpha"])
    found = umap.problems(root)
    assert any("ui/app/gone.js is in the map" in line for line in found)
    assert any("ghost" in line and "no longer exists" in line for line in found)
    assert not any("ui/app/a.js" in line for line in found)


def test_problems_require_smoke_for_shared_and_exact_fields(tmp_path):
    root = make_root(tmp_path)
    write_map(root, shared=["ui/app/a.js"], smoke=[])
    assert any("no smoke scripts" in line for line in umap.problems(root))
    (root / "scripts" / "ui-test-map.json").write_text(json.dumps({"files": {}}), encoding="utf-8")
    assert "exactly these fields" in umap.problems(root)[0]


def test_problems_flag_a_missing_map_and_bad_json(tmp_path):
    root = make_root(tmp_path)
    assert "is missing" in umap.problems(root)[0]
    (root / "scripts" / "ui-test-map.json").write_text("{", encoding="utf-8")
    assert "is not JSON" in umap.problems(root)[0]


def test_prune_drops_names_that_no_longer_exist(tmp_path):
    root = make_root(tmp_path)
    write_map(
        root,
        files={"ui/app/a.js": ["alpha", "ghost"], "ui/app/gone.js": ["beta"]},
        shared=["ui/app/b.js"],
        smoke=["alpha", "ghost"],
        partial=["ghost"],
        unexercised=["ui/app/c.js"],
    )
    umap.prune(root)
    data = umap.load(root)
    assert data["files"] == {"ui/app/a.js": ["alpha"]}
    assert data["shared"] == ["ui/app/b.js"]
    assert data["smoke"] == ["alpha"]
    assert data["partial"] == []
    assert data["unexercised"] == ["ui/app/c.js"]
    assert umap.problems(root) == []


def test_build_classifies_shared_files_and_unexercised_ones(tmp_path):
    root = make_root(tmp_path)
    measured = tmp_path / "measured"
    write_record(measured, "alpha", ["ui/app/a.js", "ui/app/b.js"])
    write_record(measured, "beta", ["ui/app/a.js", "ui/app/b.js"])
    write_record(measured, "gamma", ["ui/app/a.js"], pages=0)
    (measured / "timings-0.json").write_text(json.dumps({"alpha": 1.2, "beta": 2.0}), encoding="utf-8")
    data, no_page = umap.build(root, measured, smoke=["alpha"])
    # a.js ran in all three scripts (at least 0.9 of them), so it is shared; b.js ran in two of three, so it lists them.
    assert data["shared"] == ["ui/app/a.js"]
    assert data["files"] == {"ui/app/b.js": ["alpha", "beta"]}
    assert data["unexercised"] == ["ui/app/c.js"]
    assert data["partial"] == ["gamma"]
    assert data["scripts"] == 3
    assert data["smoke"] == ["alpha"]
    assert no_page == ["gamma"]
    assert umap.problems(root) == []
    assert umap.ui_scripts_for("ui/app/a.js", root=root) == ["alpha"]
    assert umap.ui_scripts_for("ui/app/c.js", root=root) is None


def test_shared_threshold_is_a_share_of_the_scripts(tmp_path):
    root = make_root(tmp_path)
    measured = tmp_path / "measured"
    write_record(measured, "alpha", ["ui/app/b.js"])
    write_record(measured, "beta", ["ui/app/b.js"])
    write_record(measured, "gamma", [])
    data, _ = umap.build(root, measured, share=0.5, smoke=["alpha"])
    assert data["shared"] == ["ui/app/b.js"]
    assert data["files"] == {}


def test_build_refuses_a_script_with_no_record(tmp_path):
    root = make_root(tmp_path)
    measured = tmp_path / "measured"
    write_record(measured, "alpha", ["ui/app/a.js"])
    with pytest.raises(umap.MapError, match="no coverage record for beta"):
        umap.build(root, measured, smoke=["alpha"])
    assert not (root / "scripts" / "ui-test-map.json").exists()


def test_shards_cover_every_script_once(tmp_path):
    root = make_root(tmp_path, scripts=("a1", "a2", "a3", "a4", "a5"))
    parts = [umap.shard(index, 3, root=root) for index in range(3)]
    assert sorted(name for part in parts for name in part) == ["a1", "a2", "a3", "a4", "a5"]
    with pytest.raises(umap.MapError):
        umap.shard(3, 3, root=root)


def test_served_files_are_the_page_scripts_that_exist(tmp_path):
    root = make_root(tmp_path, files=("app/a.js", "app/b.js"), tags=("app/a.js", "app/gone.js", "app/b.js", "app/a.js"))
    assert umap.served_files(root) == ["ui/app/a.js", "ui/app/b.js"]


def test_summary_names_partial_and_no_page_scripts():
    data = {
        "scripts": 3,
        "partial": ["gamma"],
        "shared": ["ui/app/a.js"],
        "shared_share": 0.9,
        "smoke": ["alpha"],
        "files": {"ui/app/b.js": ["alpha", "beta"]},
        "unexercised": ["ui/app/c.js"],
    }
    text = umap.summary(data, ["gamma"])
    assert "gamma" in text
    assert "median scripts per file: 2" in text
    assert "Scripts that opened no page: gamma" in text
