"""scripts/baseline_files.py: the piece folders that the lint and type baselines share."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baseline_files  # noqa: E402


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


def test_pieces_at_reads_a_ref_in_path_order_and_ignores_the_working_tree(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / "baselines" / "x" / "sub" / "b.json", "B")
    write_text(tmp_path / "baselines" / "x" / "a.json", "A")
    write_text(tmp_path / "baselines" / "x" / "notes.txt", "skip")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    write_text(tmp_path / "baselines" / "x" / "a.json", "changed, not committed")
    assert baseline_files.pieces_at(tmp_path, "HEAD", "baselines/x") == ["A", "B"]
    assert baseline_files.blobs_at(tmp_path, "HEAD", ["baselines/x/notes.txt"]) == [b"skip"]
    assert baseline_files.pieces_at(tmp_path, "HEAD", "baselines/missing") == []
    assert baseline_files.show_at(tmp_path, "HEAD", "baselines/x/nope.json") is None


def test_write_pieces_writes_the_wanted_pieces_and_prunes_the_rest(tmp_path):
    folder = tmp_path / "baselines"
    baseline_files.write_pieces(folder, {folder / "a.json": "1", folder / "sub" / "b.json": "2"})
    assert [p.relative_to(folder).as_posix() for p in baseline_files.pieces(folder)] == ["a.json", "sub/b.json"]
    baseline_files.write_pieces(folder, {folder / "sub" / "b.json": "3"})
    assert [p.relative_to(folder).as_posix() for p in baseline_files.pieces(folder)] == ["sub/b.json"]
    assert (folder / "sub" / "b.json").read_text(encoding="utf-8") == "3"
    baseline_files.write_pieces(folder, {})
    assert not (folder / "sub").exists()
    assert baseline_files.pieces(folder) == []
    assert baseline_files.pieces(tmp_path / "missing") == []


def test_untrack_stops_tracking_a_file_and_keeps_it_on_disk(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    write_text(tmp_path / "old.json", "{}\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    assert baseline_files.tracked(tmp_path, "old.json")
    assert baseline_files.untrack(tmp_path, ["old.json", "never.json"]) == ["old.json"]
    assert not baseline_files.tracked(tmp_path, "old.json")
    assert (tmp_path / "old.json").exists()
