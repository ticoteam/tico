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
    assert leaf["python"] == sorted(checks.PYTHON_MAP["backend/task_loops.py"])
    # Scheduler.tick catches loop failures; its integration guard must stay selected.
    assert "backend/tests/test_scheduler.py" in leaf["python"]
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
    split, loads = checks.groups(counts, times, total=4)
    assert sorted(sum(split, [])) == sorted(counts)
    assert max(loads) <= 12 and len(split) == 4
    assert checks.groups(counts, times, total=4) == (split, loads)
    checks.groups(counts, {"a.py": float("nan"), "b.py": -1, "c.py": "text"})
    xml = tmp_path / "python-1.xml"
    xml.write_text('<testsuites><testsuite><testcase file="a.py" time="2.5"/>'
                   '<testcase file="a.py" time="1.5"/><testcase file="b.py" time="3"/>'
                   '</testsuite></testsuites>')
    assert checks.durations([xml]) == {"a.py": 4, "b.py": 3}


def test_plan_keeps_full_collection_on_main_and_conftest_and_never_runs_bare_pytest(tmp_path, monkeypatch):
    files = {"backend/tests/test_task_loops.py": 2, "backend/tests/test_auth.py": 10,
             "backend/tests/test_api.py": 30, "scripts/tests/test_release_checks.py": 6,
             "backend/tests/test_health.py": 1, "backend/tests/test_system_routines.py": 1,
             "backend/tests/test_scheduler.py": 1}
    monkeypatch.setattr(checks, "collect", lambda: Counter(files))
    guarded = []
    monkeypatch.setattr(checks, "guard_changelog", lambda base: guarded.append(base))
    monkeypatch.setattr(checks, "changed_files", lambda base: ["backend/task_loops.py"])
    target = tmp_path / "plan.json"
    timing_dir = tmp_path / "previous" / "pr-python-timings-python-1"
    timing_dir.mkdir(parents=True)
    (timing_dir / "durations.json").write_text(json.dumps({"backend/tests/test_task_loops.py": 100}))
    focused = checks.prepare("pull_request", "base", target, timing_dir.parent)
    assert guarded == ["base"]
    assert focused["timed_files"] == 1
    assert sorted(sum([j["files"] for j in focused["matrix"]["include"]], [])) == sorted(checks.PYTHON_MAP["backend/task_loops.py"])
    main = checks.prepare("push", "base", target)
    assert guarded == ["base"]  # A direct release cut on main is checked by the release guards.
    groups = [j for j in main["matrix"]["include"] if j["suite"].startswith("python-")]
    # 48 measured seconds fit one VM; the time target, not a fixed count, sets the number of groups.
    assert len(groups) == 1 and sorted(sum([j["files"] for j in groups], [])) == sorted(files)
    monkeypatch.setattr(checks, "changed_files", lambda base: ["conftest.py"])
    assert checks.prepare("pull_request", "base", target)["python"] is None
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(checks.subprocess, "run", lambda command, **kw: calls.append(command) or SimpleNamespace(returncode=1))
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


def test_rust_crates_run_cargo_test_when_their_inputs_change_and_a_red_crate_fails_the_job(tmp_path, monkeypatch):
    for changed, crates in ((["runner-rs/src/state.rs"], ["runner-rs"]), (["relay-rs/src/gateway.rs"], ["relay-rs"]),
                            (["runner/redact.py"], ["runner-rs"]), (["runner-rs/Cargo.lock", "relay-rs/Cargo.lock"],
                                                                    ["relay-rs", "runner-rs"]),
                            (["backend/tests/fixtures/gateway_enroll_contract.json"], ["runner-rs"])):
        for event in ("pull_request", "push"):
            plan = checks.selection(changed, event)
            assert plan["rust"] == crates and plan["conformance"], (changed, event)
    for changed in (["backend/harness_actions.py"], ["backend/task_loops.py"], ["ui/app/chat.js"], []):
        assert checks.selection(changed)["rust"] == [] and checks.selection(changed, "push")["rust"] == []
    assert checks.selection(["backend/harness_actions.py"])["conformance"]
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_task_loops.py": 1}))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(checks, "changed_files", lambda base: ["relay-rs/src/gateway.rs", "runner-rs/src/state.rs"])
    target = tmp_path / "plan.json"
    jobs = checks.prepare("pull_request", "base", target)["matrix"]["include"]
    assert {"suite": "conformance", "files": [checks.CONFORMANCE], "crates": ["relay-rs", "runner-rs"]} in jobs

    monkeypatch.setattr(checks, "ROOT", tmp_path)
    (tmp_path / "runner/tests").mkdir(parents=True)
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
    assert [c[:2] for c in calls[:2]] == [(["cargo", "test", "--locked"], tmp_path / "relay-rs"),
                                          (["cargo", "test", "--locked"], tmp_path / "runner-rs")]
    assert [c[2] for c in calls[:2]] == [str(tmp_path / "runner-rs/target/relay-rs"), str(tmp_path / "runner-rs/target")]
    assert calls[2][0][-1] == checks.CONFORMANCE and len(calls) == 3
    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="cargo is not installed"):
        checks.run(target, "conformance")


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


def test_docs_plan_has_an_explicit_no_test_job_and_never_calls_pytest(tmp_path, monkeypatch):
    # Exercise selector inputs independently of the separate release-notes guard.
    monkeypatch.setattr(checks, "guard_changelog", lambda _: None)
    monkeypatch.setattr(checks, "changed_files", lambda _: ["docs/removed.md", "CHANGELOG.md", "README.md"])
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


def test_test_leaves_use_imports_and_fixture_references_instead_of_filenames(tmp_path):
    leaf = "backend/tests/test_leaf.py"
    consumer = "backend/tests/test_consumer.py"
    source(tmp_path, leaf, "from pytest import fixture as fx\n@fx(name='shared')\ndef local(): return 1\ndef test_ok(shared): pass\n")
    source(tmp_path, consumer, "def test_other(): pass\n")
    assert checks.selection([leaf, "CHANGELOG.md"], root=tmp_path)["python"] == [leaf]
    for text in ("from .test_leaf import local", "import backend.tests.test_leaf as helper",
                 "from backend.tests import test_leaf", "import test_leaf",
                 "import importlib\nx = importlib.import_module('backend.tests.test_leaf')",
                 "import importlib\nx = importlib.import_module('.test_leaf', 'backend.tests')",
                 "from importlib import import_module as load\nx = load(variable)",
                 "pytest_plugins = ['backend.tests.test_leaf']", "pytest_plugins = ['test_leaf']",
                 "def test_uses(shared): pass",
                 "def test_uses(request): request.getfixturevalue('shared')",
                 "import pytest\n@pytest.mark.usefixtures('shared')\ndef test_uses(): pass",
                 "import pytest\n@pytest.mark.parametrize('shared', [1], indirect=True)\ndef test_uses(): pass",
                 "def test_uses(request, variable): request.getfixturevalue(variable)",
                 "import importlib\nx = importlib.import_module(variable)"):
        source(tmp_path, consumer, text + "\n")
        assert checks.selection([leaf], root=tmp_path)["python"] is None, text
    source(tmp_path, consumer, "import pytest\n@pytest.mark.parametrize('shared', [1])\ndef test_other(shared): pass\n")
    assert checks.selection([leaf], root=tmp_path)["python"] == [leaf]
    source(tmp_path, consumer, "def test_other(): pass\n")
    for text in ("def pytest_collection_modifyitems(items): pass", "pytest_plugins = ['helper']", "@unknown\ndef test_ok(): pass", "syntax error !"):
        source(tmp_path, leaf, text + "\n")
        assert checks.selection([leaf], root=tmp_path)["python"] is None
    source(tmp_path, "backend/tests/conftest.py", "")
    assert checks.selection(["backend/tests/conftest.py"], root=tmp_path)["python"] is None


def test_audited_backend_map_adds_direct_tests_but_rejects_new_runtime_consumers(tmp_path, monkeypatch):
    module, test = "backend/feature.py", "backend/tests/test_feature.py"
    monkeypatch.setitem(checks.PYTHON_MAP, module, [test])
    monkeypatch.setitem(checks.AUDITED_IMPORTERS, module, {"backend/known.py"})
    source(tmp_path, module, "value = 1\n")
    source(tmp_path, test, "from backend import feature\ndef test_feature(): pass\n")
    source(tmp_path, "backend/known.py", "from .feature import value\n")
    source(tmp_path, "backend/tests/test_other.py", "from backend.feature import value\n")
    assert checks.selection([module, test, "CHANGELOG.md"], root=tmp_path)["python"] == [test, "backend/tests/test_other.py"]
    source(tmp_path, "backend/tests/test_dependent.py", "from .test_other import value\n")
    assert "backend/tests/test_dependent.py" in checks.selection([module], root=tmp_path)["python"]
    source(tmp_path, "backend/new.py", "from . import feature\n")
    assert checks.selection([module], root=tmp_path)["python"] is None
    for hub in checks.SHARED_HUBS:
        source(tmp_path, hub, "")
        monkeypatch.setitem(checks.PYTHON_MAP, hub, [test])
        assert checks.selection([hub], root=tmp_path)["python"] is None
    (tmp_path / "backend/new.py").unlink()
    (tmp_path / test).unlink()
    assert checks.selection([module], root=tmp_path)["python"] is None


def test_removing_a_shared_fixture_does_not_make_its_module_a_leaf(tmp_path):
    leaf = "backend/tests/test_fixture.py"
    source(tmp_path, leaf, "def test_local(): pass\n")
    source(tmp_path, "backend/tests/test_consumer.py", "def test_consumer(shared): pass\n")
    graph = checks.SourceGraph(tmp_path)
    assert graph.test_leaf(leaf)
    assert not graph.test_leaf(leaf, "import pytest\n@pytest.fixture\ndef shared(): return 1\n")


def test_groups_are_sized_to_the_job_target_and_browser_scripts_split_by_measured_time(tmp_path, monkeypatch):
    # One VM holds WORKERS x (target - setup) seconds of one-worker test time; never 0 VMs, never past the cap.
    per_python = checks.WORKERS * (checks.JOB_SECONDS - checks.SETUP_SECONDS["python"])
    assert checks.job_count(0, "python") == 1
    assert checks.job_count(per_python, "python") == 1 and checks.job_count(per_python + 1, "python") == 2
    assert checks.job_count(10 ** 9, "python") == checks.MAX_JOBS
    counts = Counter({f"t{i}.py": 1 for i in range(30)})
    split, loads = checks.groups(counts, {name: 100 for name in counts})
    assert len(split) == checks.job_count(3000, "python") and max(loads) <= per_python
    assert sorted(sum(split, [])) == sorted(counts)

    names = [f"s{i}" for i in range(40)]
    listed = []
    monkeypatch.setattr(checks, "browser_scripts", lambda args: listed.append(args) or names)
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_api.py": 1}))
    monkeypatch.setattr(checks, "guard_changelog", lambda base: None)
    monkeypatch.setattr(checks, "changed_files", lambda base: ["ui/app/core.js"])
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
    assert len([j for j in plan["matrix"]["include"] if j["suite"].startswith("browser-")]) == \
        checks.job_count(40 * checks.UNTIMED_SCRIPT_SECONDS, "browser")

    # A browser job runs exactly its scripts at full width and records their times for the next split.
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(checks.subprocess, "run",
                        lambda command, **kw: calls.append((command, kw["env"])) or SimpleNamespace(returncode=0))
    (tmp_path / "plan.json").write_text(json.dumps({"matrix": {"include": [{"suite": "browser-1", "files": ["chat"]},
                                                                          {"suite": "browser-2", "files": []}]}}))
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
    assert "chat" in core and len(core) < len(checks.browser_scripts(["--all"]))
    assert checks.browser_scripts(["chat"]) == ["chat"]
