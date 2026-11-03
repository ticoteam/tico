"""scripts/lint_baseline.py: the --base growth check follows a moved file."""
import subprocess
import sys
from pathlib import Path

import pytest

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


FORWARDERS = {
    "comment only": ("def jobs(value):  # moved to new.jobs\n    return value\n", False),
    "near-name import": ("from new import jobs_extra as _real\n\n\ndef jobs(value):\n    return _real.run(value)\n", False),
    "a near-name import alone": ("import sys\n\nfrom new import jobs_extra as _real\n\nsys.modules[__name__] = _real\n", False),
    "sys.modules": ('"""Moved."""\nimport sys\n\nfrom new import jobs as _real\n\nsys.modules[__name__] = _real\n', True),
    "import as": ("import sys\n\nimport new.jobs as _real\n\nsys.modules[__name__] = _real\n", True),
    "runpy __main__": ('import runpy\nimport sys\n\nif __name__ == "__main__":\n'
                       '    runpy.run_module("new.jobs", run_name="__main__", alter_sys=True)\n'
                       "else:\n    from new import jobs as _real\n\n    sys.modules[__name__] = _real\n", True),
    "runpy of another module": ('import runpy\n\nrunpy.run_module("new.jobs_extra", run_name="__main__")\n', False),
    "getattr package": ("from new import jobs as _real\n\n\ndef __getattr__(name):\n    return getattr(_real, name)\n", True),
    "forwarder plus a function": ("import sys\n\nfrom new import jobs as _real\n\nsys.modules[__name__] = _real\n\n\n"
                                  "def helper(value):\n    return value\n", False),
    # Each of these hides code in an allowed form's expressions: the file is code, so its allowances stay put.
    "run_module with an init_globals call": ('import runpy\n\nrunpy.run_module("new.jobs", init_globals=print("ran"))\n',
                                             False),
    "run_module with a second positional": ('import runpy\n\nrunpy.run_module("new.jobs", print("ran"))\n', False),
    "run_module with a call as run_name": ('import runpy\n\nrunpy.run_module("new.jobs", run_name=print("ran"))\n', False),
    "__getattr__ with an annotation call": ("from new import jobs as _real\n\n\n"
                                            "def __getattr__(name: print('ran')):\n    return getattr(_real, name)\n",
                                            False),
    "__getattr__ with a return annotation call": ("from new import jobs as _real\n\n\n"
                                                  "def __getattr__(name) -> print('ran'):\n"
                                                  "    return getattr(_real, name)\n", False),
    "__getattr__ whose getattr has a third call": ("from new import jobs as _real\n\n\n"
                                                   "def __getattr__(name):\n    return getattr(_real, name, print('ran'))\n",
                                                   False),
    "__getattr__ whose getattr has a keyword": ("from new import jobs as _real\n\n\n"
                                                "def __getattr__(name):\n"
                                                "    return getattr(_real, name, default=print('ran'))\n", False),
    "sys.modules set to another name": ("import sys\n\nfrom new import jobs as _real\n\nsys.modules[__name__] = print\n",
                                        False),
    "sys.modules with an annotation": ("import sys\n\nfrom new import jobs as _real\n\n"
                                       "sys.modules[__name__]: object = _real\n", False),
    "main test with a call": ('import sys\n\nif __name__ == "__main__" or print("ran"):\n'
                              '    from new import jobs as _real\n\n    sys.modules[__name__] = _real\n', False),
    "target import bound to another name": ("import sys\n\nfrom new import jobs\n\nsys.modules[__name__] = jobs\n",
                                            False),
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
    assert not lint_baseline.forwards_to("old/bash-env", "new/bash-env")    # ruff allowances are Python's only
