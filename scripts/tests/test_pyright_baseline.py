"""scripts/pyright_baseline.py: the per-file pieces, the generated single file, migration from it, and the ratchet's growth
check reading pieces at a ref. The tests run on a scratch root, never on the checkout."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_pyright_ratchet  # noqa: E402
import pyright_baseline  # noqa: E402

ENTRY = {"code": "reportAttributeAccessIssue"}
OTHER = {"code": "reportMissingImports"}


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


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def piece(root, name):
    return root / "baselines" / "pyright" / name


def test_split_then_generate_reproduces_the_single_file_byte_for_byte(tmp_path, monkeypatch):
    monkeypatch.setattr(pyright_baseline, "ROOT", tmp_path)
    original = pyright_baseline.render({"./pkg/b.py": [ENTRY, OTHER], "./a.py": [ENTRY], "./clean.py": []})
    write_text(tmp_path / ".basedpyright" / "baseline.json", original)
    pyright_baseline.split()
    folder = tmp_path / "baselines" / "pyright"
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.json")) == ["a.py.json", "pkg/b.py.json"]
    assert pyright_baseline.layout_problems() == []
    pyright_baseline.generate()
    assert (tmp_path / ".basedpyright" / "baseline.json").read_text(encoding="utf-8") == original


def test_a_piece_with_two_files_or_at_the_wrong_path_is_a_layout_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(pyright_baseline, "ROOT", tmp_path)
    write_text(piece(tmp_path, "a.py.json"), pyright_baseline.render({"./a.py": [ENTRY]}))
    assert pyright_baseline.layout_problems() == []
    write_text(piece(tmp_path, "a.py.json"), pyright_baseline.render({"./a.py": [ENTRY], "./b.py": [ENTRY]}))
    problems = pyright_baseline.layout_problems()
    assert len(problems) == 1 and "holds 2 files" in problems[0]
    write_text(piece(tmp_path, "a.py.json"), pyright_baseline.render({"./b.py": [ENTRY]}))
    problems = pyright_baseline.layout_problems()
    assert len(problems) == 1 and "not in the layout" in problems[0]


def test_layout_at_reads_the_single_file_before_the_split_and_the_pieces_after(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / ".basedpyright" / "baseline.json", pyright_baseline.render({"./a.py": [ENTRY]}))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "single file")
    (tmp_path / ".basedpyright" / "baseline.json").unlink()
    write_text(piece(tmp_path, "a.py.json"), pyright_baseline.render({"./a.py": [ENTRY]}))
    write_text(piece(tmp_path, "b.py.json"), pyright_baseline.render({"./b.py": [OTHER]}))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "pieces")
    monkeypatch.setattr(pyright_baseline, "ROOT", tmp_path)
    assert pyright_baseline.layout_at("HEAD~1") == {"./a.py": [ENTRY]}
    assert pyright_baseline.layout_at("HEAD") == {"./a.py": [ENTRY], "./b.py": [OTHER]}
    assert pyright_baseline.layout_at("HEAD~2") is None


def test_migrate_untracks_the_single_file_and_generate_refuses_while_it_is_tracked(tmp_path, monkeypatch):
    text = pyright_baseline.render({"./a.py": [ENTRY], "./b.py": [OTHER]})
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / ".basedpyright" / "baseline.json", text)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "before the split")
    monkeypatch.setattr(pyright_baseline, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        pyright_baseline.generate()
    assert pyright_baseline.migrate() == 0
    assert pyright_baseline.layout_problems() == []
    assert pyright_baseline.load_layout() == {"./a.py": [ENTRY], "./b.py": [OTHER]}
    ls = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files", "--", ".basedpyright/baseline.json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert ls.stdout == ""
    pyright_baseline.generate()
    assert (tmp_path / ".basedpyright" / "baseline.json").read_text(encoding="utf-8") == text


def test_the_ratchet_growth_check_counts_pieces_against_a_ref_with_pieces(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / "a.py", "x = 1\n")
    write_text(piece(tmp_path, "a.py.json"), pyright_baseline.render({"./a.py": [ENTRY]}))
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one entry")
    write_text(piece(tmp_path, "a.py.json"), pyright_baseline.render({"./a.py": [ENTRY, OTHER]}))
    monkeypatch.setattr(pyright_baseline, "ROOT", tmp_path)
    monkeypatch.setattr(check_pyright_ratchet, "ROOT", tmp_path)
    problems = check_pyright_ratchet.growth("HEAD", {}, [])
    assert problems == ["baseline grew: a.py has 2 entries, HEAD had 1; fix the error instead of baselining it"]
