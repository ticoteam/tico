"""Tests for scripts/baseline_merge.py: the three merge drivers, the layout they depend on, and the driver entry point."""

import json
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baseline_merge as bm  # noqa: E402
import lint_baseline  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def entry(code, start=1, end=5):
    return {"code": code, "range": {"startColumn": start, "endColumn": end, "lineCount": 1}}


def baseline(files):
    return bm.render_pyright(files)


def raises_conflict(fn, *args):
    try:
        fn(*args)
    except bm.Conflict as conflict:
        return str(conflict)
    raise AssertionError(f"no conflict from {fn.__name__}")


def counts_text(counts):
    return lint_baseline.render_counts(counts)


def test_counts_sum_each_sides_fixes():
    # Five findings in the base; ours fixed one, theirs fixed two, different ones: three remain.
    base = counts_text({("a.py", "F401"): 5})
    ours = counts_text({("a.py", "F401"): 4})
    theirs = counts_text({("a.py", "F401"): 3})
    assert lint_baseline.load_counts(bm.merge_counts(base, ours, theirs)) == {("a.py", "F401"): 2}


def test_counts_pair_removed_by_one_side_stays_removed():
    base = counts_text({("a.py", "B905"): 2, ("b.py", "B905"): 1})
    ours = counts_text({("b.py", "B905"): 1})  # a.py fixed entirely
    theirs = counts_text({("a.py", "B905"): 2, ("b.py", "B905"): 1})
    assert lint_baseline.load_counts(bm.merge_counts(base, ours, theirs)) == {("b.py", "B905"): 1}


def test_counts_pair_added_by_one_side_is_kept():
    base = counts_text({("a.py", "F401"): 1})
    ours = counts_text({("a.py", "F401"): 1, ("c.py", "E501"): 3})
    theirs = counts_text({("a.py", "F401"): 1})
    assert lint_baseline.load_counts(bm.merge_counts(base, ours, theirs)) == {
        ("a.py", "F401"): 1,
        ("c.py", "E501"): 3,
    }


def test_toml_keeps_pairs_both_sides_keep_and_applies_each_removal_and_addition():
    base = {("a.py", "F401"), ("b.py", "F401"), ("c.py", "F401")}
    ours = {("b.py", "F401"), ("c.py", "F401"), ("d.py", "E501")}  # a.py removed, d.py added
    theirs = {("a.py", "F401"), ("b.py", "F401"), ("e.py", "E501")}  # c.py removed, e.py added
    merged = bm.merge_toml(
        lint_baseline.render_toml(base), lint_baseline.render_toml(ours), lint_baseline.render_toml(theirs)
    )
    assert lint_baseline.baseline_pairs(merged) == {("b.py", "F401"), ("d.py", "E501"), ("e.py", "E501")}


def test_toml_has_no_counts_so_count_changes_do_not_touch_it():
    text = lint_baseline.render_toml({("a.py", "F401"), ("b.py", "E501")})
    assert "owner" not in text and "Findings per rule" not in text
    assert lint_baseline.baseline_pairs(text) == {("a.py", "F401"), ("b.py", "E501")}
    tomllib.loads(text)


def test_sequences_adjacent_removals_on_both_sides_merge():
    merged = bm.merge_sequences(["a", "b", "c", "d"], ["a", "c", "d"], ["a", "b", "d"])
    assert merged == ["a", "d"]


def test_sequences_identical_change_on_both_sides_is_one_change():
    assert bm.merge_sequences(["a", "b"], ["a", "x"], ["a", "x"]) == ["a", "x"]


def test_sequences_different_change_to_one_entry_conflicts():
    raises_conflict(bm.merge_sequences, ["a", "b"], ["a", "x"], ["a", "y"])


def test_sequences_insertions_at_one_place_conflict_unless_equal():
    raises_conflict(bm.merge_sequences, ["a", "b"], ["a", "x", "b"], ["a", "y", "b"])
    assert bm.merge_sequences(["a", "b"], ["a", "x", "b"], ["a", "x", "b"]) == ["a", "x", "b"]


def test_sequences_insertions_at_different_places_both_apply():
    merged = bm.merge_sequences(["a", "b", "c"], ["a", "x", "b", "c"], ["a", "b", "y", "c"])
    assert merged == ["a", "x", "b", "y", "c"]


def test_sequences_removal_next_to_insertion_both_apply():
    assert bm.merge_sequences(["a", "b", "c"], ["a", "c"], ["a", "b", "x", "c"]) == ["a", "x", "c"]


def test_pyright_merge_applies_neighbouring_removals_from_both_sides():
    base = baseline({"./backend/x.py": [entry("F1"), entry("F2"), entry("F3")]})
    ours = baseline({"./backend/x.py": [entry("F2"), entry("F3")]})
    theirs = baseline({"./backend/x.py": [entry("F1"), entry("F3")]})
    assert json.loads(bm.merge_pyright(base, ours, theirs))["files"] == {"./backend/x.py": [entry("F3")]}


def test_pyright_merge_reports_the_file_on_a_conflict():
    base = baseline({"./backend/x.py": [entry("F1")]})
    ours = baseline({"./backend/x.py": [entry("F1", end=6)]})
    theirs = baseline({"./backend/x.py": [entry("F1", end=7)]})
    message = raises_conflict(bm.merge_pyright, base, ours, theirs)
    assert message.startswith("./backend/x.py:"), message


def test_pyright_merge_adds_a_new_file_and_drops_an_emptied_one():
    base = baseline({"./backend/gone.py": [entry("F1")]})
    ours = baseline({"./backend/new.py": [entry("F9")]})
    theirs = baseline({"./backend/gone.py": [entry("F1")]})
    files = json.loads(bm.merge_pyright(base, ours, theirs))["files"]
    assert files == {"./backend/new.py": [entry("F9")]}


def test_render_pyright_is_basedpyrights_layout_on_the_committed_baseline():
    text = (ROOT / ".basedpyright" / "baseline.json").read_text(encoding="utf-8")
    assert bm.render_pyright(bm.parse_pyright(text)) == text


def test_render_pyright_sorts_files_and_drops_empty_lists():
    text = bm.render_pyright({"./b.py": [entry("F2")], "./a.py": [], "./c.py": [entry("F3")]})
    assert list(json.loads(text)["files"]) == ["./b.py", "./c.py"]
    assert not text.endswith("\n")


def test_main_merges_and_writes_ours_then_reports_conflicts():
    with tempfile.TemporaryDirectory() as tmp:
        base, ours, theirs = (Path(tmp) / name for name in ("base", "ours", "theirs"))
        base.write_text(counts_text({("a.py", "F401"): 5}), encoding="utf-8")
        ours.write_text(counts_text({("a.py", "F401"): 4}), encoding="utf-8")
        theirs.write_text(counts_text({("a.py", "F401"): 3}), encoding="utf-8")
        assert bm.main(["counts", str(base), str(ours), str(theirs)]) == 0
        assert lint_baseline.load_counts(ours.read_text(encoding="utf-8")) == {("a.py", "F401"): 2}

        base.write_text(baseline({"./x.py": [entry("F1")]}), encoding="utf-8")
        ours.write_text(baseline({"./x.py": [entry("F1", end=6)]}), encoding="utf-8")
        theirs.write_text(baseline({"./x.py": [entry("F1", end=7)]}), encoding="utf-8")
        before = ours.read_text(encoding="utf-8")
        assert bm.main(["pyright", str(base), str(ours), str(theirs)]) == 1
        assert ours.read_text(encoding="utf-8") == before  # a conflict leaves ours as git gave it


def test_normalize_rewrites_to_the_layout_and_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "baseline.json"
        path.write_text(
            json.dumps({"files": {"./b.py": [entry("F1")]}}), encoding="utf-8"
        )  # indent 2, trailing newline
        assert bm.main(["normalize", str(path)]) == 0
        once = path.read_text(encoding="utf-8")
        assert once == bm.render_pyright({"./b.py": [entry("F1")]})
        assert bm.main(["normalize", str(path)]) == 0
        assert path.read_text(encoding="utf-8") == once
