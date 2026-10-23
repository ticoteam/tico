"""CI selection cannot omit uncertain work or turn an empty/red test run green."""
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import pr_checks as checks

ROOT = Path(__file__).resolve().parents[2]


def test_only_audited_leaves_are_focused_shared_missing_and_main_run_everything(tmp_path):
    leaf = checks.selection(["backend/task_loops.py"])
    assert leaf["python"] == ["backend/tests/test_task_loops.py"]
    assert leaf["browser"] == [] and leaf["caddy"] is False
    for changed in (["conftest.py"], ["backend/store.py"], [".github/workflows/pr-checks.yml"],
                    ["backend/missing.py"], [], ["backend/task_loops.py", "backend/auth.py"]):
        plan = checks.selection(changed)
        assert plan["python"] is None and plan["browser"] is None and plan["caddy"]
    main = checks.selection(["backend/task_loops.py"], "push")
    assert main["python"] is None and main["browser"] is None and main["caddy"]
    ui = checks.selection(["ui/app/chat.js"])
    assert ui["browser"] == ["chat", "imported-history"]
    assert checks.selection(["ui/app/core.js"])["browser"] == ["--all"]
    assert checks.selection(["runner/harnesses/codex.toml"])["conformance"]
    assert checks.selection(["runner-rs/src/sessions.rs"])["conformance"]
    assert checks.selection(["backend/harness_actions.py"])["conformance"]
    # A removed mapped browser test makes the map uncertain, even if its sibling remains.
    (tmp_path / "ui/app").mkdir(parents=True)
    (tmp_path / "ui/app/chat.js").touch()
    assert checks.selection(["ui/app/chat.js"], root=tmp_path)["python"] is None



def test_measured_groups_cover_files_once_and_do_not_repeat_the_old_count_imbalance(tmp_path):
    counts = Counter({"a.py": 1, "b.py": 1, "c.py": 1, "d.py": 100, "new.py": 1})
    times = {"a.py": 12, "b.py": 11, "c.py": 10, "d.py": 1}
    split, loads = checks.groups(counts, times)
    assert sorted(sum(split, [])) == sorted(counts)
    assert max(loads) <= 12 and len(split) == 4
    assert checks.groups(counts, times) == (split, loads)
    checks.groups(counts, {"a.py": float("nan"), "b.py": -1, "c.py": "text"})
    xml = tmp_path / "python-1.xml"
    xml.write_text('<testsuites><testsuite><testcase file="a.py" time="2.5"/>'
                   '<testcase file="a.py" time="1.5"/><testcase file="b.py" time="3"/>'
                   '</testsuite></testsuites>')
    assert checks.durations([xml]) == {"a.py": 4, "b.py": 3}


def test_plan_keeps_full_collection_on_main_and_conftest_and_never_runs_bare_pytest(tmp_path, monkeypatch):
    files = {"backend/tests/test_task_loops.py": 2, "backend/tests/test_auth.py": 10,
             "backend/tests/test_api.py": 30, "scripts/tests/test_release_checks.py": 6}
    monkeypatch.setattr(checks, "collect", lambda: Counter(files))
    guarded = []
    monkeypatch.setattr(checks, "guard_changelog", lambda base: guarded.append(base))
    monkeypatch.setattr(checks, "changed_files", lambda base: ["backend/task_loops.py"])
    target = tmp_path / "plan.json"
    focused = checks.prepare("pull_request", "base", target)
    assert guarded == ["base"]
    assert focused["matrix"]["include"] == [{"suite": "python-1", "files": ["backend/tests/test_task_loops.py"]}]
    main = checks.prepare("push", "base", target)
    assert guarded == ["base"]  # A direct release cut on main is checked by the release guards.
    groups = [j for j in main["matrix"]["include"] if j["suite"].startswith("python-")]
    assert len(groups) == 4 and sorted(sum([j["files"] for j in groups], [])) == sorted(files)
    monkeypatch.setattr(checks, "changed_files", lambda base: ["conftest.py"])
    assert checks.prepare("pull_request", "base", target)["python"] is None
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(checks.subprocess, "run", lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=1))
    target.write_text(json.dumps(focused))
    # Missing selected paths fail before subprocess starts, rather than widening to an implicit full run.
    with pytest.raises(RuntimeError, match="nonempty"):
        checks.run(target, "python-1")
    assert not calls
    (tmp_path / "backend/tests").mkdir(parents=True)
    (tmp_path / "backend/tests/test_task_loops.py").touch()
    assert checks.run(target, "python-1") == 1
    assert calls[-1][-1] == "backend/tests/test_task_loops.py"
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    (tmp_path / "ci-results/python-1.xml").write_text('<testsuite><testcase><skipped/></testcase></testsuite>')
    with pytest.raises(RuntimeError, match="skip every test"):
        checks.run(target, "python-1")


@pytest.mark.slow
def test_pr_changelog_guard_rejects_product_edits_and_accepts_release_cut(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args],
                                       cwd=tmp_path, text=True).strip()

    def commit(message):
        git("add", "-A")
        git("commit", "-qm", message)

    git("init", "-q", "-b", "main")
    notes = tmp_path / "CHANGELOG.md"
    notes.write_text("# Changelog\n## [Unreleased]\n\n## [1.0.0]\n- Shipped.\n")
    commit("Start")
    base = git("rev-parse", "HEAD")
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    monkeypatch.setattr(checks, "collect", lambda: Counter({"scripts/tests/test_pr_checks.py": 1}))
    target = tmp_path / "plan.json"
    # A normal PR without notes passes through the real planning entrypoint.
    checks.prepare("pull_request", base, target)
    target.unlink()
    notes.write_text(notes.read_text().replace("## [Unreleased]\n", "## [Unreleased]\n- Per-PR note.\n"))
    commit("Product change")
    with pytest.raises(RuntimeError, match="No PR edits CHANGELOG.md"):
        checks.prepare("pull_request", base, target)
    assert not target.exists()  # The check fails before generating any test matrix.

    git("checkout", "-qb", "release-notes", base)
    notes.write_text(notes.read_text().replace("## [Unreleased]\n", "## [Unreleased]\n\n## [1.1.0]\n- New release.\n"))
    commit("Release notes\n\nChangelog-Release: v1.1.0")
    checks.prepare("pull_request", base, target)
    target.unlink()
    # An old release trailer cannot bless another edit or a product file.
    (tmp_path / "product.py").write_text("changed = True\n")
    commit("Product change")
    with pytest.raises(RuntimeError, match="No PR edits CHANGELOG.md"):
        checks.guard_changelog(base)
    git("rm", "product.py")
    notes.write_text(notes.read_text() + "- Another ordinary note.\n")
    commit("Another edit")
    with pytest.raises(RuntimeError, match="No PR edits CHANGELOG.md"):
        checks.guard_changelog(base)
    # Deleting/renaming notes is also an edit; missing history never silently passes.
    git("checkout", "-qb", "rename", base)
    git("mv", "CHANGELOG.md", "OLD.md")
    commit("Move notes")
    with pytest.raises(RuntimeError, match="No PR edits CHANGELOG.md"):
        checks.guard_changelog(base)
    with pytest.raises(subprocess.CalledProcessError):
        checks.guard_changelog("missing-base")


def test_install_retry_is_bounded_and_warm_browser_or_python_never_downloads_apt(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "RUNNER_TEMP": str(tmp_path), "CALL_LOG": str(log)}
    def stub(name, body):
        f = bin_dir / name
        f.write_text("#!/bin/bash\n" + body + "\n")
        f.chmod(0o755)
    stub("timeout", 'echo "$*" >> "$CALL_LOG"; exit 1')
    result = subprocess.run(["bash", str(ROOT / "scripts/ci_install.sh"), "python"], env=env, capture_output=True)
    assert result.returncode == 1
    attempts = log.read_text().splitlines()
    assert len(attempts) == 2 and all("--kill-after=10s 300s" in a for a in attempts)
    log.write_text("")
    stub("python3", "if [ \"${1:-}\" = - ]; then printf 'liba\\nlibb\\n'; fi")
    venv = tmp_path / "venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text('#!/bin/bash\nexit 0\n')
    (venv / "python").chmod(0o755)
    for name in ("sudo", "npm", "npx"):
        stub(name, f'echo "{name} $*" >> "$CALL_LOG"')
    cache = tmp_path / "playwright-system"
    cache.mkdir()
    (cache / "complete").touch()
    for suite in ("python", "browser"):
        log.write_text("")
        result = subprocess.run(["bash", str(ROOT / "scripts/ci_install.sh"), "--once", suite], env=env, capture_output=True)
        assert result.returncode == 0, result.stderr.decode()
        calls = log.read_text()
        assert "update" not in calls and "--download-only" not in calls and "--with-deps" not in calls
        if suite == "python":
            assert not calls
        else:
            assert "--no-download install" in calls and "playwright install chromium webkit" in calls


def test_cached_system_packages_match_the_locked_browser_version():
    manifest = json.loads((ROOT / "scripts/playwright-system-packages.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())
    assert manifest["playwright"] == lock["packages"]["node_modules/playwright-core"]["version"]
    assert manifest["platform"] == "ubuntu24.04-x64"
    assert len(set(manifest["packages"])) == len(manifest["packages"]) > 0
