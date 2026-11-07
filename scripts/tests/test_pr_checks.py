"""CI selection cannot omit uncertain work or turn an empty/red test run green."""

from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

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


def test_uncertain_changes_and_main_run_everything_and_ui_keeps_its_map(tmp_path, mapped):
    for changed in (
        ["conftest.py"],
        ["backend/tests/conftest.py"],
        ["requirements.txt"],
        ["pytest.ini"],
        ["package-lock.json"],
        ["backend/missing.py"],
        [],
        ["docs/missing.md"],
    ):
        plan = checks.selection(changed)
        assert plan["python"] is None and plan["browser"] is None and plan["caddy"], changed
    main = checks.selection(["backend/task_loops.py"], "push")
    assert main["python"] is None and main["browser"] is None and main["caddy"]
    ui = checks.selection(["ui/app/chat.js"])
    # The audited UI map's scripts run, with any core script the dependency map finds using the file.
    assert {"chat", "imported-history"} <= set(ui["browser"]) and "--all" not in ui["browser"]
    assert checks.selection(["ui/app/core.js"])["browser"] == ["--all"]
    assert checks.selection(["runner-rs/harnesses/codex.toml"])["conformance"]
    assert checks.selection(["runner-rs/src/sessions.rs"])["conformance"]
    assert checks.selection(["backend/harness_actions.py"])["conformance"]
    # A removed mapped browser test makes the map uncertain, even if its sibling remains.
    (tmp_path / "ui/app").mkdir(parents=True)
    (tmp_path / "ui/app/chat.js").touch()
    assert checks.selection(["ui/app/chat.js"], root=tmp_path)["python"] is None


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
    assert checks.prepare("pull_request", "base", target)["python"] is None
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
    graph = SimpleNamespace(
        files=[], unparsed=set(), tracked=set(), run_by={}, affected=lambda *args: set(), readers=lambda *args: set()
    )
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
        graph.files.append(name)
        assert checks.selection([name], root=tmp_path, graph=graph, core=[])["contracts"], name
    # Removing a template must still catch refusal entries that have become stale.
    (tmp_path / "templates/bot/role.md").unlink()
    assert checks.selection(["templates/bot/role.md"], root=tmp_path)["contracts"]
    source(tmp_path, "ui/app/unrelated.js", "")
    graph.files.append("ui/app/unrelated.js")
    assert not checks.selection(["ui/app/unrelated.js"], root=tmp_path, graph=graph, core=[])["contracts"]
    assert not checks.selection(["README.md"], root=tmp_path)["contracts"]
    assert checks.selection([], root=tmp_path)["contracts"]
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
    for changed in (["backend/harness_actions.py"], ["backend/task_loops.py"], ["ui/app/chat.js"], []):
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
            assert calls.splitlines() == ["npm ci --fetch-retries=1", "npm run --silent build:ui"]
        else:
            assert "--no-download install" in calls and "playwright install chromium webkit" in calls


def test_cached_system_packages_match_the_locked_browser_version():
    manifest = json.loads((ROOT / "scripts/playwright-system-packages.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())
    assert manifest["playwright"] == lock["packages"]["node_modules/playwright-core"]["version"]
    assert manifest["platform"] == "ubuntu24.04-x64"
    assert len(set(manifest["packages"])) == len(manifest["packages"]) > 0


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


def test_hub_change_selects_every_test_that_imports_it_transitively_and_no_other(tmp_path, mapped):
    # A hub imported through a shared helper (the way most tests reach backend.app through test_api) selects every
    # test that imports the helper, at module level, in a function, by importlib or as a conftest fixture body.
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
    hub = checks.selection(["backend/hub.py"], root=tmp_path, core=[])
    assert hub["python"] == [
        "backend/tests/test_api.py",
        "backend/tests/test_dynamic.py",
        "backend/tests/test_fixture.py",
        "backend/tests/test_late.py",
        "backend/tests/test_uses_helper.py",
    ]
    assert hub["browser"] == [] and not hub["caddy"] and hub["reason"] == "measured dependency map"
    assert checks.selection(["backend/leaf.py"], root=tmp_path, core=[])["python"] == ["backend/tests/test_leaf.py"]
    # A computed import may load anything, so the file that makes it runs whatever changes.
    source(tmp_path, "backend/tests/test_any.py", "import importlib\nimportlib.import_module(name)\n")
    assert "backend/tests/test_any.py" in checks.selection(["backend/leaf.py"], root=tmp_path, core=[])["python"]
    source(tmp_path, "backend/broken.py", "syntax error !\n")
    assert checks.selection(["backend/broken.py"], root=tmp_path)["python"] is None
    # On this checkout: a test-only change runs that test and the few that import, read or list it (or every file,
    # like test_local_imports), not the suite; the scheduler's guard follows
    # task_loops; the shared helper reaches the Caddy container test through caddy_assets.
    # test_ddl_guard scans all of backend/ (it skips tests at run time, which the map cannot see), so it is the twentieth;
    # test_schema_every_table reads every table the source makes in the same way, so it is the twenty-first;
    # test_system_jobs_list scans all of backend/ for recorded job keys, so it is one more.
    alone = checks.selection(["backend/tests/test_task_loops.py"])["python"]
    assert "backend/tests/test_task_loops.py" in alone and len(alone) < 26
    loops = checks.selection(["backend/task_loops.py"])
    assert "backend/tests/test_scheduler.py" in loops["python"] and not loops["caddy"]
    assert checks.selection(["backend/caddy_assets.py"])["caddy"]


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
    assert picked("docs/removed.md") is None
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
    assert "backend/tests/test_icon_font.py" in checks.selection(["ui/app/api.js"], added=[])["python"]


def test_groups_are_sized_to_the_job_target_and_browser_scripts_split_by_measured_time(tmp_path, monkeypatch, mapped):
    # One VM holds WORKERS x (target - setup) seconds of one-worker test time; never 0 VMs, never past the cap.
    per_python = checks.WORKERS * (checks.JOB_SECONDS - checks.SETUP_SECONDS["python"])
    assert checks.job_count(0, "python") == 1
    assert checks.job_count(per_python, "python") == 1 and checks.job_count(per_python + 1, "python") == 2
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
    assert len(split) == checks.job_count(3000, "python") and max(loads) <= per_python
    assert sorted(sum(split, [])) == sorted(counts)

    names = [f"s{i}" for i in range(40)]
    listed = []
    monkeypatch.setattr(checks, "browser_scripts", lambda args: listed.append(args) or names)
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_api.py": 1}))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(
        checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["ui/app/core.js"]
    )
    timings = tmp_path / "durations.json"
    timings.write_text(json.dumps({f"ui/tests/{n}.cjs": 60 for n in names}))
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", timings)
    assert listed == [["--all"]]
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


def test_the_main_cache_warm_up_saves_the_keys_pr_checks_restore():
    """Without main pushes, pr-cache.yml keeps main's caches fresh. Each cache it warms is a PR-check cache with the
    same action, key, path or shared key, on the same Spot runner (a key holds the runner image and arch), and every
    cache it fills itself is looked up first, so a warm day downloads nothing."""
    import yaml

    pr = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-checks.yml").read_text())
    warm = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-cache.yml").read_text())

    def caches(doc):
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                uses, args = step.get("uses", ""), dict(step.get("with") or {})
                if "actions/cache@" in uses or "rust-cache@" in uses or args.get("cache"):
                    lookup = args.pop("lookup-only", None)
                    yield args.get("key") or args.get("shared-key") or args["cache"], uses, args, job["runs-on"], lookup

    restored = list(caches(pr))
    warmed = list(caches(warm))
    assert (
        {w[0] for w in warmed}
        == {r[0] for r in restored}
        == {
            "pip",
            "npm",
            "pr-rust-lint",
            "pr-desktop",
            "pr-conformance",
            "pr-recorder",
            "pr-slow-relay",
            "playwright-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('package-lock.json') }}",
            "playwright-system-ubuntu24-x64-${{ steps.image.outputs.version }}-${{ hashFiles('package-lock.json', "
            "'scripts/ci_install.sh', 'scripts/playwright-system-packages.json') }}",
        }
    )
    for ident, uses, args, runner, lookup in warmed:
        if runner == "macos-latest":
            machine = runner
        else:
            assert runner.startswith("runs-on=${{ github.run_id }}/") and "spot=false" not in runner, ident
            machine = runner.split("}}", 1)[1]
            assert "gp3:125mbs" in machine, ident
        assert any((uses, args) == (r[1], r[2]) and machine in r[3] for r in restored), ident
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
    assert plan["reason"] == "measured dependency map" and plan["python"] is not None
    expected = sorted(set(checks.selection(["backend/task_loops.py"])["python"]) & set(files))
    assert plan["python"] == expected
    # An empty group diff falls back to the full suite, as a pull request's does.
    monkeypatch.setattr(checks, "changed_files", lambda base, head="HEAD", only=None: [])
    assert checks.prepare("merge_group", "queue-base", target, head="queue-head")["python"] is None


def test_each_push_runs_only_the_cheap_checks_on_hosted_runners_and_the_queue_runs_the_rest():
    doc = workflow_jobs()
    assert doc[True]["merge_group"] == {"types": ["checks_requested"]}
    assert doc[True]["pull_request"]["types"] == ["opened", "synchronize", "reopened", "ready_for_review"]
    assert doc["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.ref" in doc["concurrency"]["group"]
    pull_request_jobs = {name for name, job in doc["jobs"].items() if "merge_group" not in str(job.get("if", ""))}
    assert pull_request_jobs == {"plan", "static", "checks"}
    for name in pull_request_jobs - {"checks"}:
        runs_on = doc["jobs"][name]["runs-on"]
        # Only the merge queue takes the RunsOn label; every other event gets the free hosted runner.
        assert runs_on.startswith("${{ github.event_name == 'merge_group' && format('runs-on={0}/"), name
        assert runs_on.endswith("|| 'ubuntu-24.04' }}"), name
    assert doc["jobs"]["checks"]["runs-on"] == "ubuntu-24.04"
    for name in set(doc["jobs"]) - pull_request_jobs:
        assert "github.event_name == 'merge_group'" in str(doc["jobs"][name]["if"]), name


def test_runner_labels_stay_at_two_or_four_vcpus():
    """Every RunsOn VM stays at xlarge (4 vCPU) or smaller; each x64 label takes main's instance families (#2834)."""
    text = (ROOT / ".github" / "workflows" / "pr-checks.yml").read_text()
    assert set(re.findall(r"runner=(\d+)cpu", text)) <= {"2", "4"}
    families = re.findall(r"runner=\d+cpu-linux-x64(/family=[^/']+)?/", text)
    assert len(families) == 6 and set(families) == {"/family=m8+m7+m6+r8+r7"}, families
    # The Caddy image takes the same families on amd64 only; arm64 keeps its label.
    assert "matrix.arch != 'arm64' && '/family=m8+m7+m6+r8+r7' || ''" in text


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
    assert exit_code({**passing, "plan": "skipped"}) == 0


def test_exempt_entries_are_real_slow_test_files_each_with_a_reason():
    assert checks.check_slow_exempt(ROOT) == []
    exempt = checks.slow_exempt(ROOT)
    assert exempt and all(reason.strip() for reason in exempt.values())


def test_the_exempt_check_names_missing_and_stale_entries_and_empty_reasons(tmp_path):
    source(tmp_path, "scripts/pr_slow_exempt.json", json.dumps({"tests/test_plain.py": "x", "tests/test_gone.py": "y"}))
    source(tmp_path, "tests/test_plain.py", "def test_a():\n    pass\n")
    assert checks.check_slow_exempt(tmp_path) == [
        "tests/test_plain.py: no test in it is marked slow, so its exemption is stale",
        "tests/test_gone.py: not a test file in this checkout",
    ]
    source(tmp_path, "scripts/pr_slow_exempt.json", json.dumps({"tests/test_plain.py": " "}))
    with pytest.raises(RuntimeError, match="non-empty reason"):
        checks.slow_exempt(tmp_path)


def test_slow_marks_are_read_on_modules_classes_functions_and_parametrized_cases():
    import ast

    def marked(text):
        return checks.slow_marked(ast.parse(text))

    assert marked("import pytest\npytestmark = pytest.mark.slow\n")
    assert marked("import pytest\nclass TestA:\n    pytestmark = [pytest.mark.slow]\n")
    assert marked("import pytest\n@pytest.mark.slow\ndef test_a():\n    pass\n")
    assert marked(
        "import pytest\n@pytest.mark.parametrize('x', [pytest.param(1, marks=pytest.mark.slow)])\n"
        "def test_a(x):\n    pass\n"
    )
    assert not marked("import pytest\n@pytest.mark.skipif(True, reason='x')\ndef test_a():\n    pass\n")


def test_slow_collection_counts_each_files_tests_and_reads_no_match_as_none(monkeypatch):
    out = "backend/tests/test_a.py::test_one\nbackend/tests/test_a.py::test_two[1]\nbackend/tests/test_b.py::test_c\n\n"
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=out, stderr=""))
    assert checks.collect_slow(["x"]) == Counter({"backend/tests/test_a.py": 2, "backend/tests/test_b.py": 1})
    assert checks.collect_slow([]) == Counter()
    assert checks.collect_tests([]) == Counter()
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=5, stdout="", stderr=""))
    assert checks.collect_slow(["x"]) == Counter()


def test_a_slow_candidate_is_a_test_that_directly_uses_the_change_and_the_closure_is_not_slow_selected(mapped):
    plan = checks.selection(["backend/scheduler.py"])
    assert plan["slow"][0] == "backend/tests/test_scheduler.py"  # the test named for the module it runs comes first
    assert "backend/tests/test_scheduler.py" in plan["slow"]
    # Default tests follow the closure; slow candidates stop at one import, so there are fewer of them.
    assert set(plan["slow"]) < set(plan["python"])
    assert "relay" not in plan


def test_slow_candidates_stop_at_one_import_and_never_pass_through_a_conftest_or_a_fixture(tmp_path):
    source(tmp_path, "pkg/__init__.py", "")
    source(tmp_path, "pkg/mod.py", "VALUE = 1\n")
    source(tmp_path, "pkg/helper.py", "from pkg import mod\n")
    source(
        tmp_path,
        "tests/test_direct.py",
        "import pytest\nfrom pkg import mod\n\n@pytest.mark.slow\ndef test_a():\n    pass\n",
    )
    source(tmp_path, "tests/test_chain.py", "from pkg import helper\n\ndef test_b():\n    pass\n")
    source(
        tmp_path,
        "tests/conftest.py",
        "import pytest\nfrom pkg import mod\n\n@pytest.fixture\ndef shared():\n    return mod.VALUE\n",
    )
    source(tmp_path, "tests/test_fixture.py", "def test_c(shared):\n    pass\n")
    graph = checks.DependencyMap(tmp_path)
    assert checks.slow_candidates(graph, ["pkg/mod.py"]) == ["tests/test_direct.py"]
    assert checks.slow_candidates(graph, ["pkg/helper.py"]) == ["tests/test_chain.py"]
    assert checks.slow_candidates(graph, ["tests/conftest.py"]) == []


def test_slow_candidates_rank_changed_tests_then_the_test_named_for_the_module_then_runners_then_readers(tmp_path):
    source(tmp_path, "pkg/__init__.py", "")
    source(tmp_path, "pkg/store.py", "X = 1\n")
    source(tmp_path, "data/rules.json", "{}\n")
    source(tmp_path, "tests/test_store.py", "from pkg import store\n")
    source(tmp_path, "tests/test_other.py", "from pkg import store\n")
    source(tmp_path, "tests/test_reads.py", "import json\nopen('data/rules.json')\n")
    source(tmp_path, "tests/test_changed.py", "def test_a():\n    pass\n")
    graph = checks.DependencyMap(tmp_path)
    changed = ["pkg/store.py", "data/rules.json", "tests/test_changed.py"]
    assert checks.slow_candidates(graph, changed) == [
        "tests/test_changed.py",
        "tests/test_store.py",
        "tests/test_other.py",
        "tests/test_reads.py",
    ]


def test_one_serial_vm_keeps_the_nearest_slow_files_that_fit_its_budget_and_defers_the_rest():
    budget = checks.JOB_SECONDS - checks.SETUP_SECONDS["slow"]
    names = ["a.py", "b.py", "c.py", "d.py"]
    counts = Counter(dict.fromkeys(names, 1))
    timings = {"a.py": 110.0, "b.py": 150.0, "c.py": 90.0, "d.py": 5.0}
    kept, deferred, seconds = checks.slow_fit(names, counts, timings, counts)  # every test slow: the whole file's time
    assert kept == ["a.py", "c.py", "d.py"] and deferred == ["b.py"]
    assert seconds == 205.0 and seconds <= budget


def test_a_slow_file_is_budgeted_at_its_slow_share_of_its_measured_time():
    # The measured time is the whole file's, default and slow tests together: one slow test in fifty is one fiftieth.
    counts = Counter({"big.py": 1, "half.py": 2, "new.py": 3})
    totals = Counter({"big.py": 50, "half.py": 4})
    timings = {"big.py": 287.5, "half.py": 200.0}
    seconds = checks.slow_seconds(counts, totals, timings)
    assert seconds["big.py"] == 5.75 and seconds["half.py"] == 100.0
    # A file with no timing takes the seconds per slow test that the timed files show (105.75 over 3 tests).
    assert round(seconds["new.py"], 2) == 105.75
    # Without the share, big.py alone (287.5 s) would not fit the budget; its slow share keeps it.
    kept, deferred, total = checks.slow_fit(["big.py", "half.py"], counts, timings, totals)
    assert kept == ["big.py", "half.py"] and deferred == [] and round(total, 2) == 105.75
    assert checks.slow_fit(["big.py"], counts, {"big.py": 287.5}, Counter({"big.py": 1}))[1] == ["big.py"]


def test_slow_selection_is_empty_on_main_and_docs_and_uncertain_changes_keep_only_their_changed_tests(mapped):
    assert checks.selection(["backend/scheduler.py"], "push")["slow"] == []
    assert checks.selection(["docs/images/none.png"])["slow"] == []
    assert checks.selection(["pytest.ini"])["slow"] == []
    assert checks.selection([])["slow"] == []
    assert checks.selection(["pytest.ini", "backend/tests/test_scheduler.py"])["slow"] == [
        "backend/tests/test_scheduler.py"
    ]


def test_the_plan_confirms_slow_files_by_collection_drops_exempt_ones_and_shards_one_process_per_vm(
    tmp_path, monkeypatch
):
    scheduler = "backend/tests/test_scheduler.py"
    exempt_file = "backend/tests/test_e2e_cli.py"
    plain = "backend/tests/test_plain.py"
    collected = []
    picked = {
        "python": [],
        "browser": [],
        "caddy": True,
        "conformance": False,
        "rust": [],
        "contracts": False,
        "slow": [scheduler, exempt_file, plain],
        "reason": "measured dependency map",
    }
    monkeypatch.setattr(checks, "selection", lambda *a, **kw: dict(picked))
    monkeypatch.setattr(checks, "slow_exempt", lambda root=ROOT: {exempt_file: "contracts job"})
    monkeypatch.setattr(
        checks,
        "collect_slow",
        lambda files=None, root=ROOT: collected.append(files) or Counter({scheduler: 2, exempt_file: 1}),
    )
    monkeypatch.setattr(checks, "collect_tests", lambda files, root=ROOT: Counter({scheduler: 2}))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(checks, "changed_files", lambda *a, **kw: [])
    target = tmp_path / "plan.json"
    plan = checks.prepare("merge_group", "base", target)
    assert collected == [[scheduler, exempt_file, plain]]
    assert plan["slow"] == [scheduler] and plan["slow_exempt"] == [exempt_file] and plan["slow_deferred"] == []
    assert {"suite": "slow-1", "files": [scheduler]} in plan["matrix"]["include"]
    assert all("relay" not in job for job in plan["matrix"]["include"])
    picked.update(slow=[])
    assert checks.prepare("merge_group", "base", target)["matrix"]["include"] == [{"suite": "selection", "files": []}]


def test_the_queue_runs_at_most_one_slow_vm_and_names_the_slow_files_it_defers_in_the_job_summary(
    tmp_path, monkeypatch
):
    nearest, second = "backend/tests/test_nearest.py", "backend/tests/test_second.py"
    picked = {
        "python": [],
        "browser": [],
        "caddy": True,
        "conformance": False,
        "rust": [],
        "contracts": False,
        "slow": [nearest, second],
        "reason": "measured dependency map",
    }
    monkeypatch.setattr(checks, "selection", lambda *a, **kw: dict(picked))
    monkeypatch.setattr(checks, "slow_exempt", lambda root=ROOT: {})
    monkeypatch.setattr(checks, "collect_slow", lambda files=None, root=ROOT: Counter({nearest: 1, second: 1}))
    monkeypatch.setattr(checks, "collect_tests", lambda files, root=ROOT: Counter({nearest: 1, second: 1}))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(checks, "changed_files", lambda *a, **kw: [])
    durations = tmp_path / "durations.json"
    durations.write_text(json.dumps({nearest: 200.0, second: 60.0}))  # 200 s fits the budget; 60 s more does not
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    plan = checks.prepare("merge_group", "base", tmp_path / "plan.json", str(durations))
    slow_jobs = [job for job in plan["matrix"]["include"] if job["suite"].startswith("slow-")]
    assert slow_jobs == [{"suite": "slow-1", "files": [nearest]}]
    assert plan["slow"] == [nearest] and plan["slow_deferred"] == [second]
    text = summary.read_text()
    assert "Slow tests deferred to release validation (1)" in text and f"- `{second}`" in text


def test_a_slow_shard_runs_its_files_in_one_process_and_names_the_tests_it_skipped(tmp_path, monkeypatch, capsys):
    scheduler = "backend/tests/test_scheduler.py"
    target = tmp_path / "plan.json"
    target.write_text(
        json.dumps({"slow": [scheduler], "matrix": {"include": [{"suite": "slow-1", "files": [scheduler]}]}})
    )
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="existing file list"):
        checks.run(target, "slow-1")
    source(tmp_path, scheduler, "")
    calls = []
    monkeypatch.setattr(
        checks.subprocess, "run", lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=0)
    )
    (tmp_path / "ci-results").mkdir()
    (tmp_path / "ci-results/slow-1.xml").write_text(
        "<testsuite><testcase name='test_a'/><testcase name='test_b'><skipped/></testcase></testsuite>"
    )
    assert checks.run(target, "slow-1") == 0
    assert calls == [
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "0",
            *checks.PYTEST_FLAGS,
            "-o",
            "junit_family=xunit1",
            "--junitxml=ci-results/slow-1.xml",
            "-m",
            "slow",
            scheduler,
        ]
    ]
    assert "::warning::1 slow test(s) skipped in slow-1: test_b" in capsys.readouterr().out


def test_the_slow_shards_build_the_relay_before_their_tests_and_upload_their_results():
    steps = workflow_jobs()["jobs"]["tests"]["steps"]
    build = next(s for s in steps if "cargo build --locked" in s.get("run", ""))
    assert build["if"] == "startsWith(matrix.suite, 'slow-')"
    assert build["working-directory"] == "relay-rs"
    assert "TICO_SCREEN_RELAY_BIN=$PWD/target/debug/tico-screen-relay" in build["run"]
    assert "GITHUB_ENV" in build["run"]
    names = [s.get("name", s.get("uses")) for s in steps]
    assert names.index(build["name"]) < names.index("Run selected tests")
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert "startsWith(matrix.suite, 'slow-')" in upload["if"]


def test_the_static_checks_check_the_exempt_list_on_every_push():
    steps = workflow_jobs()["jobs"]["static"]["steps"]
    assert any(step.get("run") == "python3 scripts/pr_checks.py slow-check" for step in steps)
