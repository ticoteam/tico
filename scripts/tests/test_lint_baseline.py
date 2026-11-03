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
