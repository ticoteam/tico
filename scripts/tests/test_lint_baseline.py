"""scripts/lint_baseline.py: the --base growth check follows a moved file, and the per-file pieces, the generated toml,
migration from the single counts file, and growth against a ref in either layout. Tests run on scratch roots only."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lint_baseline  # noqa: E402


def git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@acme.example",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@acme.example",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


def test_a_moved_file_keeps_its_path_even_with_a_forwarder_left_behind(tmp_path, monkeypatch):
    body = "".join(f"def helper_{i}(value):\n    return value + {i}\n\n\n" for i in range(40))
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "jobs.py").write_text(body)
    (tmp_path / "old" / "plain.py").write_text(body.replace("helper", "plain"))
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    (tmp_path / "new").mkdir()
    (tmp_path / "old" / "jobs.py").rename(tmp_path / "new" / "jobs.py")
    (tmp_path / "old" / "jobs.py").write_text("from new import jobs as _real\n")  # the forwarder
    (tmp_path / "old" / "plain.py").rename(tmp_path / "new" / "plain.py")  # a plain move
    git(tmp_path, "add", "-A")
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    assert lint_baseline.moved_since("HEAD") == {"old/jobs.py": "new/jobs.py", "old/plain.py": "new/plain.py"}


def test_a_copy_whose_old_path_still_holds_code_or_a_suppression_carries_nothing(tmp_path, monkeypatch):
    body = "".join(f"def helper_{i}(value):\n    return value + {i}\n\n\n" for i in range(40))
    (tmp_path / "old").mkdir()
    for name in ("rewritten.py", "kept.py", "forwarder.py"):
        (tmp_path / "old" / name).write_text(body.replace("helper", name[:-3]))
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    (tmp_path / "new").mkdir()
    for name in ("rewritten.py", "kept.py", "forwarder.py"):
        (tmp_path / "new" / name).write_text((tmp_path / "old" / name).read_text())
    # A rewrite that still holds code at the old path, a forwarder that keeps a suppression, and a real forwarder.
    (tmp_path / "old" / "rewritten.py").write_text("def rewritten(value):\n    return value\n")
    (tmp_path / "old" / "kept.py").write_text("from new import kept as _real\n")
    (tmp_path / "old" / "forwarder.py").write_text("from new import forwarder as _real\n")
    git(tmp_path, "add", "-A")
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    old = {("old/rewritten.py", "BLE001"), ("old/kept.py", "BLE001"), ("old/forwarder.py", "BLE001")}
    new = {
        ("old/kept.py", "BLE001"),
        ("new/rewritten.py", "BLE001"),
        ("new/kept.py", "BLE001"),
        ("new/forwarder.py", "BLE001"),
    }
    carried = lint_baseline.carry_moves(old, new, "HEAD")
    # Only the forwarder's allowance moves, and it leaves its old path: the other two copies show up as growth.
    assert carried == {("old/rewritten.py", "BLE001"), ("old/kept.py", "BLE001"), ("new/forwarder.py", "BLE001")}
    assert new - carried == {("new/rewritten.py", "BLE001"), ("new/kept.py", "BLE001")}


FORWARDERS = {
    "comment only": ("def jobs(value):  # moved to new.jobs\n    return value\n", False),
    "near-name import": (
        "from new import jobs_extra as _real\n\n\ndef jobs(value):\n    return _real.run(value)\n",
        False,
    ),
    "a near-name import alone": (
        "import sys\n\nfrom new import jobs_extra as _real\n\nsys.modules[__name__] = _real\n",
        False,
    ),
    "sys.modules": (
        '"""Moved."""\nimport sys\n\nfrom new import jobs as _real\n\nsys.modules[__name__] = _real\n',
        True,
    ),
    "import as": ("import sys\n\nimport new.jobs as _real\n\nsys.modules[__name__] = _real\n", True),
    "runpy __main__": (
        'import runpy\nimport sys\n\nif __name__ == "__main__":\n'
        '    runpy.run_module("new.jobs", run_name="__main__", alter_sys=True)\n'
        "else:\n    from new import jobs as _real\n\n    sys.modules[__name__] = _real\n",
        True,
    ),
    "runpy of another module": ('import runpy\n\nrunpy.run_module("new.jobs_extra", run_name="__main__")\n', False),
    "getattr package": (
        "from new import jobs as _real\n\n\ndef __getattr__(name):\n    return getattr(_real, name)\n",
        True,
    ),
    "forwarder plus a function": (
        "import sys\n\nfrom new import jobs as _real\n\nsys.modules[__name__] = _real\n\n\n"
        "def helper(value):\n    return value\n",
        False,
    ),
    # Each of these hides code in an allowed form's expressions: the file is code, so its allowances stay put.
    "run_module with an init_globals call": (
        'import runpy\n\nrunpy.run_module("new.jobs", init_globals=print("ran"))\n',
        False,
    ),
    "run_module with a second positional": ('import runpy\n\nrunpy.run_module("new.jobs", print("ran"))\n', False),
    "run_module with a call as run_name": (
        'import runpy\n\nrunpy.run_module("new.jobs", run_name=print("ran"))\n',
        False,
    ),
    "__getattr__ with an annotation call": (
        "from new import jobs as _real\n\n\ndef __getattr__(name: print('ran')):\n    return getattr(_real, name)\n",
        False,
    ),
    "__getattr__ with a return annotation call": (
        "from new import jobs as _real\n\n\ndef __getattr__(name) -> print('ran'):\n    return getattr(_real, name)\n",
        False,
    ),
    "__getattr__ whose getattr has a third call": (
        "from new import jobs as _real\n\n\ndef __getattr__(name):\n    return getattr(_real, name, print('ran'))\n",
        False,
    ),
    "__getattr__ whose getattr has a keyword": (
        "from new import jobs as _real\n\n\n"
        "def __getattr__(name):\n"
        "    return getattr(_real, name, default=print('ran'))\n",
        False,
    ),
    "sys.modules set to another name": (
        "import sys\n\nfrom new import jobs as _real\n\nsys.modules[__name__] = print\n",
        False,
    ),
    "sys.modules with an annotation": (
        "import sys\n\nfrom new import jobs as _real\n\nsys.modules[__name__]: object = _real\n",
        False,
    ),
    "main test with a call": (
        'import sys\n\nif __name__ == "__main__" or print("ran"):\n'
        '    from new import jobs as _real\n\n    sys.modules[__name__] = _real\n',
        False,
    ),
    "target import bound to another name": (
        "import sys\n\nfrom new import jobs\n\nsys.modules[__name__] = jobs\n",
        False,
    ),
}


@pytest.mark.parametrize("case", list(FORWARDERS))
def test_only_a_real_forwarder_takes_a_copied_files_allowances_along(case, tmp_path, monkeypatch):
    text, moves = FORWARDERS[case]
    body = "".join(f"def helper_{i}(value):\n    return value + {i}\n\n\n" for i in range(40))
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "jobs.py").write_text(body)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "jobs.py").write_text(body)
    (tmp_path / "old" / "jobs.py").write_text(text)
    git(tmp_path, "add", "-A")
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    assert lint_baseline.forwards_to("old/jobs.py", "new/jobs.py") is moves
    carried = lint_baseline.carry_moves({("old/jobs.py", "BLE001")}, {("new/jobs.py", "BLE001")}, "HEAD")
    assert carried == {("new/jobs.py" if moves else "old/jobs.py", "BLE001")}


def test_a_package_main_forwarder_and_a_shell_file(tmp_path, monkeypatch):
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "__main__.py").write_text('import runpy\n\nrunpy.run_module("new", run_name="__main__")\n')
    (tmp_path / "old" / "bash-env").write_text('. "${BASH_ENV%/old/bash-env}/new/bash-env"\n')
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    assert lint_baseline.forwards_to("old/__main__.py", "new/__main__.py")
    assert not lint_baseline.forwards_to("old/__main__.py", "new/jobs.py")
    assert not lint_baseline.forwards_to("old/bash-env", "new/bash-env")  # ruff allowances are Python's only


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(root, message):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def test_a_count_change_touches_only_that_files_piece(tmp_path, monkeypatch):
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    counts = {("backend/a.py", "BLE001"): 2, ("backend/b.py", "BLE001"): 1, ("backend/b.py", "F401"): 3}
    lint_baseline.write_counts(counts)
    folder = tmp_path / "baselines" / "ruff"
    before = {p: p.read_text(encoding="utf-8") for p in folder.rglob("*.json")}
    assert sorted(p.relative_to(folder).as_posix() for p in before) == ["backend/a.py.json", "backend/b.py.json"]
    lint_baseline.write_counts({**counts, ("backend/a.py", "BLE001"): 1})
    after = {p: p.read_text(encoding="utf-8") for p in folder.rglob("*.json")}
    assert [p.name for p in before if before[p] != after[p]] == ["a.py.json"]
    assert lint_baseline.read_counts() == {**counts, ("backend/a.py", "BLE001"): 1}


def test_a_file_with_no_findings_loses_its_piece(tmp_path, monkeypatch):
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    lint_baseline.write_counts({("backend/a.py", "BLE001"): 1, ("backend/b.py", "F401"): 1})
    lint_baseline.write_counts({("backend/b.py", "F401"): 1})
    assert not (tmp_path / "baselines" / "ruff" / "backend" / "a.py.json").exists()
    assert (tmp_path / "baselines" / "ruff" / "backend" / "b.py.json").exists()


def test_generate_extends_ruff_toml_and_lists_every_pair_of_the_pieces(tmp_path, monkeypatch):
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    write_text(tmp_path / "ruff.toml", '[lint]\nselect = ["F", "B"]\n')
    lint_baseline.write_counts({("backend/b.py", "F401"): 1, ("backend/a.py", "BLE001"): 2})
    assert lint_baseline.generate() == {("backend/b.py", "F401"): 1, ("backend/a.py", "BLE001"): 2}
    text = (tmp_path / "ruff-baseline.toml").read_text(encoding="utf-8")
    assert text.startswith('extend = "ruff.toml"\n')
    assert text.endswith('[lint.per-file-ignores]\n"backend/a.py" = ["BLE001"]\n"backend/b.py" = ["F401"]\n')


def test_the_gate_names_the_baseline_and_the_raw_run_names_ruff_toml_alone():
    gate = lint_baseline.ruff_command(["--output-format", "concise"])
    assert gate[gate.index("--config") + 1] == "ruff-baseline.toml"
    assert "lint.per-file-ignores = {}" not in gate
    raw = lint_baseline.ruff_command([], raw=True)
    assert raw[raw.index("--config") + 1] == "ruff.toml"
    assert "ruff-baseline.toml" not in raw
    assert "lint.per-file-ignores = {}" in raw


def test_a_piece_with_two_files_or_at_the_wrong_path_is_a_layout_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    lint_baseline.write_counts({("backend/a.py", "BLE001"): 1})
    assert lint_baseline.layout_problems() == []
    piece = tmp_path / "baselines" / "ruff" / "backend" / "a.py.json"
    write_text(piece, json.dumps({"backend/a.py": {"BLE001": 1}, "backend/b.py": {"F401": 1}}, indent=2) + "\n")
    problems = lint_baseline.layout_problems()
    assert len(problems) == 1 and "holds 2 files" in problems[0]
    write_text(
        tmp_path / "baselines" / "ruff" / "elsewhere.json", lint_baseline.render_piece("backend/a.py", {"BLE001": 1})
    )
    write_text(piece, lint_baseline.render_piece("backend/a.py", {"BLE001": 1}))
    problems = lint_baseline.layout_problems()
    assert len(problems) == 1 and "not in the layout" in problems[0]


def test_migrate_turns_the_tracked_counts_file_into_pieces_and_untracks_both_old_files(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / "ruff.toml", '[lint]\nselect = ["F", "B"]\n')
    old_counts = {"backend/a.py": {"BLE001": 2}, "backend/b.py": {"F401": 1}}
    write_text(tmp_path / "ruff-baseline-counts.json", json.dumps(old_counts, indent=2, sort_keys=True) + "\n")
    write_text(tmp_path / "ruff-baseline.toml", "# the single file of before the split\n[lint.per-file-ignores]\n")
    commit(tmp_path, "before the split")
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        lint_baseline.generate()  # the old files are still tracked: refuse
    assert lint_baseline.migrate() == 0
    assert lint_baseline.read_counts() == {("backend/a.py", "BLE001"): 2, ("backend/b.py", "F401"): 1}
    assert not (tmp_path / "ruff-baseline-counts.json").exists()
    tracked = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files", "--", "ruff-baseline-counts.json", "ruff-baseline.toml"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert tracked.stdout == ""
    text = (tmp_path / "ruff-baseline.toml").read_text(encoding="utf-8")
    assert text.endswith('[lint.per-file-ignores]\n"backend/a.py" = ["BLE001"]\n"backend/b.py" = ["F401"]\n')
    assert lint_baseline.migrate() == 0  # nothing left to migrate


def test_the_growth_check_reads_a_ref_from_before_the_split(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / "ruff.toml", '[lint]\nselect = ["B"]\n')
    write_text(tmp_path / "ruff-baseline-counts.json", json.dumps({"backend/a.py": {"BLE001": 1}}, indent=2) + "\n")
    commit(tmp_path, "before the split")
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    lint_baseline.write_counts({("backend/a.py", "BLE001"): 2})
    problems = lint_baseline.check_growth("HEAD")
    assert "count grew: backend/a.py BLE001 1 -> 2 (fix the findings; a baseline may only shrink)" in problems
    lint_baseline.write_counts({("backend/a.py", "BLE001"): 1})
    assert lint_baseline.check_growth("HEAD") == []


def test_the_growth_check_reads_pieces_at_a_ref_from_after_the_split(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / "ruff.toml", '[lint]\nselect = ["B"]\n')
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    lint_baseline.write_counts({("backend/a.py", "BLE001"): 1})
    commit(tmp_path, "pieces")
    lint_baseline.write_counts({("backend/a.py", "BLE001"): 2})
    assert lint_baseline.check_growth("HEAD") == [
        "count grew: backend/a.py BLE001 1 -> 2 (fix the findings; a baseline may only shrink)"
    ]


def test_a_file_split_into_a_package_takes_its_suppressions_to_the_parts_it_now_imports(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / "ruff.toml", '[lint]\nselect = ["B", "BLE"]\n')
    write_text(tmp_path / "clients/table.py", "def big():\n    pass\n")
    monkeypatch.setattr(lint_baseline, "ROOT", tmp_path)
    lint_baseline.write_counts({("clients/table.py", "BLE001"): 2})
    commit(tmp_path, "one file")
    # The facade imports from both parts; clients/other.py is new and nothing imports it.
    write_text(tmp_path / "clients/table.py", "from clients.parts.a import x\nfrom clients.parts.b import y\n")
    write_text(tmp_path / "clients/parts/a.py", "x = 1\n")
    write_text(tmp_path / "clients/parts/b.py", "y = 2\n")
    write_text(tmp_path / "clients/other.py", "z = 3\n")
    lint_baseline.write_counts({("clients/parts/a.py", "BLE001"): 1, ("clients/parts/b.py", "BLE001"): 1})
    assert lint_baseline.check_growth("HEAD") == []
    lint_baseline.write_counts({("clients/parts/a.py", "BLE001"): 2, ("clients/parts/b.py", "BLE001"): 1})
    assert lint_baseline.check_growth("HEAD") == [
        "count grew: BLE001 split out of clients/table.py 2 -> 3 in clients/parts/a.py, clients/parts/b.py "
        "(fix the findings; a baseline may only shrink)"
    ]
    lint_baseline.write_counts({("clients/parts/a.py", "BLE001"): 1, ("clients/other.py", "BLE001"): 1})
    assert lint_baseline.check_growth("HEAD") == [
        "new suppression: BLE001 in clients/other.py (fix it, or get it reviewed as a baseline change)",
        "count grew: clients/other.py BLE001 0 -> 1 (fix the findings; a baseline may only shrink)",
    ]
