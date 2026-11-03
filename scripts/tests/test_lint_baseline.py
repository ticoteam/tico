"""scripts/lint_baseline.py: the --base growth check follows a moved file."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lint_baseline  # noqa: E402


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@acme.example", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@acme.example", "PATH": "/usr/bin:/bin:/usr/local/bin"})


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
    (tmp_path / "old" / "jobs.py").write_text("from new import jobs as _real\n")    # the forwarder
    (tmp_path / "old" / "plain.py").rename(tmp_path / "new" / "plain.py")          # a plain move
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
    new = {("old/kept.py", "BLE001"), ("new/rewritten.py", "BLE001"), ("new/kept.py", "BLE001"),
           ("new/forwarder.py", "BLE001")}
    carried = lint_baseline.carry_moves(old, new, "HEAD")
    # Only the forwarder's allowance moves, and it leaves its old path: the other two copies show up as growth.
    assert carried == {("old/rewritten.py", "BLE001"), ("old/kept.py", "BLE001"), ("new/forwarder.py", "BLE001")}
    assert new - carried == {("new/rewritten.py", "BLE001"), ("new/kept.py", "BLE001")}
