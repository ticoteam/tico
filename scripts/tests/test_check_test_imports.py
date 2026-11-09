"""No Python file imports a test module: each form of the import is caught, a helpers/ import is not, the allowlist
keeps the deliberate ones, and the message names the helper module to import from."""

import subprocess
from pathlib import Path

from scripts import check_test_imports as guard


def tree(root, files, allowed=""):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    if allowed:
        (root / guard.ALLOWED).parent.mkdir(parents=True, exist_ok=True)
        (root / guard.ALLOWED).write_text(allowed)
    return sorted(files)


def found(root: Path, files, allowed=""):
    return guard.problems(root, tree(root, files, allowed))


def test_every_form_of_importing_a_test_module_is_caught_with_its_helper_named(tmp_path):
    problems = found(
        tmp_path,
        {
            "backend/tests/test_a.py": "from backend.tests.test_b import x\n",
            "backend/tests/test_c.py": "import backend.tests.test_b\n",
            "backend/tests/test_d.py": "from backend.tests import test_b\n",
            "backend/tests/test_e.py": "from .test_b import x\n",
            "backend/tests/helpers/f.py": "def f():\n    from backend.tests.test_b import x\n",
            "setup/tests/test_g.py": "from setup.tests.test_h import y\n",
            "runner_support/tests/test_i.py": "from runner_support.tests.test_j import z\n",
        },
    )
    assert [p.split(" ")[0] for p in problems] == [
        "backend/tests/helpers/f.py:2",
        "backend/tests/test_a.py:1",
        "backend/tests/test_c.py:1",
        "backend/tests/test_d.py:1",
        "backend/tests/test_e.py:1",
        "runner_support/tests/test_i.py:1",
        "setup/tests/test_g.py:1",
    ]
    assert "import from backend.tests.helpers.b instead" in problems[1]
    assert "import from setup.tests.helpers.h instead" in problems[-1]


def test_helpers_and_production_modules_named_test_are_not_test_modules(tmp_path):
    assert not found(
        tmp_path,
        {
            "backend/tests/test_a.py": "from backend.tests.helpers.b import x\nfrom backend import test_receipts\n",
            "backend/review_gate.py": "from . import test_receipts\n",
        },
    )


def test_the_allowlist_keeps_a_pair_or_every_importer_of_a_module(tmp_path):
    allowed = "# why\nbackend/tests/test_a.py -> backend.tests.test_b\n* -> backend.tests.test_api  # moved later\n"
    problems = found(
        tmp_path,
        {
            "backend/tests/test_a.py": "from backend.tests.test_b import x\n",
            "backend/tests/test_c.py": "from backend.tests.test_b import x\nfrom backend.tests.test_api import api\n",
            "backend/tests/test_d.py": "from backend.tests.test_api import api\n",
        },
        allowed,
    )
    assert [p.split(" ")[0] for p in problems] == ["backend/tests/test_c.py:1"]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def test_base_reports_only_the_python_files_the_change_touches(tmp_path, capsys):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    tree(tmp_path, {"backend/tests/test_a.py": "from backend.tests.test_b import x\n"})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base: a violation already on main")
    base = git(tmp_path, "rev-parse", "HEAD")
    tree(tmp_path, {"backend/tests/test_c.py": "from backend.tests.test_b import y\n"})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "change: a new violation")

    assert guard.main(["--base", base], root=tmp_path) == 1
    out = capsys.readouterr().out
    assert out.startswith("backend/tests/test_c.py:1 ")
    assert "test_a.py" not in out

    assert guard.main(["--base", "HEAD"], root=tmp_path) == 0


def test_the_repository_imports_no_test_module_outside_the_allowlist():
    assert guard.problems() == []
