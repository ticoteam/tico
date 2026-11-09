"""CI selection cannot omit uncertain work or turn an empty/red test run green."""

from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
import weakref

import pytest
import yaml

from scripts import flake_quarantine as quarantine
from scripts import pr_checks as checks

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def repo_map():
    return checks.DependencyMap(ROOT)


@pytest.fixture
def mapped(monkeypatch, repo_map):
    """Selections on this checkout reuse one measured map; temporary trees build their own."""
    build = checks.DependencyMap
    monkeypatch.setattr(checks, "DependencyMap", lambda root: repo_map if root == ROOT else build(root))


def test_uncertain_changes_run_the_smoke_set_never_everything_and_ui_keeps_its_map(tmp_path, mapped):
    smoke_tests, smoke_scripts = checks.smoke(ROOT)
    for changed in (
        ["conftest.py"],
        ["backend/tests/conftest.py"],
        ["requirements.txt"],
        ["pytest.ini"],
        ["package-lock.json"],
        ["backend/missing.py"],
        [],
    ):
        plan = checks.selection(changed)
        assert plan["python"] is not None and plan["browser"] is not None, changed
        assert set(smoke_tests) <= set(plan["python"]) and set(smoke_scripts) <= set(plan["browser"]), changed
        assert "--all" not in plan["browser"] and plan["uncertain"], changed
    assert checks.selection(["docs/missing.md"])["python"] is not None
    main = checks.selection(["backend/task_loops.py"], "push")
    assert main["python"] is None and main["browser"] is None and main["caddy"]
    ui = checks.selection(["ui/app/chat.ts"])
    # The audited UI map's scripts run, with any core script the dependency map finds using the file.
    assert {"chat", "imported-history"} <= set(ui["browser"]) and "--all" not in ui["browser"]
    # A UI file the map does not list runs its linked scripts and the smoke browser scripts, never every script.
    core = checks.selection(["ui/app/core.ts"])
    assert "--all" not in core["browser"] and set(checks.browser_scripts([])) <= set(core["browser"])
    assert checks.selection(["runner-rs/harnesses/codex.toml"])["conformance"]
    assert checks.selection(["runner-rs/src/sessions.rs"])["conformance"]
    assert checks.selection(["backend/harness_actions.py"])["conformance"]
    # A removed mapped browser test runs the smoke browser scripts, even if its sibling remains.
    smoke_tree(tmp_path)
    (tmp_path / "ui/app").mkdir(parents=True)
    (tmp_path / "ui/app/chat.ts").touch()
    missing = checks.selection(["ui/app/chat.ts"], root=tmp_path)
    assert "--all" not in missing["browser"] and set(smoke_scripts) <= set(missing["browser"]) and missing["uncertain"]


def test_measured_groups_cover_files_once_and_do_not_repeat_the_old_count_imbalance(tmp_path):
    counts = Counter({"a.py": 1, "b.py": 1, "c.py": 1, "d.py": 100, "new.py": 1})
    times = {"a.py": 12, "b.py": 11, "c.py": 10, "d.py": 1}
    split, loads = checks.groups(counts, times, total=4)
    assert sorted(sum(split, [])) == sorted(counts)
    assert max(loads) <= 12 and len(split) == 4
    assert checks.groups(counts, times, total=4) == (split, loads)
    checks.groups(counts, {"a.py": float("nan"), "b.py": -1, "c.py": "text"})
    xml = tmp_path / "python-1.xml"
    xml.write_text(
        '<testsuites><testsuite><testcase file="a.py" time="2.5"/>'
        '<testcase file="a.py" time="1.5"/><testcase file="b.py" time="3"/>'
        '</testsuite></testsuites>'
    )
    assert checks.durations([xml]) == {"a.py": 4, "b.py": 3}


def test_plan_keeps_full_collection_on_main_and_conftest_and_never_runs_bare_pytest(tmp_path, monkeypatch, mapped):
    files = {
        "backend/tests/test_task_loops.py": 2,
        "backend/tests/test_auth.py": 10,
        "backend/tests/test_api.py": 30,
        "scripts/tests/test_release_checks.py": 6,
        "backend/tests/test_health.py": 1,
        "backend/tests/test_system_routines.py": 1,
        "backend/tests/test_scheduler.py": 1,
    }
    monkeypatch.setattr(checks, "collect", lambda: Counter(files))
    guarded = []
    monkeypatch.setattr(checks, "guard_changelog", lambda base: guarded.append(base))
    imaged = []
    monkeypatch.setattr(checks, "image_checks", lambda changed: imaged.append(changed))
    monkeypatch.setattr(
        checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["backend/task_loops.py"]
    )
    target = tmp_path / "plan.json"
    timing_dir = tmp_path / "previous" / "pr-python-timings-python-1"
    timing_dir.mkdir(parents=True)
    (timing_dir / "durations.json").write_text(json.dumps({"backend/tests/test_task_loops.py": 100}))
    focused = checks.prepare("pull_request", "base", target, timing_dir.parent)
    assert guarded == ["base"] and imaged == [["backend/task_loops.py"]]
    assert focused["timed_files"] == 1
    assert (
        sorted(sum([j["files"] for j in focused["matrix"]["include"] if j["suite"].startswith("python-")], []))
        == sorted(set(checks.selection(["backend/task_loops.py"])["python"]) & set(files))
        == sorted(files)
    )
    main = checks.prepare("push", "base", target)
    assert guarded == ["base"]  # A direct release cut on main is checked by the release guards.
    groups = [j for j in main["matrix"]["include"] if j["suite"].startswith("python-")]
    # 48 measured seconds fit one VM; the time target, not a fixed count, sets the number of groups.
    assert len(groups) == 1 and sorted(sum([j["files"] for j in groups], [])) == sorted(files)
    monkeypatch.setattr(checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["conftest.py"])
    # A root conftest runs the smoke set and the tests that name it, never all of the tests below it.
    conftest_python = checks.prepare("pull_request", "base", target)["python"]
    assert set(checks.smoke(ROOT)[0]) & set(files) <= set(conftest_python) <= set(files)
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(
        checks.subprocess, "run", lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=1)
    )
    focused["matrix"] = {"include": [{"suite": "python-1", "files": ["backend/tests/test_task_loops.py"]}]}
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


def test_contract_inputs_run_even_without_a_static_dependency_and_deletions_run_too(tmp_path):
    # A new tool, response field or command reference can change the contract without a Python import edge.
    smoke_tree(tmp_path)
    for name in (
        "backend/routes/tasks.py",
        "clients/hubtools.py",
        "clients/hubcli.py",
        "templates/bot/role.md",
        "skills/sample/SKILL.md",
        "backend/company_file/roles.json",
        "e2e/goldens/events.json",
        "roles/ops.md",
        "company/about.md",
        "companies/acme.yaml",
    ):
        source(tmp_path, name, "")
        assert checks.selection([name], root=tmp_path, core=[])["contracts"], name
    # Removing a template must still catch refusal entries that have become stale.
    (tmp_path / "templates/bot/role.md").unlink()
    assert checks.selection(["templates/bot/role.md"], root=tmp_path)["contracts"]
    source(tmp_path, "ui/app/unrelated.js", "")
    assert not checks.selection(["ui/app/unrelated.js"], root=tmp_path, core=[])["contracts"]
    assert not checks.selection(["README.md"], root=tmp_path)["contracts"]
    # An empty diff runs the smoke set, which has no contract test; the contracts job follows its inputs only.
    assert not checks.selection([], root=tmp_path)["contracts"]
    assert checks.selection(["README.md"], event="push", root=tmp_path)["contracts"]


def test_contract_job_survives_default_deselection_and_runs_slow_tests_in_parallel(tmp_path, monkeypatch):
    # Default collection has one fast MCP test and no CLI/golden tests. The contracts job must bypass that filter.
    picked = {
        "python": list(checks.CONTRACT_TESTS),
        "browser": [],
        "caddy": True,
        "conformance": False,
        "rust": [],
        "contracts": True,
    }
    monkeypatch.setattr(checks, "selection", lambda *a, **kw: dict(picked))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(checks, "changed_files", lambda *a, **kw: [])
    monkeypatch.setattr(checks, "collect", lambda: Counter({checks.CONTRACT_TESTS[0]: 1}))
    target = tmp_path / "plan.json"
    plan = checks.prepare("pull_request", "base", target)
    jobs = plan["matrix"]["include"]
    assert plan["python"] == [checks.CONTRACT_TESTS[0]]
    assert jobs == [
        {"suite": "python-1", "files": [checks.CONTRACT_TESTS[0]]},
        {"suite": "contracts", "files": list(checks.CONTRACT_TESTS)},
    ]
    # Even a plan with no default tests must retain the contracts job.
    monkeypatch.setattr(checks, "collect", Counter)
    assert checks.prepare("pull_request", "base", target)["matrix"]["include"] == [jobs[1]]
    for name in checks.CONTRACT_TESTS:
        source(tmp_path, name, "")
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(
        checks.subprocess, "run", lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=1)
    )
    assert checks.run(target, "contracts") == 1  # A red contract fails the matrix and the required timings gate.
    assert calls == [
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            str(checks.WORKERS),
            *checks.PYTEST_FLAGS,
            "-o",
            "junit_family=xunit1",
            "--junitxml=ci-results/contracts.xml",
            "-m",
            "slow",
            *checks.CONTRACT_TESTS,
        ]
    ]
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    report = tmp_path / "ci-results/contracts.xml"
    report.write_text("<testsuite><testcase><skipped/></testcase></testsuite>")
    with pytest.raises(RuntimeError, match="skip every test"):
        checks.run(target, "contracts")
    report.write_text("<testsuite><testcase/></testsuite>")
    assert checks.run(target, "contracts") == 0


def test_rust_crates_run_cargo_test_when_their_inputs_change_and_a_red_crate_fails_the_job(
    tmp_path, monkeypatch, mapped
):
    for changed, crates in (
        (["runner-rs/src/state.rs"], ["runner-rs"]),
        (["relay-rs/src/gateway.rs"], ["relay-rs"]),
        (["runner_support/redact.py"], ["runner-rs"]),
        (["runner-rs/Cargo.lock", "relay-rs/Cargo.lock"], ["relay-rs", "runner-rs"]),
        (["backend/tests/fixtures/gateway_enroll_contract.json"], ["runner-rs"]),
    ):
        for event in ("pull_request", "push"):
            plan = checks.selection(changed, event)
            assert plan["rust"] == crates and plan["conformance"], (changed, event)
    for changed in (["backend/harness_actions.py"], ["backend/task_loops.py"], ["ui/app/chat.ts"], []):
        assert checks.selection(changed)["rust"] == [] and checks.selection(changed, "push")["rust"] == []
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_task_loops.py": 1}))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(
        checks,
        "changed_files",
        lambda base, head="HEAD", only=None: [] if only else ["relay-rs/src/gateway.rs", "runner-rs/src/state.rs"],
    )
    target = tmp_path / "plan.json"
    jobs = checks.prepare("pull_request", "base", target)["matrix"]["include"]
    assert {"suite": "conformance", "files": [checks.CONFORMANCE], "crates": ["relay-rs", "runner-rs"]} in jobs

    monkeypatch.setattr(checks, "ROOT", tmp_path)
    (tmp_path / "runner_support/tests").mkdir(parents=True)
    (tmp_path / checks.CONFORMANCE).touch()
    (tmp_path / "ci-results").mkdir()
    (tmp_path / "ci-results/conformance.xml").write_text("<testsuite><testcase/></testsuite>")
    calls = []

    def fake(command, cwd, env=None):
        calls.append((command, cwd, env and env["CARGO_TARGET_DIR"]))
        return SimpleNamespace(returncode=101 if cwd == tmp_path / "relay-rs" else 0)

    monkeypatch.setattr(checks.subprocess, "run", fake)
    monkeypatch.setattr(checks.shutil, "which", lambda name: "/usr/bin/cargo")
    # relay-rs is red: runner-rs and the conformance tests still run, and the job fails.
    assert checks.run(target, "conformance") == 1
    assert [c[:2] for c in calls[:2]] == [
        (["cargo", "test", "--locked"], tmp_path / "relay-rs"),
        (["cargo", "test", "--locked"], tmp_path / "runner-rs"),
    ]
    assert [c[2] for c in calls[:2]] == [
        str(tmp_path / "runner-rs/target/relay-rs"),
        str(tmp_path / "runner-rs/target"),
    ]
    assert calls[2][0][-1] == checks.CONFORMANCE and len(calls) == 3
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="cargo is not installed"):
        checks.run(target, "conformance")


@pytest.mark.slow
def test_pr_changelog_guard_rejects_product_edits_and_accepts_release_cut(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=tmp_path, text=True
        ).strip()

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
    # Bringing main's own notes edit in is a clean union; a note the merge itself adds is an ordinary edit.
    release = git("rev-parse", "HEAD")
    git("checkout", "-qb", "main-notes", base)
    notes.write_text(notes.read_text() + "- Corrected on main.\n")
    commit("Main notes fix")
    main = git("rev-parse", "HEAD")
    git("checkout", "-qb", "union", release)
    git("merge", "-q", "--no-edit", main)
    checks.guard_changelog(main)
    git("checkout", "-qb", "slipped", release)
    git("merge", "-q", "--no-commit", main)
    notes.write_text(notes.read_text().replace("## [1.1.0]\n", "## [1.1.0]\n- Slipped in by the merge.\n"))
    commit("Merge main")
    with pytest.raises(RuntimeError, match="No PR edits CHANGELOG.md"):
        checks.guard_changelog(main)
    git("checkout", "-q", "release-notes")
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
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path),
        "CALL_LOG": str(log),
    }

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
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/ci_install.sh"), "--once", suite], env=env, capture_output=True
        )
        assert result.returncode == 0, result.stderr.decode()
        calls = log.read_text()
        assert "update" not in calls and "--download-only" not in calls and "--with-deps" not in calls
        if suite == "python":
            assert calls.splitlines() == [
                "npm ci --fetch-retries=1 --no-audit --no-fund --prefer-offline",
                "npm run --silent build:ui",
            ]
        else:
            assert "--no-download install" in calls and "playwright install chromium webkit" in calls


def test_cached_system_packages_match_the_locked_browser_version():
    manifest = json.loads((ROOT / "scripts/playwright-system-packages.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())
    assert manifest["playwright"] == lock["packages"]["node_modules/playwright-core"]["version"]
    assert manifest["platform"] == "ubuntu24.04-x64"
    assert len(set(manifest["packages"])) == len(manifest["packages"]) > 0


def test_install_keeps_a_restored_venv_that_starts_and_still_installs_the_lock_into_it(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    venv = tmp_path / "venv/bin"
    venv.mkdir(parents=True)

    def stub(path, body):
        path.write_text(f'#!/bin/bash\necho "{path.name} $*" >> "{log}"\n{body}\n')
        path.chmod(0o755)

    for name in ("python3", "npm"):
        stub(bin_dir / name, "")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path)}
    for starts in (True, False):
        log.write_text("")
        # A restored venv whose interpreter cannot start (`-c ''` fails) is rebuilt; pip runs in both cases.
        stub(venv / "python", "" if starts else 'if [ "$1" = -c ]; then exit 1; fi')
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/ci_install.sh"), "--once", "python"], env=env, capture_output=True
        )
        assert result.returncode == 0, result.stderr.decode()
        calls = log.read_text().splitlines()
        assert any(c.startswith("python -m pip install") and "-r backend/requirements-dev.txt" in c for c in calls)
        assert (f"python3 -m venv --clear {tmp_path}/venv" in calls) is not starts, calls


def test_free_disk_removes_the_unused_sdks_only_under_the_space_a_job_needs(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    (bin_dir / "df").write_text(
        '#!/bin/bash\nif [ "$1" = -h ]; then echo "/dev/root 145G"; else printf "Avail\\n  85G\\n"; fi\n'
    )
    (bin_dir / "sudo").write_text(f'#!/bin/bash\necho "sudo $*" >> "{log}"\n')
    for tool in ("df", "sudo"):
        (bin_dir / tool).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    for need, removes in (("30", False), ("85", False), ("86", True)):
        log.write_text("")
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/ci/free_disk.sh"), need], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "/dev/root 145G" in result.stdout
        assert log.read_text().split() == (
            ["sudo", "rm", "-rf", "/usr/share/dotnet", "/usr/local/lib/android", "/opt/ghc"] if removes else []
        ), need
    assert subprocess.run(["bash", str(ROOT / "scripts/ci/free_disk.sh")], env=env, capture_output=True).returncode


def test_docs_plan_has_an_explicit_no_test_job_and_never_calls_pytest(tmp_path, monkeypatch, mapped):
    # Exercise selector inputs independently of the separate release-notes guard.
    monkeypatch.setattr(checks, "guard_changelog", lambda _: None)
    for read, reader in (
        ("docs/openapi/v2.json", "backend/tests/test_openapi_v2.py"),
        ("CHANGELOG.md", "scripts/tests/test_release_checklist.py"),
    ):
        both = checks.selection(["backend/task_loops.py", read])["python"]
        assert reader in both and "backend/tests/test_scheduler.py" in both, read
    monkeypatch.setattr(
        checks,
        "changed_files",
        lambda _, head="HEAD", only=None: [] if only else ["docs/images/removed.png", "README.md"],
    )
    monkeypatch.setattr(checks, "collect", lambda: pytest.fail("docs must not collect tests"))
    target = tmp_path / "plan.json"
    plan = checks.prepare("pull_request", "base", target)
    assert plan["python"] == plan["browser"] == [] and not plan["caddy"]
    assert plan["matrix"]["include"] == [{"suite": "selection", "files": []}]
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **k: pytest.fail("must not run tests"))
    assert checks.run(target, "selection") == 0
    plan["python"] = None
    target.write_text(json.dumps(plan))
    with pytest.raises(RuntimeError, match="explicitly unaffected"):
        checks.run(target, "selection")
    assert checks.selection(["docs/example.md"], "push")["python"] is None


def source(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def smoke_tree(root):
    """A temporary tree reads its own copy of the smoke set (rules 2 to 4 run it)."""
    source(root, checks.SMOKE_FILE, (ROOT / checks.SMOKE_FILE).read_text())


def test_hub_change_selects_every_test_that_imports_it_transitively_and_no_other(tmp_path, mapped):
    # A hub imported through a shared helper (the way most tests reach backend.app through test_api) selects every
    # test that imports the helper, at module level, in a function or by importlib. A conftest fixture body that runs
    # it is not followed: its users get the smoke set instead (rule 2).
    source(tmp_path, "backend/__init__.py", "")
    source(tmp_path, "backend/hub.py", "VALUE = 1\n")
    source(tmp_path, "backend/app.py", "from . import hub\n")
    source(tmp_path, "backend/leaf.py", "LEAF = 1\n")
    source(
        tmp_path,
        "backend/tests/conftest.py",
        "import pytest\n@pytest.fixture\ndef app():\n    from backend import app\n    return app\n",
    )
    source(tmp_path, "backend/tests/test_api.py", "from backend.app import hub\ndef test_api(): pass\n")
    source(tmp_path, "backend/tests/test_uses_helper.py", "from .test_api import hub\ndef test_x(): pass\n")
    source(tmp_path, "backend/tests/test_late.py", "def test_x():\n    import backend.app\n")
    source(tmp_path, "backend/tests/test_dynamic.py", "import importlib\nimportlib.import_module('backend.app')\n")
    source(tmp_path, "backend/tests/test_fixture.py", "def test_x(app): pass\n")
    source(tmp_path, "backend/tests/test_leaf.py", "from backend.leaf import LEAF\ndef test_x(): pass\n")
    smoke_tree(tmp_path)
    smoke_tests, _ = checks.smoke(tmp_path)
    hub = checks.selection(["backend/hub.py"], root=tmp_path, core=[])
    direct = {
        "backend/tests/test_api.py",
        "backend/tests/test_dynamic.py",
        "backend/tests/test_late.py",
        "backend/tests/test_uses_helper.py",
    }
    assert direct <= set(hub["python"]) and set(smoke_tests) <= set(hub["python"])
    assert "backend/tests/test_fixture.py" not in hub["python"]
    # A measured change runs only the browser scripts it touches (none here); the smoke browser set is for uncertain ones.
    assert hub["browser"] == [] and not hub["caddy"]
    assert hub["reason"].startswith("measured dependency map")
    assert checks.selection(["backend/leaf.py"], root=tmp_path, core=[])["python"] == ["backend/tests/test_leaf.py"]
    # A computed import may load anything, so the file that makes it runs whatever changes.
    source(tmp_path, "backend/tests/test_any.py", "import importlib\nimportlib.import_module(name)\n")
    assert "backend/tests/test_any.py" in checks.selection(["backend/leaf.py"], root=tmp_path, core=[])["python"]
    source(tmp_path, "backend/broken.py", "syntax error !\n")
    assert set(smoke_tests) <= set(checks.selection(["backend/broken.py"], root=tmp_path, core=[])["python"])
    # On this checkout: a test-only change runs that test and the few that import, read or list it (or every file,
    # like test_local_imports), not the suite; the scheduler's guard follows
    # task_loops; the shared helper reaches the Caddy container test through caddy_assets.
    # test_ddl_guard scans all of backend/ (it skips tests at run time, which the map cannot see), so it is the twentieth;
    # test_schema_every_table reads every table the source makes in the same way, so it is the twenty-first;
    # test_system_jobs_list scans all of backend/ for recorded job keys, so it is one more.
    # test_entities_visible_lint scans all of backend/ for reads of the Directory's tables, so it is one more.
    # Script tests that import pr_checks.py (release_train, release_increment, release_notes, release_workflow,
    # hosted_release_check, main_red) or name a script that runs it (check_conflicts' hook stub), and the three
    # baseline test modules the OWNED_DATA table names by path, run for any test file change too: about 36 files.
    # The bound leaves room for a few more such script tests, so adding one doesn't fail every queue group behind it;
    # it is still a small share of the suite's ~1,500 test files.
    alone = checks.selection(["backend/tests/test_task_loops.py"])["python"]
    assert "backend/tests/test_task_loops.py" in alone and len(alone) < 50
    loops = checks.selection(["backend/task_loops.py"])
    assert "backend/tests/test_scheduler.py" in loops["python"] and not loops["caddy"]
    assert checks.selection(["backend/caddy_assets.py"])["caddy"]


def test_the_map_holds_one_syntax_tree_at_a_time(tmp_path, monkeypatch):
    # Every tree held at once peaked near 2.3 GB on this checkout, which the allocator keeps: four workers that each
    # built the map ran a merge queue VM out of memory. Each tree is let go before the next file is parsed.
    for n in range(3):
        source(tmp_path, f"m{n}.py", "import os\nVALUE = 1\n")
    source(tmp_path, "broken.py", "syntax error !\n")
    parse, trees, held = checks.ast.parse, [], []

    def tracked(*args, **kwargs):
        held.append(sum(tree() is not None for tree in trees))  # trees still alive when the next parse starts
        tree = parse(*args, **kwargs)
        trees.append(weakref.ref(tree))
        return tree

    monkeypatch.setattr(checks.ast, "parse", tracked)
    graph = checks.DependencyMap(tmp_path)
    monkeypatch.undo()  # pytest parses source itself to report a failure
    assert max(held) <= 1, held  # at most the one the caller is about to replace
    assert len(trees) == 6 and graph.unparsed == {"broken.py"}  # each readable file twice: imports, then the scans


def test_docs_changelog_and_workflow_edits_run_the_tests_that_read_them_not_the_suite(tmp_path, mapped):
    # The manual reads docs/ and the app imports the manual: a page edit runs the tests that read docs/ and the tests of
    # the manual itself, not every test that imports the app. A deleted page still runs everything.
    source(tmp_path, "backend/__init__.py", "")
    source(
        tmp_path,
        "backend/manual.py",
        "from pathlib import Path\nDOCS = Path(__file__).resolve().parents[1] / 'docs'\n"
        "def pages(): return [p.read_text() for p in DOCS.glob('*.md')]\n",
    )
    source(tmp_path, "backend/app.py", "from . import manual\n")
    source(tmp_path, "backend/tests/test_api.py", "from backend import app\ndef test_x(): pass\n")
    source(tmp_path, "backend/tests/test_manual.py", "from backend import manual\ndef test_x(): manual.pages()\n")
    source(
        tmp_path,
        "backend/tests/test_api_guide.py",
        "from pathlib import Path\nROOT = Path(__file__).parents[2]\n"
        "def test_x(): (ROOT / 'docs' / 'api.md').read_text()\n",
    )
    source(
        tmp_path,
        "scripts/tests/test_notes.py",
        "from pathlib import Path\ndef test_x(): Path('CHANGELOG.md').read_text()\n",
    )
    source(
        tmp_path,
        "scripts/tests/test_release_workflow.py",
        "from pathlib import Path\nROOT = Path(__file__).parents[2]\n"
        "def test_x(): (ROOT / '.github' / 'workflows' / 'release.yml').read_text()\n",
    )
    for name in ("docs/api.md", "docs/other.md", "CHANGELOG.md", ".github/workflows/release.yml", checks.PR_WORKFLOW):
        source(tmp_path, name, "text\n")

    def picked(*changed):
        return checks.selection(list(changed), root=tmp_path, core=[], added=[])["python"]

    assert picked("docs/other.md") == ["backend/tests/test_manual.py"]
    assert picked("docs/api.md") == ["backend/tests/test_api_guide.py", "backend/tests/test_manual.py"]
    assert picked("CHANGELOG.md") == ["scripts/tests/test_notes.py"]
    assert picked(".github/workflows/release.yml") == ["scripts/tests/test_release_workflow.py"]
    # Code beside a page still reaches whatever imports it.
    assert "backend/tests/test_api.py" in picked("docs/other.md", "backend/manual.py")
    assert picked("docs/removed.md") == ["backend/tests/test_manual.py"]
    # The PR workflow runs what reads it, one short browser script and the Caddy job it alone defines.
    workflow = checks.selection([checks.PR_WORKFLOW], root=tmp_path, core=[], added=[])
    assert workflow["python"] == [] and workflow["browser"] == [checks.SMOKE_SCRIPT] and workflow["caddy"]
    # A file that lists the repository reads every page.
    source(
        tmp_path,
        "scripts/tests/test_tracked.py",
        "import subprocess\ndef test_x(): subprocess.run(['git', 'ls-files'])\n",
    )
    assert "scripts/tests/test_tracked.py" in picked("docs/other.md")
    # On this checkout: a page edit runs the docs and manual tests, not the suite; release tests read the workflows;
    # the PR workflow and the selector run the selector's tests, not the suite.
    page = checks.selection(["docs/api.md"], added=[])["python"]
    assert {"backend/tests/test_docs.py", "backend/tests/test_manual.py"} <= set(page) and len(page) < 60
    assert "backend/tests/test_api.py" not in page
    assert "backend/tests/test_changelog.py" in checks.selection(["CHANGELOG.md"], added=[])["python"]
    assert "scripts/tests/test_release_workflow.py" in checks.selection([".github/workflows/release.yml"])["python"]
    for changed in (checks.PR_WORKFLOW, "scripts/pr_checks.py", "scripts/ci_install.sh"):
        plan = checks.selection([changed], added=[])
        assert "scripts/tests/test_pr_checks.py" in plan["python"] and len(plan["python"]) < 60, changed
    assert checks.selection(["scripts/ci_install.sh"], added=[])["browser"] == [checks.SMOKE_SCRIPT]


def test_scripts_and_files_a_test_runs_or_reads_by_path_select_it(tmp_path):
    # Non-backend surfaces map to the tests that execute or read them: a script run by path, a module run with -m, a
    # shell script naming a Python module, a data file opened through a __file__-located folder, a browser script.
    source(tmp_path, "scripts/build.py", "print(1)\n")
    source(tmp_path, "scripts/tool.sh", "#!/bin/sh\npython -m setup.cli \"$@\"\n")
    source(tmp_path, "setup/__init__.py", "")
    source(tmp_path, "setup/cli.py", "X = 1\n")
    source(tmp_path, "setup/data/plans.json", "{}\n")
    source(
        tmp_path,
        "setup/plans.py",
        "from pathlib import Path\nDATA = Path(__file__).parent / 'data'\n"
        "def load(): return (DATA / 'plans.json').read_text()\n",
    )
    source(
        tmp_path,
        "scripts/tests/test_build.py",
        "import subprocess, sys\ndef test_x(): subprocess.run([sys.executable, 'scripts/build.py'])\n",
    )
    source(
        tmp_path,
        "scripts/tests/test_tool.py",
        "import subprocess\ndef test_x(): subprocess.run(['sh', 'scripts/tool.sh'])\n",
    )
    source(tmp_path, "setup/tests/test_plans.py", "from setup import plans\ndef test_x(): plans.load()\n")
    source(tmp_path, "ui/tests/chat.cjs", "require('child_process').execFileSync('python3', ['scripts/build.py'])\n")
    source(tmp_path, "ui/tests/extra.cjs", "require('child_process').execFileSync('python3', ['scripts/build.py'])\n")
    build = checks.selection(["scripts/build.py"], root=tmp_path, core=["chat"])
    assert build["python"] == ["scripts/tests/test_build.py"] and build["browser"] == ["chat"]
    assert checks.selection(["setup/cli.py"], root=tmp_path, core=[])["python"] == ["scripts/tests/test_tool.py"]
    assert checks.selection(["setup/data/plans.json"], root=tmp_path, core=[])["python"] == [
        "setup/tests/test_plans.py"
    ]
    # Reading a file is not running it: a change to what the data file's reader imports does not reach the readers.
    assert checks.selection(["setup/cli.py"], root=tmp_path, core=[])["browser"] == []


def test_caddy_job_follows_the_image_inputs_and_a_selective_plan_runs_the_image_import_check(tmp_path, monkeypatch):
    source(tmp_path, "backend/__init__.py", "")
    source(tmp_path, "backend/caddy_assets.py", "from . import routes\n")
    source(tmp_path, "backend/routes.py", "")
    source(tmp_path, "backend/other.py", "")
    source(tmp_path, "docker/entrypoint.sh", "#!/bin/sh\n")
    for changed, caddy in (
        (["backend/routes.py"], True),
        (["backend/other.py"], False),
        (["docker/entrypoint.sh"], True),
    ):
        assert checks.selection(changed, root=tmp_path, core=[])["caddy"] is caddy, changed
    source(tmp_path, "Dockerfile", "RUN .venv/bin/python -c 'import backend.other' && true\n")
    calls = []
    monkeypatch.setattr(checks.subprocess, "run", lambda command, **kw: calls.append(command))
    checks.image_checks(["backend/other.py", "README.md"], tmp_path)
    assert calls == [[checks.sys.executable, "-c", "import backend.other"]]
    source(tmp_path, "backend/other.py", "syntax error !\n")
    with pytest.raises(SyntaxError):
        checks.image_checks(["backend/other.py"], tmp_path)
    source(tmp_path, "Dockerfile", "FROM scratch\n")
    with pytest.raises(RuntimeError, match="import check"):
        checks.image_checks([], tmp_path)


def test_a_listing_that_uses_only_names_sees_added_files_and_a_content_listing_sees_edits(tmp_path, mapped):
    source(tmp_path, "skills/a.md", "a\n")
    head = "from pathlib import Path\nSKILLS = Path(__file__).parents[1] / 'skills'\n"
    source(tmp_path, "tests/test_names.py", head + "def test_x(): assert [p.name for p in SKILLS.glob('*.md')]\n")
    source(
        tmp_path, "tests/test_bodies.py", head + "def test_x(): assert [p.read_text() for p in SKILLS.glob('*.md')]\n"
    )
    edited = checks.selection(["skills/a.md"], root=tmp_path, core=[], added=[])
    assert edited["python"] == ["tests/test_bodies.py"]
    added = checks.selection(["skills/a.md"], root=tmp_path, core=[], added=["skills/a.md"])
    assert added["python"] == ["tests/test_bodies.py", "tests/test_names.py"]
    # A read anywhere in the file (module level, another function) may read what was listed: edits reach it.
    source(
        tmp_path,
        "tests/test_module.py",
        head + "FILES = sorted(SKILLS.glob('*.md'))\nTEXT = [p.read_text() for p in FILES]\n",
    )
    source(
        tmp_path,
        "tests/test_split.py",
        head + "def names(): return list(SKILLS.iterdir())\ndef test_x(): assert [p.read_bytes() for p in names()]\n",
    )
    edited = checks.selection(["skills/a.md"], root=tmp_path, core=[], added=[])["python"]
    assert {"tests/test_module.py", "tests/test_split.py"} <= set(edited) and "tests/test_names.py" not in edited
    # On this checkout, edits (CI passes only --diff-filter=A files as added) reach the guards that list and read.
    corpus = checks.selection(["backend/tests/fixtures/golden_corpus/kept/handbook.md"], added=[])["python"]
    assert "backend/tests/test_golden_corpus.py" in corpus
    # A UI file outside the map runs the tests that name it or read it directly, not every reader's importers.
    assert "scripts/tests/test_pr_checks.py" in checks.selection(["ui/app/api.ts"], added=[])["python"]


def test_groups_are_sized_to_the_job_target_and_browser_scripts_split_by_measured_time(tmp_path, monkeypatch, mapped):
    # One VM holds WORKERS x (target - setup) seconds of one-worker test time, and at least WORKERS x MIN_JOB_SECONDS;
    # never 0 VMs, never past the cap.
    per_python = checks.WORKERS * max(checks.JOB_SECONDS - checks.SETUP_SECONDS["python"], checks.MIN_JOB_SECONDS)
    assert checks.job_count(0, "python") == 1
    # One VM past the target by a file's worth is still one VM: 1 s more is 0.25 s more wall time on each of its workers.
    assert checks.job_count(per_python, "python") == 1 and checks.job_count(per_python + 1, "python") == 1
    assert checks.job_count(2 * per_python, "python") == 2
    assert checks.job_count(10**9, "python") == checks.MAX_JOBS
    # The cap only stops a runaway plan: Nov 5's default suite (40,000 one-worker seconds in 936 files) held at 16 VMs
    # ran 11-14 minutes per shard. It now splits into VMs of at most TICO_CI_JOB_SECONDS, setup included.
    suite = Counter({f"backend/tests/test_{i}.py": 1 for i in range(936)})
    split, loads = checks.groups(suite, {name: 40000 / 936 for name in suite})
    # Files are whole, so a VM can run one file past its target (the same 60 s margin the 180 s target had).
    assert len(split) > 16 and max(loads) / checks.WORKERS + checks.SETUP_SECONDS["python"] <= checks.JOB_SECONDS + 60
    # A small selection still takes one VM.
    assert len(checks.groups(Counter({"a.py": 3}), {"a.py": 200})[0]) == 1
    counts = Counter({f"t{i}.py": 1 for i in range(30)})
    split, loads = checks.groups(counts, {name: 100 for name in counts})
    # A remainder under the minimum folds into the VMs before it, so a VM holds under two minimums.
    assert len(split) == checks.job_count(3000, "python") and max(loads) < 2 * checks.WORKERS * checks.MIN_JOB_SECONDS
    assert sorted(sum(split, [])) == sorted(counts)

    names = [f"s{i}" for i in range(40)]
    listed = []
    monkeypatch.setattr(checks, "browser_scripts", lambda args, root=None: listed.append(args) or names)
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_api.py": 1}))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(
        checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["ui/app/core.ts"]
    )
    monkeypatch.setattr(checks, "image_checks", lambda changed, root=None: None)
    timings = tmp_path / "durations.json"
    timings.write_text(json.dumps({f"ui/tests/{n}.cjs": 60 for n in names}))
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", timings)
    assert listed[-1] and "--all" not in listed[-1]
    browser = [j for j in plan["matrix"]["include"] if j["suite"].startswith("browser-")]
    assert len(browser) == checks.job_count(2400, "browser") > 1
    assert sorted(sum([j["files"] for j in browser], [])) == sorted(names)
    # With no measured times, each script still counts, so a first --all run is split too.
    timings.write_text("{}")
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", timings)
    assert len([j for j in plan["matrix"]["include"] if j["suite"].startswith("browser-")]) == checks.job_count(
        40 * checks.UNTIMED_SCRIPT_SECONDS, "browser"
    )

    # A browser job runs exactly its scripts at full width and records their times for the next split.
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(
        checks.subprocess,
        "run",
        lambda command, **kw: calls.append((command, kw["env"])) or SimpleNamespace(returncode=0),
    )
    (tmp_path / "plan.json").write_text(
        json.dumps(
            {"matrix": {"include": [{"suite": "browser-1", "files": ["chat"]}, {"suite": "browser-2", "files": []}]}}
        )
    )
    assert checks.run(tmp_path / "plan.json", "browser-1") == 0
    command, env = calls[-1]
    assert command == ["node", "scripts/ui-tests.cjs", "-j", str(checks.WORKERS), "chat"]
    assert env["TICO_UI_TIMINGS"] == str(tmp_path / "ci-results" / "browser-1.json")
    # The UI helpers import the backend through TICO_PYTHON: the suite's own interpreter, never a bare python3.
    assert env["TICO_PYTHON"] == sys.executable
    with pytest.raises(RuntimeError, match="nonempty"):
        checks.run(tmp_path / "plan.json", "browser-2")
    recorded = tmp_path / "browser-1.json"
    recorded.write_text(json.dumps({"chat": 12.5, "bad": "x"}))
    assert checks.durations([], [recorded]) == {"ui/tests/chat.cjs": 12.5}


def test_browser_listing_matches_the_runner_selection():
    core = checks.browser_scripts([])
    assert {"chat", checks.SMOKE_SCRIPT} <= set(core) and len(core) < len(checks.browser_scripts(["--all"]))
    assert checks.browser_scripts(["chat"]) == ["chat"]


def test_a_pr_merge_ref_is_checked_against_current_main_not_the_older_event_base(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=tmp_path, text=True
        ).strip()

    git("init", "-q", "-b", "main")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n## [Unreleased]\n")
    git("add", "-A")
    git("commit", "-qm", "Start")
    stale = git("rev-parse", "HEAD")
    git("checkout", "-qb", "feature")
    (tmp_path / "app.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "Product change")
    git("checkout", "-q", "main")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n## [Unreleased]\n\n## [1.1.0]\n- Cut.\n")
    git("add", "-A")
    git("commit", "-qm", "Release notes\n\nChangelog-Release: v1.1.0")
    # GitHub checks out the PR merged into current main; the event's base sha is still the older tip.
    git("merge", "-q", "--no-edit", "feature")
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    monkeypatch.setattr(checks, "collect", lambda: Counter({"scripts/tests/test_pr_checks.py": 1}))
    checks.prepare("pull_request", stale, tmp_path / "plan.json")


def receipt_job(name, url, status="completed", conclusion="success"):
    return {
        "name": name,
        "html_url": url,
        "status": status,
        "conclusion": conclusion,
        "started_at": "2026-11-04T17:00:00Z",
        "completed_at": "2026-11-04T17:01:30Z",
    }


def run_receipt(stdin):
    """The receipt action as the timings job runs it: gh's output on stdin, the summary and exit code back."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "pr_checks.py"), "receipt"],
        input=stdin,
        capture_output=True,
        timeout=60,
    )
    return result.returncode, result.stdout.decode()


JOBS = [
    receipt_job("tests (1)", "https://github.com/ticoteam/tico/actions/runs/1/job/11"),
    receipt_job("static", "https://github.com/ticoteam/tico/actions/runs/1/job/12", conclusion="failure"),
]
ERROR_PAGE = {"message": "Server Error", "documentation_url": "https://docs.github.com/rest"}


def test_a_receipt_of_jobs_pages_is_the_table_without_a_note():
    code, out = run_receipt(json.dumps([{"total_count": 2, "jobs": JOBS}]).encode())
    assert code == 0
    assert out == checks.receipt(JOBS) + "\n" and checks.RECEIPT_INCOMPLETE not in out


def test_an_error_page_beside_a_jobs_page_keeps_the_jobs_and_says_the_receipt_is_incomplete():
    code, out = run_receipt(json.dumps([ERROR_PAGE, {"total_count": 2, "jobs": JOBS}]).encode())
    assert code == 0
    assert "| [tests (1)](https://github.com/ticoteam/tico/actions/runs/1/job/11) | success | 90.0 |" in out
    assert "| [static](https://github.com/ticoteam/tico/actions/runs/1/job/12) | failure | 90.0 |" in out
    assert "Server Error" not in out and out.rstrip().endswith(checks.RECEIPT_INCOMPLETE)


@pytest.mark.parametrize(
    "stdin",
    [
        json.dumps([ERROR_PAGE]).encode(),  # an error-only response
        b'[{"total_count": 2, "jobs": [',  # invalid JSON
        b"",  # no output at all
        b"\xff",  # not UTF-8
        json.dumps(ERROR_PAGE).encode(),  # a top level that is not a list
        json.dumps([{"jobs": "not a list"}]).encode(),  # jobs that are not a list
        json.dumps([{"jobs": [{"name": "half"}]}]).encode(),  # a job without its fields
    ],
)
def test_a_receipt_that_cannot_read_its_pages_exits_zero_and_says_so(stdin):
    code, out = run_receipt(stdin)
    assert code == 0
    assert out.startswith("| Job | Result | Seconds |") and out.rstrip().endswith(checks.RECEIPT_INCOMPLETE)


RUNSON_WORKFLOWS = ("ci.yml", "remote-run.yml")


def test_the_main_cache_warm_up_saves_the_keys_pr_checks_restore():
    """Without main pushes, pr-cache.yml keeps main's caches fresh. Each cache it warms is a PR-check cache with the
    same action, key, path or shared key, on the same GitHub-hosted runner image (a key holds the image and arch), and every
    cache it fills itself is looked up first, so a warm day downloads nothing."""
    import yaml

    pr = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-checks.yml").read_text())
    warm = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-cache.yml").read_text())

    def caches(doc):
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                uses, args = step.get("uses", ""), dict(step.get("with") or {})
                # A restore-only step (actions/cache/restore, same pinned commit) restores what actions/cache saves.
                uses = uses.replace("actions/cache/restore@", "actions/cache@")
                if "actions/cache@" in uses or "rust-cache@" in uses or args.get("cache"):
                    lookup = args.pop("lookup-only", None)
                    yield args.get("key") or args.get("shared-key") or args["cache"], uses, args, job["runs-on"], lookup

    restored = list(caches(pr))
    warmed = list(caches(warm))
    venv = (
        "venv-ubuntu24-${{ runner.arch }}-py${{ steps.python.outputs.python-version }}-"
        "${{ hashFiles('backend/requirements-dev.txt', 'scripts/ci_install.sh') }}"
    )
    assert (
        {w[0] for w in warmed}
        == {r[0] for r in restored}
        == {
            "pip",
            "npm",
            venv,
            "pr-rust-lint",
            "pr-desktop",
            "pr-conformance",
            "pr-recorder",
            "playwright-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('package-lock.json') }}",
            "playwright-system-ubuntu24-x64-${{ steps.image.outputs.version }}-${{ hashFiles('package-lock.json', "
            "'scripts/ci_install.sh', 'scripts/playwright-system-packages.json') }}",
        }
    )
    # The venv key names the exact Python its job set up: each job restoring or saving it sets Python up first, as
    # `python`, and only PR checks' restore-only action reads it there (a pull request or queue group saves nothing).
    # Every reader, not just one, has the warmer's exact path and key, or it would never hit.
    venv_with = {"path": "${{ runner.temp }}/venv", "key": venv}
    for doc, action, expected in (
        (pr, "actions/cache/restore@", venv_with),
        (warm, "actions/cache@", {**venv_with, "lookup-only": True}),
    ):
        for name, job in doc["jobs"].items():
            steps = job.get("steps", [])
            at = [i for i, step in enumerate(steps) if "venv" in str((step.get("with") or {}).get("key", ""))]
            if not at:
                continue
            assert len(at) == 1 and action in steps[at[0]]["uses"], name
            assert steps[at[0]]["with"] == expected, name
            setup = [i for i, step in enumerate(steps) if "actions/setup-python@" in step.get("uses", "")]
            assert len(setup) == 1 and steps[setup[0]].get("id") == "python" and setup[0] < at[0], name
            install = [i for i, step in enumerate(steps) if "scripts/ci_install.sh" in str(step.get("run", ""))]
            assert install and at[0] < install[0], name
    assert sum(venv in str(job) for job in pr["jobs"].values()) == 5  # plan, tests, factory-sim, static, caddy-linux
    for ident, uses, args, runner, lookup in warmed:
        assert runner in ("ubuntu-24.04", "macos-latest"), ident
        assert any((uses, args) == (r[1], r[2]) and runner == r[3] for r in restored), ident
        assert lookup is True or ident in ("pip", "npm"), ident  # setup-python/node have no lookup-only
    assert "schedule" in warm[True] and warm[True]["push"]["branches"] == ["main"]
    assert set(pr[True]) == {"pull_request", "merge_group"}
    assert "converted_to_draft" not in pr[True]["pull_request"]["types"]


def test_the_receipt_step_is_cosmetic_and_nothing_waits_on_the_timings_job():
    job = workflow_jobs()["jobs"]["timings"]
    steps = {step.get("name"): step for step in job["steps"]}
    receipt_step = steps["Per-job timings linked from the PR check"]
    assert receipt_step["continue-on-error"] is True
    assert receipt_step["timeout-minutes"] * 2 <= job["timeout-minutes"]
    assert "timings" not in workflow_jobs()["jobs"]["checks"]["needs"]


def workflow_jobs():
    import yaml

    return yaml.safe_load((ROOT / ".github" / "workflows" / "pr-checks.yml").read_text())


def test_a_merge_group_diffs_its_queue_base_to_its_queue_head_not_the_checkout(tmp_path):
    """GitHub merges a queue group into its base, and that merge commit is the change under test: the diff is base to
    the group's head, whatever the checkout (HEAD) happens to be."""

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=tmp_path, text=True
        ).strip()

    git("init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "Start")
    base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "feature")
    (tmp_path / "app.py").write_text("x = 2\n")
    (tmp_path / "new.py").write_text("y = 1\n")
    git("add", "-A")
    git("commit", "-qm", "Change")
    git("checkout", "-q", "main")
    git("merge", "-q", "--no-ff", "--no-edit", "feature")
    queue_head = git("rev-parse", "HEAD")
    git("checkout", "-q", base)  # the checkout is the old base; the group's head is the merge commit
    assert sorted(checks.changed_files(base, queue_head, root=tmp_path)) == ["app.py", "new.py"]
    assert checks.changed_files(base, queue_head, root=tmp_path, only="A") == ["new.py"]
    assert checks.changed_files(base, root=tmp_path) == []


def test_a_queue_group_diffs_the_same_files_its_pull_request_run_did_plus_the_changes_queued_ahead(tmp_path, mapped):
    """PR checks == queue checks. A pull request run diffs its merge commit from main as it was then (merge_parent, even
    when the event's base sha is stale); its queue group diffs the group's head from the group's base. For the same
    change the two lists match, and a group behind another holds the union, so the queue never selects less."""

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=tmp_path, text=True
        ).strip()

    def commit(name, text, message):
        (tmp_path / name).write_text(text)
        git("add", "-A")
        git("commit", "-qm", message)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    stale = commit("app.py", "x = 1\n", "Start")
    git("checkout", "-qb", "one")
    commit("one.py", "a = 1\n", "One")
    git("checkout", "-qb", "two", "main")
    commit("two.py", "b = 1\n", "Two")
    git("checkout", "-q", "main")
    base = commit("other.py", "c = 1\n", "Main moves on")
    # GitHub's refs/pull/N/merge: the branch merged into main as main is when the run starts.
    git("checkout", "-q", "--detach", base)
    git("merge", "-q", "--no-ff", "--no-edit", "two")
    pr_merge = git("rev-parse", "HEAD")
    pr_base = checks.merge_parent(stale, pr_merge, root=tmp_path)
    assert pr_base == base
    pr_files = checks.changed_files(pr_base, pr_merge, root=tmp_path)
    # Alone in its group: the group's head is the same merge onto the same base.
    git("checkout", "-q", "--detach", base)
    git("merge", "-q", "--no-ff", "--no-edit", "two")
    alone = git("rev-parse", "HEAD")
    assert checks.changed_files(base, alone, root=tmp_path) == pr_files == ["two.py"]
    # Behind `one` in a group: its head carries both changes, so it selects at least what its pull request run did.
    git("checkout", "-q", "--detach", base)
    git("merge", "-q", "--no-ff", "--no-edit", "one")
    git("merge", "-q", "--no-ff", "--no-edit", "two")
    behind = git("rev-parse", "HEAD")
    assert checks.changed_files(base, behind, root=tmp_path) == ["one.py", "two.py"]
    for files in (["backend/task_loops.py"], ["conftest.py"], ["ui/app/chat.ts"], [checks.PR_WORKFLOW]):
        pr, queue = checks.selection(files, "pull_request"), checks.selection(files, "merge_group")
        assert pr == queue, files


def test_a_merge_group_plans_its_own_selection_and_never_runs_the_pull_request_gates(tmp_path, monkeypatch, mapped):
    files = {"backend/tests/test_task_loops.py": 2, "backend/tests/test_auth.py": 10}
    monkeypatch.setattr(checks, "collect", lambda: Counter(files))

    def refuse(*args, **kwargs):
        raise AssertionError("a merge queue group is checked from its own base, not from a pull request's merge parent")

    monkeypatch.setattr(checks, "merge_parent", refuse)
    monkeypatch.setattr(checks, "guard_changelog", refuse)
    monkeypatch.setattr(checks, "image_checks", lambda changed: None)
    seen = []

    def fake_changed(base, head="HEAD", only=None):
        seen.append((base, head, only))
        return [] if only else ["backend/task_loops.py"]

    monkeypatch.setattr(checks, "changed_files", fake_changed)
    target = tmp_path / "plan.json"
    plan = checks.prepare("merge_group", "queue-base", target, head="queue-head")
    assert seen == [("queue-base", "queue-head", None), ("queue-base", "queue-head", "A")]
    assert plan["reason"].startswith("measured dependency map") and plan["python"] is not None
    expected = sorted(set(checks.selection(["backend/task_loops.py"])["python"]) & set(files))
    assert plan["python"] == expected
    # An empty group diff falls back to the full suite, as a pull request's does.
    monkeypatch.setattr(checks, "changed_files", lambda base, head="HEAD", only=None: [])
    assert checks.prepare("merge_group", "queue-base", target, head="queue-head")["python"] == [
        "backend/tests/test_auth.py"
    ]


def test_every_job_runs_on_a_github_hosted_runner_and_only_a_ready_pull_request_runs_its_tests():
    doc = workflow_jobs()
    assert doc[True]["merge_group"] == {"types": ["checks_requested"]}
    assert doc[True]["pull_request"]["types"] == ["opened", "synchronize", "reopened", "ready_for_review"]
    assert doc["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.ref" in doc["concurrency"]["group"]
    jobs = doc["jobs"]
    # PR checks and the merge queue take no RunsOn label and no repository variable: every job is GitHub-hosted.
    expected = {name: "ubuntu-24.04" for name in jobs}
    expected |= {
        "recorder": "macos-latest",
        "caddy-linux": "${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-24.04' }}",
    }
    assert {name: job["runs-on"] for name, job in jobs.items()} == expected
    # PR checks == queue checks: every job the gate needs runs on a ready pull request from this repository as well as
    # in a queue group, under the same condition, so a failure shows on the pull request before it is enqueued.
    ready = (
        "github.event_name == 'merge_group' || (github.event_name == 'pull_request' && "
        "!github.event.pull_request.draft && github.event.pull_request.head.repo.full_name == github.repository)"
    )
    for name in ("tests", "factory-sim", "rust-lint", "desktop-app", "recorder", "caddy-linux"):
        assert ready in jobs[name]["if"], name
    for name, output in (
        ("factory-sim", "factory"),
        ("rust-lint", "rust_lint"),
        ("desktop-app", "desktop"),
        ("recorder", "recorder"),
        ("caddy-linux", "caddy"),
    ):
        assert f"needs.plan.outputs.{output} == 'true'" in jobs[name]["if"], name
    # Only the cosmetic timing receipt runs in the merge queue alone; nothing waits on it.
    assert "github.event_name == 'merge_group'" in jobs["timings"]["if"]
    assert "github.event_name == 'pull_request'" not in jobs["timings"]["if"]
    queue_only = {name for name, job in jobs.items() if "pull_request" not in str(job.get("if", "pull_request"))}
    assert queue_only == {"timings"}
    assert set(jobs) == {
        "plan",
        "static",
        "tests",
        "factory-sim",
        "checks",
        "rust-lint",
        "desktop-app",
        "recorder",
        "caddy-linux",
        "timings",
    }


def test_a_draft_or_a_fork_runs_no_test_or_sim_leg():
    jobs = workflow_jobs()["jobs"]
    assert jobs["plan"]["if"] == "${{ !github.event.pull_request.draft }}"
    for name in ("tests", "factory-sim"):
        assert jobs[name]["needs"] == "plan", name  # a draft has no plan, so the leg is skipped with it
        # A fork's pull request is not this repository's head, so it runs no leg here; a maintainer queues it instead.
        assert "!github.event.pull_request.draft" in jobs[name]["if"], name
        assert "github.event.pull_request.head.repo.full_name == github.repository" in jobs[name]["if"], name


def test_a_failed_suite_or_sim_leg_cancels_its_run_and_checks_still_reports():
    jobs = workflow_jobs()["jobs"]
    for name in ("tests", "factory-sim"):
        job = jobs[name]
        assert job["permissions"] == {"contents": "read", "actions": "write"}, name
        cancel = job["steps"][-1]
        assert cancel["if"] == "failure()" and cancel["run"] == 'gh run cancel "$GITHUB_RUN_ID"', name
        assert cancel["env"] == {"GH_TOKEN": "${{ github.token }}", "GH_REPO": "${{ github.repository }}"}, name
    # Only the two heavy legs and the plan (which cancels superseded queue groups) may cancel; every other job keeps the
    # workflow's read-only token.
    assert {name for name, job in jobs.items() if "permissions" in job} == {"tests", "factory-sim", "plan"}
    # The required gate still runs after a cancel, and a cancelled timing receipt never waits for a runner.
    assert "always()" in jobs["checks"]["if"] and "tests" in jobs["checks"]["needs"]
    assert "!cancelled()" in jobs["timings"]["if"]


def test_pr_checks_and_the_cache_warm_up_take_no_runson_label_and_always_free_the_disk():
    """PR checks and the cache warm-up run on GitHub-hosted runners only: no RunsOn label, no S3 cache extra, no runner
    size, no repository variable. Their disk-freeing step always runs (hosted runners start with about 14 GB free)."""
    import yaml

    for name in ("pr-checks.yml", "pr-cache.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text()
        assert not re.search(r"runs-on=|runner=\d|s3-cache|TICO_CI_HOSTED", text), name
        for job in yaml.safe_load(text)["jobs"].values():
            for step in job.get("steps", []):
                if "/opt/ghc" in str(step.get("run", "")):
                    assert "if" not in step, name


def test_every_runs_on_label_sits_behind_the_hosted_switch_and_stays_the_default():
    """vars.TICO_CI_HOSTED == 'true' moves a RunsOn job to a GitHub-hosted runner. Unset, each RunsOn label is the
    default, and only a hosted job frees the preinstalled SDKs. The macOS recorder job stays on macos-latest."""
    import yaml

    for name in RUNSON_WORKFLOWS:
        jobs = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())["jobs"]
        for job_name, job in jobs.items():
            runner = str(job.get("runs-on", ""))
            if "runs-on=" not in runner:
                continue
            # The arm64 jobs nest their arch choice inside the switch: "&& (matrix.arch == 'arm64' && ... || ...)".
            # A release validation's incremental mode (anchor set) is hosted too, so the switch is a hosted-or-mode test.
            hosted = ("${{ vars.TICO_CI_HOSTED == 'true' && ", "${{ (vars.TICO_CI_HOSTED == 'true' || ")
            assert runner.startswith(hosted) and "'ubuntu-24.04'" in runner, (name, job_name)
            assert "format('runs-on={0}/" in runner, (name, job_name)  # the RunsOn label is the fallback, kept verbatim
            if "arm64" in runner:
                assert "'ubuntu-24.04-arm'" in runner, (name, job_name)
            for step in job.get("steps", []):
                if "/opt/ghc" in str(step.get("run", "")):
                    assert step.get("if", "").startswith("vars.TICO_CI_HOSTED == 'true'"), (name, job_name)
    recorder = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-checks.yml").read_text())["jobs"]["recorder"]
    assert recorder["runs-on"] == "macos-latest"


def test_checks_is_the_one_gate_and_fails_on_a_failed_or_cancelled_need_while_skipped_passes():
    gate = workflow_jobs()["jobs"]["checks"]
    assert gate["name"] == "checks" and "always()" in gate["if"]
    needs = set(gate["needs"])
    assert needs == set(workflow_jobs()["jobs"]) - {"checks", "timings"}
    env = gate["steps"][0]["env"]
    names = {key: re.search(r"needs\.([\w-]+)\.result", value).group(1) for key, value in env.items()}
    assert set(names.values()) == needs

    def exit_code(results):
        run_env = {**os.environ, **{key: results[name] for key, name in names.items()}}
        return subprocess.run(["bash", "-c", gate["steps"][0]["run"]], env=run_env, capture_output=True).returncode

    passing = dict.fromkeys(needs, "success")
    assert exit_code(passing) == 0
    assert exit_code({**passing, "tests": "skipped", "recorder": "skipped", "caddy-linux": "skipped"}) == 0
    assert exit_code({**passing, "tests": "failure"}) == 1
    assert exit_code({**passing, "caddy-linux": "cancelled"}) == 1
    assert exit_code({**passing, "factory-sim": "failure"}) == 1
    assert exit_code({**passing, "factory-sim": "skipped"}) == 0
    assert exit_code({**passing, "plan": "skipped"}) == 0


def measured(code=(), data=(), folders=()):
    return {"code": list(code), "data": list(data), "folders": list(folders)}


def test_a_change_the_recorded_sim_run_reads_selects_the_factory_sim():
    recorded = measured(code=["backend/wake_rules.py"], data=["backend/data/rules.json"], folders=["backend/workers/"])
    assert "runs or reads" in checks.factory_reason(["backend/wake_rules.py"], call_path=recorded)
    assert "runs or reads" in checks.factory_reason(["backend/data/rules.json"], call_path=recorded)
    assert "lists backend/workers/" in checks.factory_reason(["backend/workers/new_job.py"], call_path=recorded)
    assert "catalog template" in checks.factory_reason(["templates/catalog/new-role/AGENT.md"], call_path=recorded)


def test_changes_the_sim_never_reads_do_not_select_it():
    # A listing sees only the entries directly in its folder: a change below one of them moves nothing it lists.
    recorded = measured(code=["backend/wake_rules.py"], folders=["backend/workers/"])
    for name in [
        "ui/app/chat.ts",
        "docs/releasing.md",
        "backend/tests/test_auth.py",
        "scripts/remote.py",
        "backend/workers/sub/job.py",
    ]:
        assert checks.factory_reason([name], call_path=recorded) is None, name


def test_the_sim_pins_its_own_code_its_workflow_job_and_the_pinned_backend_requirements():
    recorded = measured()
    assert "sim's own code" in checks.factory_reason(["evals/factory/sim.py"], call_path=recorded)
    assert "defines the sim's job" in checks.factory_reason([checks.PR_WORKFLOW], call_path=recorded)
    assert "pins" in checks.factory_reason(["backend/requirements-dev.txt"], call_path=recorded)
    assert "pins" in checks.factory_reason(["backend/requirements.txt"], call_path=recorded)


def test_an_empty_or_unmeasured_change_runs_the_sim(tmp_path):
    assert "empty" in checks.factory_reason([], call_path=measured())
    assert "missing" in checks.factory_reason(["docs/releasing.md"], root=tmp_path)


def test_a_factory_plan_packs_the_sim_for_a_queue_group_or_a_pull_request_only_when_it_runs(monkeypatch):
    monkeypatch.setattr(checks, "factory_call_path", lambda root=checks.ROOT: measured(code=["backend/wake_rules.py"]))
    shards = checks.factory_shards()
    # One VM per group: a ready pull request's sim is packed as the queue's is, never run as one 1/1 VM.
    for event in ("merge_group", "pull_request"):
        plan = checks.factory_plan(event, ["backend/wake_rules.py"])
        assert plan["run"] is True and plan["shards"] == shards, event
    unrelated = checks.factory_plan("merge_group", ["ui/app/chat.ts"])
    assert unrelated["run"] is False and unrelated["shards"] == ["1/1"]
    assert checks.factory_plan("pull_request", ["ui/app/chat.ts"])["shards"] == ["1/1"]


def test_the_factory_shards_cover_every_scenario_once_in_about_five_minutes_a_vm():
    from evals.factory import run as factory
    from evals.factory.scenarios import SCENARIOS

    timings = factory.timings()
    assert set(timings) == {s.name for s in SCENARIOS}, "timings.json needs a time for every scenario"
    shards = checks.factory_shards()
    count = len(shards)
    assert shards == [f"{index}/{count}" for index in range(1, count + 1)]
    pairs = [(name, 1) for name in sorted(timings)]
    groups = [factory.pack(pairs, count, timings)[index] for index in range(count)]
    assert sorted(pair for group in groups for pair in group) == sorted(pairs)
    slowest_vm = max(sum(timings[name] for name, _ in group) for group in groups) / factory.REMOTE_CPUS
    assert slowest_vm <= 300, slowest_vm


def test_the_factory_sim_runs_in_the_queue_and_on_a_ready_pull_request_on_the_plans_shards():
    jobs = workflow_jobs()["jobs"]
    assert {"factory", "factory_shards"} <= set(jobs["plan"]["outputs"])
    job = jobs["factory-sim"]
    assert job["needs"] == "plan"
    assert "needs.plan.outputs.factory == 'true'" in job["if"]
    assert "github.event_name == 'merge_group'" in job["if"] and "github.event_name == 'pull_request'" in job["if"]
    assert job["strategy"]["matrix"]["shard"] == "${{ fromJSON(needs.plan.outputs.factory_shards) }}"
    assert job["runs-on"] == "ubuntu-24.04"
    [sim] = [step["run"] for step in job["steps"] if "evals.factory.run" in step.get("run", "")]
    assert '--seeds 1 --shard "$SHARD" -j 4' in sim


def test_the_recorded_call_path_matches_the_repository_and_decides_what_the_queue_runs():
    recorded = checks.factory_call_path()
    # The two data entries that are not files: the sim opens VERSION, which this repository does not have, and a
    # directory of registry fixtures. Neither is a file a change can add without also changing what the sim reads.
    not_files = {"VERSION", "backend/tests/fixtures/registry"}
    assert all((checks.ROOT / name).is_file() for name in recorded["code"])
    assert all((checks.ROOT / name).is_file() or name in not_files for name in recorded["data"])
    assert all((checks.ROOT / name).is_dir() for name in recorded["folders"])
    assert not [name for name in recorded["code"] if name.startswith("ui/")]
    assert "ui/index.html" in recorded["data"] and "backend/wake_rules.py" in recorded["code"]
    assert "templates/catalog/" in recorded["folders"]
    assert checks.factory_reason(["backend/wake_rules.py"]) is not None
    assert checks.factory_reason(["ui/app/chat.ts"]) is None
    assert checks.factory_reason(["docs/releasing.md"]) is None
    assert checks.factory_reason(["scripts/tests/test_pr_checks.py"]) is None


def test_a_deleted_file_runs_the_tests_that_name_or_read_its_old_path(tmp_path, mapped):
    smoke_tree(tmp_path)
    source(tmp_path, "backend/__init__.py", "")
    source(tmp_path, "backend/tests/test_missing_user.py", "from backend.missing import X\ndef test_x(): pass\n")
    source(
        tmp_path,
        "backend/tests/test_reads_path.py",
        "from pathlib import Path\ndef test_x(): Path('backend/missing.py').read_text()\n",
    )
    source(tmp_path, "backend/tests/test_unrelated.py", "def test_x(): pass\n")
    plan = checks.selection(["backend/missing.py"], root=tmp_path, core=[])
    assert plan["python"] == ["backend/tests/test_missing_user.py", "backend/tests/test_reads_path.py"]
    assert plan["browser"] == []
    # Code that still names the old path would fail on import: its checks run with the smoke set.
    source(tmp_path, "backend/other.py", "from backend.missing import X\n")
    plan = checks.selection(["backend/missing.py"], root=tmp_path, core=[])
    assert "backend/tests/test_missing_user.py" in plan["python"]
    assert "backend/tests/test_unrelated.py" not in plan["python"]
    assert set(checks.smoke(tmp_path)[0]) <= set(plan["python"])


def test_a_renamed_away_file_names_its_old_path_and_a_pure_ui_rename_lists_only_the_new_name(tmp_path):
    assert checks.name_status("R100\0ui/app/a.js\0ui/app/a.ts\0") == [("R", "ui/app/a.ts", "ui/app/a.js", 100)]

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=tmp_path, text=True
        ).strip()

    git("init", "-q", "-b", "main")
    same = "export const value = 1;\n" * 40
    source(tmp_path, "ui/app/a.js", same)
    source(tmp_path, "ui/app/b.js", same)
    git("add", "-A")
    git("commit", "-qm", "Start")
    base = git("rev-parse", "HEAD")
    git("mv", "ui/app/a.js", "ui/app/a.ts")
    git("mv", "ui/app/b.js", "ui/app/c.js")
    source(tmp_path, "ui/app/c.js", same + "// edited\n")
    git("add", "-A")
    git("commit", "-qm", "Rename")
    head = git("rev-parse", "HEAD")
    # a.js to a.ts with the same bytes lists only the new name; a rename that also edits keeps the old name.
    assert checks.changed_files(base, head, root=tmp_path) == ["ui/app/a.ts", "ui/app/b.js", "ui/app/c.js"]
    assert checks.changed_files(base, head, root=tmp_path, only="A") == ["ui/app/a.ts", "ui/app/c.js"]


def test_a_pinned_file_runs_the_tests_that_read_it_and_the_smoke_set(tmp_path, mapped):
    smoke_tree(tmp_path)
    source(tmp_path, "requirements.txt", "flask==3.0.0\n")
    source(
        tmp_path,
        "backend/tests/test_deps.py",
        "from pathlib import Path\ndef test_x(): Path('requirements.txt').read_text()\n",
    )
    source(tmp_path, "backend/tests/test_unrelated.py", "def test_x(): pass\n")
    smoke_tests, smoke_scripts = checks.smoke(tmp_path)
    plan = checks.selection(["requirements.txt"], root=tmp_path, core=[])
    assert "backend/tests/test_deps.py" in plan["python"] and "backend/tests/test_unrelated.py" not in plan["python"]
    assert set(smoke_tests) <= set(plan["python"]) and set(smoke_scripts) <= set(plan["browser"])
    assert checks.pinned("requirements-dev.txt") and checks.pinned("pytest.ini")
    assert not checks.pinned("docs/requirements.md")


def test_a_conftest_runs_the_tests_below_it_when_few_and_the_smoke_set_when_many(tmp_path, mapped):
    smoke_tree(tmp_path)
    smoke_tests = checks.smoke(tmp_path)[0]
    source(tmp_path, "scripts/tests/conftest.py", "import pytest\n")
    source(tmp_path, "scripts/tests/test_small.py", "def test_x(): pass\n")
    small = checks.selection(["scripts/tests/conftest.py"], root=tmp_path, core=[])
    assert "scripts/tests/test_small.py" in small["python"] and not set(smoke_tests) & set(small["python"])
    for index in range(checks.CONFTEST_BELOW_LIMIT + 1):
        source(tmp_path, f"scripts/tests/test_many_{index}.py", "def test_x(): pass\n")
    big = checks.selection(["scripts/tests/conftest.py"], root=tmp_path, core=[])
    assert set(smoke_tests) <= set(big["python"])
    assert "scripts/tests/test_small.py" not in big["python"]


def test_an_unparsed_file_runs_the_checks_that_name_it_else_the_smoke_set(tmp_path, mapped):
    smoke_tree(tmp_path)
    smoke_tests, smoke_scripts = checks.smoke(tmp_path)
    source(tmp_path, "backend/broken.py", "syntax error !\n")
    source(tmp_path, "backend/tests/test_names_broken.py", "PATH = 'backend/broken.py'\ndef test_x(): pass\n")
    source(tmp_path, "backend/tests/test_other.py", "def test_x(): pass\n")
    named = checks.selection(["backend/broken.py"], root=tmp_path, core=[])
    assert named["python"] == ["backend/tests/test_names_broken.py"] and named["browser"] == []
    source(tmp_path, "backend/broken2.py", "syntax error !\n")
    unnamed = checks.selection(["backend/broken2.py"], root=tmp_path, core=[])
    assert set(smoke_tests) <= set(unnamed["python"]) and set(smoke_scripts) <= set(unnamed["browser"])


def test_a_ui_file_outside_the_map_runs_its_readers_its_linked_scripts_and_the_smoke_browser(tmp_path, mapped):
    smoke_tree(tmp_path)
    smoke_scripts = checks.smoke(tmp_path)[1]
    source(tmp_path, "ui/app/extra.js", "export {};\n")
    source(
        tmp_path,
        "scripts/tests/test_extra_reader.py",
        "from pathlib import Path\ndef test_x(): Path('ui/app/extra.js').read_text()\n",
    )
    source(tmp_path, "ui/tests/linked.cjs", "const page = 'ui/app/extra.js';\n")
    plan = checks.selection(["ui/app/extra.js"], root=tmp_path, core=[])
    assert "scripts/tests/test_extra_reader.py" in plan["python"]
    assert "linked" in plan["browser"] and set(smoke_scripts) <= set(plan["browser"])
    assert "--all" not in plan["browser"]


def test_index_html_script_urls_map_to_the_files_the_server_serves():
    # The page names scripts by URL (backend/ui_bundle.py): /tico/ui/x is ui/x, /vendor/x is ui/vendor/x.
    diff = (
        '+<script src="/tico/ui/app/b.ts"></script>\n'
        '+<script src="/vendor/marked.min.js"></script>\n'
        '+<script src="https://cdn.example/x.js"></script>\n'
        "+++ ui/index.html\n"
    )
    assert checks.script_srcs("ui/index.html", diff) == ["ui/app/b.ts", "ui/vendor/marked.min.js"]


def test_plan_refuses_to_run_outside_ci(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("TICO_PLAN_LOCAL", raising=False)
    monkeypatch.setattr(checks, "prepare", lambda *a: pytest.fail("plan collected tests outside CI"))
    monkeypatch.setattr(sys, "argv", ["pr_checks.py", "plan", "--event", "pull_request"])
    assert checks.main() == 2
    assert "runs in CI only" in capsys.readouterr().err


def test_an_index_html_script_rename_counts_as_a_change_to_the_file_it_loads(tmp_path):
    diff = '+<script src="app/b.js"></script>\n-<script src="app/a.js"></script>\n+++ ui/index.html\n'
    assert checks.script_srcs("ui/index.html", diff) == ["ui/app/b.js"]

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=tmp_path, text=True
        ).strip()

    git("init", "-q", "-b", "main")
    source(tmp_path, "ui/index.html", '<script src="app/a.js"></script>\n')
    source(tmp_path, "ui/app/a.js", "a\n")
    source(tmp_path, "ui/app/b.js", "b\n")
    git("add", "-A")
    git("commit", "-qm", "Start")
    base = git("rev-parse", "HEAD")
    source(tmp_path, "ui/index.html", '<script src="app/b.js"></script>\n')
    git("add", "-A")
    git("commit", "-qm", "Load b")
    head = git("rev-parse", "HEAD")
    assert checks.script_changes(base, head, ["ui/index.html"], root=tmp_path) == ["ui/app/b.js"]
    assert checks.script_changes(base, head, ["ui/app/b.js"], root=tmp_path) == []


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
def test_pull_requests_and_queue_groups_never_run_the_full_suite_or_every_browser_script(event, mapped):
    changes = (
        ["backend/task_loops.py"],
        ["conftest.py"],
        ["requirements.txt"],
        ["pytest.ini"],
        ["package-lock.json"],
        ["ui/app/core.ts"],
        ["ui/app/chat.ts"],
        ["backend/missing.py"],
        ["docs/missing.md"],
        ["backend/tests/conftest.py"],
        ["Dockerfile"],
        [checks.PR_WORKFLOW],
        [],
    )
    for changed in changes:
        plan = checks.selection(changed, event)
        assert plan["python"] is not None and plan["browser"] is not None, changed
        assert "--all" not in plan["browser"], changed
        assert plan["reason"] != "main runs the full default suite", changed


def test_the_smoke_set_is_checked_and_a_missing_entry_fails_the_check(tmp_path):
    assert checks.smoke_check(ROOT) == []
    source(
        tmp_path,
        checks.SMOKE_FILE,
        json.dumps(
            {
                "python": [{"file": "backend/tests/test_gone.py", "seconds": 1, "reason": "gone"}],
                "browser": [{"script": "gone", "seconds": 1, "reason": "gone"}],
            }
        ),
    )
    problems = checks.smoke_check(tmp_path)
    assert any("test_gone.py" in problem for problem in problems)
    assert any("browser gone" in problem for problem in problems)
    source(tmp_path, checks.SMOKE_FILE, "[]")
    assert checks.smoke_check(tmp_path)


def test_a_file_that_reaches_tests_only_through_a_conftest_selects_its_direct_readers_and_the_smoke_set(
    tmp_path, mapped
):
    # The root conftest loads a slot script by path, the slot script reads remote.py, and remote's own test imports it.
    smoke_tree(tmp_path)
    smoke_tests, smoke_scripts = checks.smoke(tmp_path)
    source(tmp_path, "conftest.py", "SLOT = 'scripts/heavy_slot.py'\n")
    source(tmp_path, "scripts/heavy_slot.py", "REMOTE = 'scripts/remote.py'\n")
    source(tmp_path, "scripts/remote.py", "X = 1\n")
    source(tmp_path, "scripts/tests/test_remote.py", "from scripts import remote\ndef test_x(): pass\n")
    source(tmp_path, "backend/tests/test_unrelated.py", "def test_x(): pass\n")
    plan = checks.selection(["scripts/remote.py"], root=tmp_path, core=[])
    assert "scripts/tests/test_remote.py" in plan["python"]
    assert "backend/tests/test_unrelated.py" not in plan["python"]
    assert set(smoke_tests) <= set(plan["python"]) and set(smoke_scripts) <= set(plan["browser"])
    assert any("conftest.py is reached" in note for note in plan["uncertain"])


def test_a_changed_browser_script_runs_itself_when_the_map_does_not_list_it(tmp_path):
    # The queue group of #2844 changed ui/tests/threads.cjs and threads-real-hub.cjs and ran neither: a ui/ file outside
    # UI_MAP seeded nothing, and a browser script names no other script.
    smoke_tree(tmp_path)
    source(tmp_path, "ui/tests/notes.cjs", "// a browser script\n")
    plan = checks.selection(["ui/tests/notes.cjs"], root=tmp_path, core=[])
    assert "notes" in plan["browser"]
    assert "--all" not in plan["browser"]


def icon_font_tree(root):
    """#2893's shape: scripts/build-icon-font.py globs ui/app/*.js and backend/tests/test_icon_font.py runs it."""
    source(
        root,
        "scripts/build-icon-font.py",
        "from pathlib import Path\n"
        'ROOT = Path(__file__).resolve().parent.parent\nUI = ROOT / "ui"\n'
        'def used_icons():\n    return [p.read_text() for p in UI.glob("app/*.js")]\n',
    )
    source(
        root,
        "backend/tests/test_icon_font.py",
        "import importlib.util\nfrom pathlib import Path\n"
        'ROOT = Path(__file__).resolve().parents[2]\n'
        '_spec = importlib.util.spec_from_file_location("build_icon_font", ROOT / "scripts" / "build-icon-font.py")\n'
        "build = importlib.util.module_from_spec(_spec)\n_spec.loader.exec_module(build)\n"
        "def test_every_icon_in_use_is_in_the_font_manifest():\n    assert build.used_icons() is not None\n",
    )


def test_a_ui_change_selects_test_icon_font_through_the_module_that_globs_the_ui_folder(tmp_path):
    # #2893 changed ui/app/ files. backend/tests/test_icon_font.py runs scripts/build-icon-font.py, which reads each
    # ui/app/*.js: the test is reached through that module, not through a direct read of the file.
    smoke_tree(tmp_path)
    icon_font_tree(tmp_path)
    source(tmp_path, "ui/app/new.js", "export const icon = 'filter_list';\n")
    source(tmp_path, "ui/styles/new.css", ".x { color: red; }\n")
    plan = checks.selection(["ui/app/new.js"], root=tmp_path, core=[])
    assert "backend/tests/test_icon_font.py" in plan["python"]
    unrelated = checks.selection(["ui/styles/new.css"], root=tmp_path, core=[])
    assert "backend/tests/test_icon_font.py" not in unrelated["python"]


def test_a_verdict_moves_change_runs_the_factory_sim_that_the_queue_did_not_run_for_2841():
    # #2841 changed backend/verdict_moves.py and merged through the queue, which had no factory rule then: the sim went
    # red on main at 21:26Z (verdict_past_open_sibling). The recorded call path reaches the file, so the sim runs.
    assert checks.factory_reason(["backend/verdict_moves.py"]) is not None


def test_a_template_edit_runs_the_hub_command_test_in_the_contract_job_that_2866_broke(mapped):
    # #2866 added librarian commands with no recorded refusal. test_e2e_cli walks templates/ and skills/ and runs in the
    # contract job, which any change under those folders selects.
    assert "backend/tests/test_e2e_cli.py" in checks.CONTRACT_TESTS
    plan = checks.selection(["templates/catalog/librarian/AGENT.md"])
    assert plan["contracts"] is True


def test_a_ui_file_the_map_lists_still_selects_test_icon_font_through_the_folder_listing(tmp_path, monkeypatch):
    # A UI_MAP file runs its mapped scripts, and the module that globs ui/app/*.js reaches test_icon_font for it too,
    # as it does for a file the map does not list. The map lists this file, so no rule-4 "not in UI_MAP" note applies.
    smoke_tree(tmp_path)
    icon_font_tree(tmp_path)
    source(tmp_path, "ui/app/mapped.js", "export const icon = 'home';\n")
    source(tmp_path, "ui/tests/mapped-one.cjs", "// the browser script the map links to\n")
    monkeypatch.setattr(checks, "UI_MAP", {"ui/app/mapped.js": ["mapped-one"]})
    plan = checks.selection(["ui/app/mapped.js"], root=tmp_path, core=[])
    assert "backend/tests/test_icon_font.py" in plan["python"]
    assert "mapped-one" in plan["browser"] and "--all" not in plan["browser"]
    assert not any("is not in UI_MAP" in note for note in plan["uncertain"])


OWNED_TESTS = {  # each test the baselines/ table lists, and the module it tests
    "scripts/tests/test_lint_baseline.py": "lint_baseline",
    "scripts/tests/test_pyright_baseline.py": "pyright_baseline",
    "scripts/tests/test_baseline_files.py": "baseline_files",
}


def test_a_baseline_piece_runs_only_the_tests_that_own_its_folder():
    # One piece used to select 930 Python tests through the generic data path: every module that globs *.json on an
    # unknown folder reaches conftest. The owners' tests are the whole answer for a piece, however it changed.
    owners = sorted(checks.OWNED_DATA["baselines/"])
    assert set(owners) == set(OWNED_TESTS)
    for changed in (
        ["baselines/ruff/backend/hubdb.py.json"],  # changed
        ["baselines/pyright/backend/store.py.json"],  # changed, in the other baseline
        ["baselines/ruff/backend/removed.py.json"],  # deleted: the piece is no longer in the tree
        ["baselines/ruff/backend/old.py.json", "baselines/ruff/backend/new.py.json"],  # renamed: old and new names
    ):
        plan = checks.selection(changed)
        assert plan["python"] == owners, changed
        assert plan["browser"] == [] and plan["uncertain"] == [], changed
        assert not plan["caddy"] and not plan["contracts"], changed
        assert plan["reason"] == "owned data: baselines/ runs its owners' tests", changed


def test_a_ui_piece_runs_only_the_test_its_folder_lists(monkeypatch):
    # No ui/ folder has an owner yet, so the table lists none. The same rule holds for any folder it does list.
    monkeypatch.setitem(checks.OWNED_DATA, "ui/baselines/", ("scripts/tests/test_baseline_files.py",))
    plan = checks.selection(["ui/baselines/chat.json", "ui/baselines/chat-two.json"])
    assert plan["python"] == ["scripts/tests/test_baseline_files.py"]
    assert plan["browser"] == [] and plan["uncertain"] == []


def test_a_json_outside_the_owned_folders_keeps_the_generic_path(tmp_path, mapped, monkeypatch):
    # A data file that no owner lists still runs the test that reads it, and the table changes nothing for it.
    source(tmp_path, "backend/__init__.py", "")
    source(tmp_path, "backend/tests/fixtures/contract.json", "{}\n")
    source(
        tmp_path,
        "backend/tests/test_reads_contract.py",
        "def test_x():\n    open('backend/tests/fixtures/contract.json').read()\n",
    )
    smoke_tree(tmp_path)
    changed = ["backend/tests/fixtures/contract.json"]
    plan = checks.selection(changed, root=tmp_path, core=[], added=[])
    assert "backend/tests/test_reads_contract.py" in plan["python"]
    monkeypatch.setattr(checks, "OWNED_DATA", {})
    assert checks.selection(changed, root=tmp_path, core=[], added=[]) == plan


TOP_BASELINES = re.compile(r"(?<![\w./-])baselines/")


def test_owned_folders_list_existing_tests_of_their_scripts_and_only_the_owners_read_them():
    for test, module in OWNED_TESTS.items():
        assert (ROOT / test).is_file(), f"{test} is listed for baselines/ but does not exist"
        assert module in (ROOT / test).read_text(encoding="utf-8"), f"{test} does not test {module}"
    # The non-test Python files that name the baselines/ folder: the two owning scripts, and pr_checks.py (this table).
    # Only the top-level folder: ui/baselines/ is the UI's own per-file counts (scripts/ui_baseline.py), not owned here.
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "*.py"], capture_output=True, text=True, check=True
    ).stdout.split()
    readers = {
        name
        for name in tracked
        if "tests/" not in name and TOP_BASELINES.search((ROOT / name).read_text(encoding="utf-8", errors="replace"))
    }
    assert readers == {"scripts/lint_baseline.py", "scripts/pyright_baseline.py", "scripts/pr_checks.py"}


def test_a_ui_file_converted_to_typescript_keeps_the_map_entry_its_js_name_had(tmp_path, monkeypatch):
    # A script converted from ui/app/mapped.js to ui/app/mapped.ts keeps its UI_MAP entry: the map is keyed by the .js name.
    smoke_tree(tmp_path)
    icon_font_tree(tmp_path)
    source(tmp_path, "ui/app/mapped.ts", "export const icon: string = 'home';\n")
    source(tmp_path, "ui/tests/mapped-one.cjs", "// the browser script the map links to\n")
    monkeypatch.setattr(checks, "UI_MAP", {"ui/app/mapped.js": ["mapped-one"]})
    plan = checks.selection(["ui/app/mapped.ts"], root=tmp_path, core=[])
    assert "mapped-one" in plan["browser"] and "--all" not in plan["browser"]
    assert not any("is not in UI_MAP" in note for note in plan["uncertain"])


def test_a_remainder_under_five_minutes_of_work_a_vm_folds_into_one_vm():
    # A VM carries at least MIN_JOB_SECONDS (5 minutes) of tests per worker, setup excluded: a 1,100 s selection is one
    # VM of about 5 minutes, not two VMs of 2.3 minutes that each spend their setup on a small share of tests.
    assert checks.MIN_JOB_SECONDS >= 300
    assert checks.job_count(1100, "python") == 1
    # Under twice the minimum stays one VM; a selection only splits once each VM gets 5 minutes.
    assert checks.job_count(checks.WORKERS * checks.MIN_JOB_SECONDS * 2 - 1, "python") == 1
    assert checks.job_count(checks.WORKERS * checks.MIN_JOB_SECONDS * 2, "python") == 2
    counts = Counter({f"t{i}.py": 1 for i in range(11)})
    split, loads = checks.groups(counts, {name: 100 for name in counts})
    assert len(split) == 1 and loads == [1100.0]


def test_the_factory_legs_hold_four_minutes_of_work_each():
    # The sim's work is fixed by its scenarios, not by the selection: its legs fold to the same four-minute minimum.
    from evals.factory import run as factory

    timings = factory.timings()
    pairs = [(name, 1) for name in sorted(timings)]
    total = sum(timings[name] for name, _ in pairs)
    legs = max(
        1, min(factory.groups_for(pairs, timings), int(total // (factory.REMOTE_CPUS * checks.FACTORY_LEG_SECONDS)))
    )
    assert len(checks.factory_shards()) == legs
    assert legs == 1 or total / legs / factory.REMOTE_CPUS >= checks.FACTORY_LEG_SECONDS


def test_size_groups_raises_the_target_to_meet_a_ceiling_and_stops_at_the_cap():
    counts = Counter({f"backend/tests/test_{i}.py": 1 for i in range(346)})
    timings = {name: 6720 / 346 for name in counts}
    # An ordinary pull request's ceiling is 8 test jobs: 2 fixed jobs and 2 factory legs leave 4 for the Python VMs,
    # which takes VMs of about 8 minutes (the cap) for a 346-file change.
    sized = checks.size_groups(counts, timings, fixed=2, shards=2, ceiling=8)
    assert checks.JOB_SECONDS < sized["target"] <= checks.MAX_JOB_SECONDS
    assert sized["tests"] == len(sized["split"]) + 4 and sized["tests"] <= 8
    # Already under the ceiling, the target stays at the usual one.
    small = checks.size_groups(Counter({"a.py": 1}), {"a.py": 100}, fixed=1, shards=0, ceiling=8)
    assert small["target"] == checks.JOB_SECONDS and small["tests"] == 2
    # Past the cap the plan keeps the cap's VMs and reports the count it has.
    big = Counter({f"t{i}.py": 1 for i in range(1000)})
    capped = checks.size_groups(big, {name: 100 for name in big}, shards=2, ceiling=8)
    assert capped["target"] == checks.MAX_JOB_SECONDS and capped["tests"] > 8


def test_a_queue_group_packs_toward_six_test_jobs_of_up_to_twelve_minutes():
    """Every queue group shares the organisation's ~60 concurrent hosted jobs, so a group packs its tests into fewer,
    longer VMs than a pull request: each VM spends about two minutes on setup. A selection too big for six VMs at the cap
    keeps the cap's VMs: only a narrower selection brings it within six."""
    assert checks.CEILING["merge_group"] == 6 and checks.QUEUE_MAX_JOB_SECONDS > checks.MAX_JOB_SECONDS
    # Nov 8's typical backend group: about 930 files, 22,300 one-worker seconds, a browser group, the contract tests and
    # two factory legs. At the pull request's cap it takes 13 Python VMs; the queue's cap takes fewer.
    counts = Counter({f"backend/tests/test_{i}.py": 1 for i in range(930)})
    timings = {name: 22300 / 930 for name in counts}
    browser = (Counter(dict.fromkeys([f"s{i}" for i in range(32)], 1)), dict.fromkeys([f"s{i}" for i in range(32)], 50))
    pr = checks.size_groups(counts, timings, browser=browser, fixed=1, shards=2, ceiling=checks.CEILING["merge_group"])
    queue = checks.size_groups(
        counts,
        timings,
        browser=browser,
        fixed=1,
        shards=2,
        ceiling=checks.CEILING["merge_group"],
        cap=checks.QUEUE_MAX_JOB_SECONDS,
    )
    assert queue["target"] == checks.QUEUE_MAX_JOB_SECONDS and len(queue["split"]) < len(pr["split"])
    # 13 test jobs (9 Python, a browser, the contracts, 2 factory legs) against 17 at the pull request's cap.
    assert queue["tests"] == 13 and pr["tests"] == 17
    # No VM is planned past the cap by more than one file's worth.
    assert max(queue["loads"]) / checks.WORKERS + checks.SETUP_SECONDS["python"] <= checks.QUEUE_MAX_JOB_SECONDS + 30
    # A small group stays at the usual target in one or two VMs: 30 minutes of one-worker tests is one VM.
    small = Counter({f"t{i}.py": 1 for i in range(20)})
    sized = checks.size_groups(
        small, {n: 90 for n in small}, ceiling=checks.CEILING["merge_group"], cap=checks.QUEUE_MAX_JOB_SECONDS
    )
    assert sized["target"] == checks.JOB_SECONDS and len(sized["split"]) == 1


def test_queue_jobs_start_only_for_a_change_to_their_folders_and_all_start_for_an_unreadable_diff(mapped):
    assert checks.queue_jobs(["backend/task_loops.py", "ui/app/chat.ts"]) == dict.fromkeys(
        checks.QUEUE_JOB_PATHS, False
    )
    # The screen relay is a Rust crate the lint checks, and the Caddy image compiles it.
    assert checks.queue_jobs(["relay-rs/src/main.rs"]) == {"rust_lint": True, "desktop": False, "recorder": False}
    assert checks.selection(["relay-rs/src/main.rs"])["caddy"]
    for name in ("runner-rs/src/sessions.rs", "tools/release-command/src/main.rs", "contracts/runner/schema.json"):
        assert checks.queue_jobs([name])["rust_lint"], name
    assert checks.queue_jobs(["scripts/rust_checks.sh"])["rust_lint"]
    assert checks.queue_jobs(["app/src/main.rs"]) == {"rust_lint": False, "desktop": True, "recorder": False}
    assert checks.queue_jobs(["recorder/src/lib.rs"]) == {"rust_lint": False, "desktop": False, "recorder": True}
    # Anchored at the root: a folder named app/ or recorder/ deeper in the tree is not the desktop app or the recorder.
    assert checks.queue_jobs(
        ["backend/app/x.py", "docs/recorder/x.md", "scripts/tests/rust_checks.sh"]
    ) == dict.fromkeys(checks.QUEUE_JOB_PATHS, False)
    # A diff that could not be read (or is empty) never skips a job.
    assert checks.queue_jobs([]) == dict.fromkeys(checks.QUEUE_JOB_PATHS, True)


def test_the_plan_decides_the_queue_jobs_on_both_tiers_and_a_group_checks_its_merge_commit_without_a_static_vm(
    tmp_path, monkeypatch, mapped
):
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_task_loops.py": 2}))
    monkeypatch.setattr(checks, "image_checks", lambda changed: None)
    monkeypatch.setattr(
        checks,
        "changed_files",
        lambda base, head="HEAD", only=None: [] if only else ["app/src/main.rs", "backend/task_loops.py"],
    )
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    plan = checks.prepare("merge_group", "queue-base", tmp_path / "plan.json", head="queue-head")
    assert plan["queue_jobs"] == {"rust_lint": False, "desktop": True, "recorder": False}
    lines = output.read_text().splitlines()
    assert {"rust_lint=false", "desktop=true", "recorder=false"} <= set(lines)
    # A ready pull request runs the same jobs under the same conditions (PR checks == queue checks), so its plan writes
    # the same outputs from its own diff.
    monkeypatch.setattr(checks, "merge_parent", lambda base: base)
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    output.write_text("")
    plan = checks.prepare("pull_request", "pr-base", tmp_path / "plan.json")
    assert plan["queue_jobs"] == {"rust_lint": False, "desktop": True, "recorder": False}
    assert {"rust_lint=false", "desktop=true", "recorder=false"} <= set(output.read_text().splitlines())

    jobs = workflow_jobs()["jobs"]
    outputs = jobs["plan"]["outputs"]
    for job, output_name in (("rust-lint", "rust_lint"), ("desktop-app", "desktop"), ("recorder", "recorder")):
        assert outputs[output_name] == f"${{{{ steps.plan.outputs.{output_name} }}}}", job
        assert jobs[job]["needs"] == "plan", job
        assert f"needs.plan.outputs.{output_name} == 'true'" in jobs[job]["if"], job
        # The plan decided: no step inside the job diffs the branch again.
        assert not any("git diff" in str(step.get("run", "")) for step in jobs[job]["steps"]), job
    # The static checks VM runs on pull requests only; a queue group's plan checks its merge commit in its place.
    assert "github.event_name == 'pull_request'" in jobs["static"]["if"]


def test_a_queue_group_runs_every_whole_tree_static_check_on_its_merge_commit_beside_its_selection(tmp_path):
    """Two pull requests can pass their own static checks and break a whole-tree one together (a route one removes and
    the other calls), so a queue group runs those on its merge commit. The plan job starts them before the selection and
    reports them after it, so they cost no VM and little wall time."""
    jobs = workflow_jobs()["jobs"]
    steps = jobs["plan"]["steps"]
    names = [str(step.get("name", "")) for step in steps]
    start = names.index("Start the merge commit checks")
    select = names.index("Select affected tests and size measured Python and browser groups")
    report = names.index("Merge commit checks")
    assert names.index("Install (five minutes per attempt, one retry)") < start < select < report
    for index in (start, report):
        assert steps[index]["if"] == "github.event_name == 'merge_group'"
    assert 'merge_commit_checks.sh "$BASE_SHA" "$QUEUE_HEAD"' in steps[start]["run"]
    assert "set +e" in steps[start]["run"] and steps[start]["run"].rstrip().endswith("&")
    assert steps[report]["timeout-minutes"] <= 10 and 'exit "$(cat' in steps[report]["run"]
    script = (ROOT / "scripts/ci/merge_commit_checks.sh").read_text()
    # Every static check runs on the merge commit too, the per-file ones included: they share configuration (a lint rule
    # one pull request turns on can fail the other's new code).
    static = [str(step.get("run", "")) for step in jobs["static"]["steps"] if step.get("run")]
    found = {re.search(r"scripts/[\w/]+\.(?:py|sh)", run).group(0) for run in static if "scripts/" in run}
    found -= {"scripts/ci_install.sh"}  # the static job's install, which the plan job has already done
    found |= {"-m backend.openapi_v2 --check", "npm ci --ignore-scripts", "pinned_tool.sh actionlint"}
    assert found >= {"scripts/check_migration_order.py", "scripts/lint.sh", "scripts/check_ui_lint.sh"}
    for name in found:
        assert name in script, name
    assert 'LINT_BASE_REF="$base"' in script and 'PYRIGHT_BASE_REF="$base"' in script

    # Each check runs even after one fails, and any failure fails the script.
    fake = tmp_path / "repo"
    (fake / "scripts/ci").mkdir(parents=True)
    (fake / "scripts/ci/merge_commit_checks.sh").write_text(script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    for tool, code in (("python3", 0), ("bash", 1), ("uvx", 0), ("npm", 0)):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> {log}\nexit {code}\n')
        stub.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(fake / "scripts/ci/merge_commit_checks.sh"), "base", "head"],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    calls = log.read_text().splitlines()
    bash_checks = sum(line.startswith("check bash ") for line in script.splitlines())
    assert sum(call.startswith("bash ") for call in calls) == bash_checks  # every check ran past the first failure
    assert calls[-1] == "bash scripts/pinned_tool.sh actionlint"


def test_a_queue_group_supersedes_only_the_older_queue_groups_of_its_own_pull_request():
    runs = [
        {"id": 1, "run_number": 10, "event": "merge_group", "head_branch": "gh-readonly-queue/main/pr-2905-aaa111"},
        {"id": 2, "run_number": 11, "event": "merge_group", "head_branch": "gh-readonly-queue/main/pr-2905-bbb222"},
        {"id": 3, "run_number": 11, "event": "merge_group", "head_branch": "gh-readonly-queue/main/pr-2906-ccc333"},
        {"id": 4, "run_number": 13, "event": "merge_group", "head_branch": "gh-readonly-queue/main/pr-2905-ddd444"},
        {"id": 5, "run_number": 9, "event": "pull_request", "head_branch": "pr-2905-push"},
    ]
    head = "refs/heads/gh-readonly-queue/main/pr-2905-eee555"
    assert checks.superseded_runs(runs, head, 12) == [1, 2]
    assert checks.superseded_runs(runs, "refs/heads/some-branch", 12) == []


def test_a_queue_group_cancels_older_groups_for_its_pull_request_and_no_pull_request_run_is_affected():
    import yaml

    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-checks.yml").read_text())
    plan = doc["jobs"]["plan"]
    assert plan["permissions"]["actions"] == "write"
    step = next(s for s in plan["steps"] if s.get("name") == "Cancel older queue groups for this pull request")
    assert "github.event_name == 'merge_group'" in step["if"] and step.get("continue-on-error") is True
    assert "cancel-superseded" in step["run"] and "--head-ref" in step["run"] and "--run-number" in step["run"]
    # Pull requests keep their own group, keyed on the number, so no queue group can share it.
    assert doc["concurrency"]["group"] == "pr-checks-${{ github.event.pull_request.number || github.ref }}"


def junit(*skips):
    cases = "".join(
        f'<testcase classname="t" name="n{i}">'
        + (f'<skipped type="{kind}" message="m"/>' if kind else "")
        + "</testcase>"
        for i, kind in enumerate(skips)
    )
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>'


def test_a_suite_whose_only_tests_are_quarantined_failures_passes_but_an_all_skipped_one_does_not(
    tmp_path, monkeypatch
):
    """pr_checks.run fails a Python suite that skipped every test. A quarantined test that failed ran: it is an xfail,
    which JUnit writes as a skipped element too, and must not make the suite fail."""
    plan = {"matrix": {"include": [{"suite": "python-1", "files": ["fixture_tests/test_x.py"]}]}}
    (tmp_path / "fixture_tests").mkdir(parents=True)
    (tmp_path / "fixture_tests/test_x.py").touch()
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    report = {}

    def fake(command, **kw):
        (tmp_path / "ci-results/python-1.xml").write_text(report["xml"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checks.subprocess, "run", fake)
    report["xml"] = junit("pytest.xfail")
    assert checks.run(tmp_path / "plan.json", "python-1", root=tmp_path, quarantine=True) == 0
    report["xml"] = junit("pytest.skip", None)
    assert checks.run(tmp_path / "plan.json", "python-1", root=tmp_path, quarantine=True) == 0
    report["xml"] = junit("pytest.skip", "pytest.skip")
    with pytest.raises(RuntimeError, match="skip every test"):
        checks.run(tmp_path / "plan.json", "python-1", root=tmp_path, quarantine=True)


def test_the_queue_and_pr_suites_pass_the_flag_and_release_validation_never_does(tmp_path, monkeypatch, capsys):
    plan = {"matrix": {"include": [{"suite": "python-1", "files": ["fixture_tests/test_x.py"]}]}}
    (tmp_path / "fixture_tests").mkdir(parents=True)
    (tmp_path / "fixture_tests/test_x.py").touch()
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    calls = []
    monkeypatch.setattr(
        checks.subprocess, "run", lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=1)
    )
    assert checks.run(tmp_path / "plan.json", "python-1", root=tmp_path) == 1
    assert "--quarantine" not in calls[-1]  # the release gate's incremental VMs call run() without it
    assert checks.run(tmp_path / "plan.json", "python-1", root=tmp_path, quarantine=True) == 1
    assert "--quarantine" in calls[-1] and calls[-1][-1] == "fixture_tests/test_x.py"
    # The job log names the files a leg ran: the pass evidence detection needs for a test in it.
    assert quarantine.test_files(capsys.readouterr().out) == ["fixture_tests/test_x.py"]
    workflows = ROOT / ".github/workflows"
    tests = yaml.safe_load((workflows / "pr-checks.yml").read_text())["jobs"]["tests"]
    step = next(s for s in tests["steps"] if s.get("name") == "Run selected tests")
    assert "scripts/pr_checks.py run --quarantine" in step["run"]
    for name in ("ci.yml", "release.yml", "hotfix.yml", "train.yml"):
        assert "quarantine" not in (workflows / name).read_text(), name
