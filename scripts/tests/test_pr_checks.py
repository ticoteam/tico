"""CI selection cannot omit uncertain work or turn an empty/red test run green."""

from collections import Counter
from functools import partial
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace
import weakref

import pytest
import yaml

from backend import test_receipts
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
    main = checks.selection(["backend/task_engine.py"], "push")
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
        "backend/tests/test_task_engine.py": 2,
        "backend/tests/test_auth.py": 10,
        "backend/tests/test_api.py": 30,
        "scripts/tests/test_release_checks.py": 6,
        "backend/tests/test_health.py": 1,
        "backend/tests/test_system_routines.py": 1,
        "backend/tests/test_scheduler.py": 1,
    }
    monkeypatch.setattr(checks, "collect", lambda: Counter(files))
    imaged = []
    monkeypatch.setattr(checks, "image_checks", lambda changed: imaged.append(changed))
    monkeypatch.setattr(
        checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["backend/task_engine.py"]
    )
    target = tmp_path / "plan.json"
    timing_dir = tmp_path / "previous" / "pr-python-timings-python-1"
    timing_dir.mkdir(parents=True)
    (timing_dir / "durations.json").write_text(json.dumps({"backend/tests/test_task_engine.py": 100}))
    focused = checks.prepare("pull_request", "base", target, timing_dir.parent)
    assert imaged == [["backend/task_engine.py"]]
    assert focused["timed_files"] == 1
    assert (
        sorted(sum([j["files"] for j in focused["matrix"]["include"] if j["suite"].startswith("python-")], []))
        == sorted(set(checks.selection(["backend/task_engine.py"])["python"]) & set(files))
        == sorted(files)
    )
    main = checks.prepare("push", "base", target)
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
    focused["matrix"] = {"include": [{"suite": "python-1", "files": ["backend/tests/test_task_engine.py"]}]}
    target.write_text(json.dumps(focused))
    # Missing selected paths fail before subprocess starts, rather than widening to an implicit full run.
    with pytest.raises(RuntimeError, match="nonempty"):
        checks.run(target, "python-1")
    assert not calls
    (tmp_path / "backend/tests").mkdir(parents=True)
    (tmp_path / "backend/tests/test_task_engine.py").touch()
    assert checks.run(target, "python-1") == 1
    assert calls[-1][-1] == "backend/tests/test_task_engine.py"
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
    for changed in (["backend/harness_actions.py"], ["backend/task_engine.py"], ["ui/app/chat.ts"], []):
        assert checks.selection(changed)["rust"] == [] and checks.selection(changed, "push")["rust"] == []
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_task_engine.py": 1}))
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


def test_a_route_change_selects_the_contract_tests(repo_map):
    # The spec is generated from the code, so no committed spec file in a route's diff reaches the contract test any more.
    # The route's module reaches it through the app's imports, and the generator reaches it directly.
    for changed in ("backend/computers_routes.py", "backend/openapi_v2.py"):
        picked = checks.selection([changed], graph=repo_map)
        assert "backend/tests/test_openapi_v2.py" in picked["python"], changed
        # A light pull request narrows the plan (depth 1, the hub app.py is not followed): the contract test must stay.
        light = checks.narrow(picked, [changed], "pull_request", graph=repo_map)
        assert "backend/tests/test_openapi_v2.py" in light["python"], changed


# Release-only tests a change can break run before it merges (selection rules 6 and 7): #3301 taught a command in a
# playbook whose refusal was not recorded, which only the release gate's every-command CLI sweep compares; #3265 renamed a
# seat that a browser script outside CORE asserted, and only the release check runs those scripts.
CLI_SWEEP, TEMPLATES_SWEEP = "backend/tests/test_e2e_cli.py", "backend/tests/test_e2e_templates.py"


def test_a_content_change_turns_on_the_slow_sweep_that_reads_it_on_a_pull_request_and_in_the_queue(repo_map):
    for changed, sweeps in (
        (["templates/catalog/seo-owner/playbooks/find-ideas.md"], [CLI_SWEEP, TEMPLATES_SWEEP]),
        (["skills/tico-tickets/SKILL.md"], [CLI_SWEEP]),
        (["clients/cli_spec.py"], [CLI_SWEEP]),
        (["clients/commands/tasks.py"], [CLI_SWEEP]),
        (["e2e/cli/expected-refusals.json"], [CLI_SWEEP]),
        (["clients/catalog.py"], [TEMPLATES_SWEEP]),
        (["backend/health.py"], []),
        (["ui/app/tools.ts"], []),
    ):
        for event in ("pull_request", "merge_group"):
            picked = checks.narrow(checks.selection(changed, event, graph=repo_map), changed, event, graph=repo_map)
            assert picked["sweeps"] == sweeps, (changed, event)
            # A sweep runs in the contracts job; a path the contracts already follow keeps them as before.
            assert picked["contracts"] == (bool(sweeps) or changed[0].startswith(checks.CONTRACT_INPUTS)), changed


def test_the_contracts_job_carries_a_sweeps_environment_and_an_unswept_change_keeps_todays_job(tmp_path, monkeypatch):
    picked = {"python": [], "browser": [], "caddy": True, "conformance": False, "rust": [], "contracts": True}
    monkeypatch.setattr(checks, "changed_files", lambda *a, **kw: [])
    monkeypatch.setattr(checks, "collect", Counter)
    target = tmp_path / "plan.json"
    for sweeps, job in (
        ([], {"suite": "contracts", "files": list(checks.CONTRACT_TESTS)}),
        ([TEMPLATES_SWEEP], {"suite": "contracts", "files": [*checks.CONTRACT_TESTS, TEMPLATES_SWEEP]}),
        (
            [CLI_SWEEP, TEMPLATES_SWEEP],
            {
                "suite": "contracts",
                "files": [*checks.CONTRACT_TESTS, TEMPLATES_SWEEP],
                "env": {"TICO_E2E_CLI_ALL": "1"},
            },
        ),
    ):
        monkeypatch.setattr(checks, "selection", lambda *a, sweeps=sweeps, **kw: {**picked, "sweeps": sweeps})
        assert checks.prepare("pull_request", "base", target)["matrix"]["include"] == [job], sweeps
    # run() hands the job's environment to pytest; an incremental release plan's `sweeps` do the same for its lane.
    for name in [*checks.CONTRACT_TESTS, TEMPLATES_SWEEP]:
        source(tmp_path, name, "")
    monkeypatch.setattr(checks, "ROOT", tmp_path)
    monkeypatch.delenv("TICO_E2E_CLI_ALL", raising=False)
    envs = []
    monkeypatch.setattr(
        checks.subprocess, "run", lambda command, **kw: envs.append(kw.get("env")) or SimpleNamespace(returncode=1)
    )
    assert checks.run(target, "contracts") == 1
    increment = tmp_path / "increment.json"
    for sweeps in ([CLI_SWEEP], []):
        increment.write_text(
            json.dumps({"jobs": [{"name": "contracts", "files": list(checks.CONTRACT_TESTS)}], "sweeps": sweeps})
        )
        assert checks.run(increment, "contracts") == 1
    assert [(env or {}).get("TICO_E2E_CLI_ALL") for env in envs] == ["1", "1", None]


def test_a_ui_change_runs_the_release_only_browser_scripts_it_picks_before_it_merges(repo_map):
    every = set(checks.runnable_scripts(ROOT))
    assert len(every) > checks.BROWSER_ALL_OVER and "redesign-e2e-tools" in every

    def light(changed, event="pull_request"):
        return checks.narrow(checks.selection(changed, event, graph=repo_map), changed, event, graph=repo_map)

    for event in ("pull_request", "merge_group"):
        # A changed script runs itself.
        picked = light(["ui/tests/redesign-e2e-tools.cts"], event)
        assert picked["browser_direct"] == ["redesign-e2e-tools"] and "redesign-e2e-tools" in picked["browser"]
        # A helper runs every script that uses it, through other helpers: real-hub.cts <- redesign-e2e.cts <- the script.
        picked = light(["ui/tests/support/real-hub.cts"], event)
        assert {"redesign-e2e-tools", "real-hub-smoke"} <= set(picked["browser"]) < every
        # A helper most scripts use runs every script the release check runs.
        assert set(light(["ui/tests/support/page.cts"], event)["browser"]) >= every
        # UI source outside UI_MAP runs the scripts named after it, beside the smoke set.
        assert {"tools", "bot-tools", "redesign-e2e-tools"} <= set(light(["ui/app/tools.ts"], event)["browser_direct"])
        # A change outside ui/ picks none: its plan is what it was.
        assert light(["backend/health.py"], event)["browser_direct"] == []
    assert light(["backend/health.py"])["browser"] == []


def test_rule_6_follows_helpers_matches_names_and_runs_every_script_past_the_limit(tmp_path):
    smoke_tree(tmp_path)
    source(tmp_path, "ui/tests/a.cts", "const x = require('./support/x.cts');\n")
    source(tmp_path, "ui/tests/b.cts", "const p = require('./support/p.cts');\n")
    source(tmp_path, "ui/tests/support/x.cts", "const y = require('./y.cts');\nconst gone = require('./gone.cts');\n")
    source(tmp_path, "ui/tests/support/y.cts", "spawn(PYTHON, ['ui/tests/support/hub_server.py']);\n")
    source(tmp_path, "ui/tests/support/hub_server.py", "")
    source(tmp_path, "ui/tests/support/p.cts", "")
    files = checks.git_files(tmp_path)
    for helper in ("x.cts", "y.cts", "hub_server.py", "gone.cts"):
        assert checks.support_scripts(tmp_path, files, "ui/tests/support/" + helper) == {"a"}, helper
    scripts = ["settings", "settings-bots-search-bulk", "bot-settings", "tools", "redesign-e2e-tools"]
    assert checks.named_after("ui/app/settings-bots.ts", scripts) == {"settings-bots-search-bulk"}
    assert checks.named_after("ui/styles/tools.css", scripts) == {"tools", "redesign-e2e-tools"}
    assert checks.named_after("ui/index.html", scripts) == set()
    for name in range(checks.BROWSER_ALL_OVER + 1):
        source(tmp_path, f"ui/tests/s{name}.cts", "")
    changed = [f"ui/tests/s{name}.cts" for name in range(checks.BROWSER_ALL_OVER)]
    picked = checks.selection(changed, root=tmp_path, core=[])
    assert set(picked["browser_direct"]) == {Path(n).stem for n in changed} and "a" not in picked["browser"]
    picked = checks.selection([*changed, f"ui/tests/s{checks.BROWSER_ALL_OVER}.cts"], root=tmp_path, core=[])
    assert {"a", "b"} <= set(picked["browser"]) and any("rule 6" in u for u in picked["uncertain"])


def test_rule_6_runs_the_scripts_that_name_a_ui_function_the_diff_changes(tmp_path):
    # Nov 12: #3485 changed taskModalOpen in ui/app/task-modal.ts; task-code.cts calls it but was not selected, and the
    # release check went red. A script that names a top-level function or const whose definition the diff changes runs;
    # one that names only an unchanged global of the same file does not.
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=tmp_path, text=True
        ).strip()

    git("init", "-q", "-b", "main")
    smoke_tree(tmp_path)
    modal = (ROOT / "ui/app/task-modal.ts").read_text()
    source(tmp_path, "ui/app/task-modal.ts", modal)
    source(tmp_path, "ui/tests/task-code.cts", (ROOT / "ui/tests/task-code.cts").read_text())
    source(tmp_path, "ui/tests/other.cts", "await page.evaluate(() => taskModalLoadFailed('t', null));\n")
    git("add", "-A")
    git("commit", "-qm", "Start")
    base = git("rev-parse", "HEAD")

    def change(old, new):
        text = (tmp_path / "ui/app/task-modal.ts").read_text()
        assert old in text
        source(tmp_path, "ui/app/task-modal.ts", text.replace(old, new, 1))
        git("commit", "-qam", "Change")
        return checks.selection(["ui/app/task-modal.ts"], root=tmp_path, base=base, head="HEAD", core=[])

    start = modal.index("async function taskModalOpen(")
    body = modal.index("\n", start) + 1
    line = modal[body : modal.index("\n", body) + 1]
    picked = change(line, line + "  void 0;\n")  # one line inside taskModalOpen
    assert "task-code" in picked["browser_direct"] and "task-code" in picked["browser"]
    assert "other" not in picked["browser_direct"]
    assert checks.changed_definitions(base, "HEAD", ["ui/app/task-modal.ts"], tmp_path) == {
        "ui/app/task-modal.ts": {"taskModalOpen"}
    }
    git("reset", "-q", "--hard", base)
    picked = change("function taskModalLoadFailed(", "function taskModalLoadFailed(/* changed */ ")
    assert "other" in picked["browser_direct"] and "task-code" not in picked["browser_direct"]
    # A deleted UI file names its old functions: a script still calling one runs.
    git("reset", "-q", "--hard", base)
    git("rm", "-q", "ui/app/task-modal.ts")
    git("commit", "-qm", "Delete")
    picked = checks.selection(["ui/app/task-modal.ts"], root=tmp_path, base=base, head="HEAD", core=[])
    assert {"task-code", "other"} <= set(picked["browser_direct"])
    # Without a base (no diff to read), the rule picks nothing new.
    assert "task-code" not in checks.selection(["ui/app/task-modal.ts"], root=tmp_path, core=[])["browser_direct"]


def test_changed_definitions_maps_added_and_removed_lines_to_their_top_level_names():
    old = "const A = 1;\n\nfunction b() {\n  return A;\n}\n// note\nlet c = 2;\n"
    new = "const A = 1;\n\nfunction b() {\n  return A + 1;\n}\n// note changed\nlet c = 2;\nasync function d() {}\n"
    spans = checks.top_level_spans(new)
    assert [s[0] for s in spans] == ["A", "b", "c", "d"]
    diff = "@@ -4 +4 @@\n-  return A;\n+  return A + 1;\n@@ -6 +6 @@\n-// note\n+// note changed\n@@ -7,0 +8 @@\n+x\n"
    assert checks.hunk_names(diff, old, new) == {"b", "d"}
    # A removed function is named by the old side.
    assert checks.hunk_names("@@ -3,3 +2,0 @@\n-x\n-y\n-z\n", old, "const A = 1;\n\n// note\nlet c = 2;\n") == {"b"}


PHONE_SCRIPTS = {
    f"phone-dead-ends-{state}" for state in ("bot-waiting", "no-computer", "no-credit", "setup-halfway", "sign-off")
}


def test_a_ui_app_or_style_change_runs_the_phone_dead_end_scripts_and_a_backend_change_does_not(tmp_path):
    # A fix in ui/app/ or ui/styles/ removes a KNOWN phone break, and its dead-end script fails until the line goes: such a
    # change runs all five scripts, and so does their rules file. A backend change runs none of them.
    smoke_tree(tmp_path)
    source(tmp_path, "ui/styles/x.css", ".x { color: red; }\n")
    source(tmp_path, "ui/app/x.ts", "export const x = 1;\n")
    source(tmp_path, "ui/tests/support/phone-rules.cts", "// the phone rules\n")
    for name in PHONE_SCRIPTS:
        source(tmp_path, f"ui/tests/{name}.cts", "require('./support/phone-rules.cts');\n")
    source(tmp_path, "backend/health.py", "")
    for changed in (["ui/styles/x.css"], ["ui/app/x.ts"], ["ui/tests/support/phone-rules.cts"]):
        plan = checks.selection(changed, root=tmp_path, core=[])
        assert PHONE_SCRIPTS <= set(plan["browser_direct"]) and PHONE_SCRIPTS <= set(plan["browser"]), changed
    assert not PHONE_SCRIPTS & set(checks.selection(["backend/health.py"], root=tmp_path, core=[])["browser"])


def test_docs_plan_has_an_explicit_no_test_job_and_never_calls_pytest(tmp_path, monkeypatch, mapped):
    # CHANGELOG.md and docs/openapi/v2.json are gone; pytest.ini is a tracked file a test reads.
    for read, reader in (("pytest.ini", "backend/tests/test_ddl_guard.py"),):
        both = checks.selection(["backend/task_engine.py", read])["python"]
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
    # task_engine; the shared helper reaches the Caddy container test through caddy_assets.
    # test_ddl_guard scans all of backend/ (it skips tests at run time, which the map cannot see), so it is the twentieth;
    # test_schema_every_table reads every table the source makes in the same way, so it is the twenty-first;
    # test_system_jobs_list scans all of backend/ for recorded job keys, so it is one more.
    # test_entities_visible_lint scans all of backend/ for reads of the Directory's tables, so it is one more.
    # Script tests that import pr_checks.py (release_train, release_increment, release_notes, release_workflow,
    # hosted_release_check, main_red) or name a script that runs it (check_conflicts' hook stub), and the three
    # baseline test modules the OWNED_DATA table names by path, run for any test file change too: about 36 files.
    # The bound leaves room for a few more such script tests, so adding one doesn't fail every queue group behind it;
    # it is still a small share of the suite's ~1,500 test files.
    alone = checks.selection(["backend/tests/test_task_engine.py"])["python"]
    assert "backend/tests/test_task_engine.py" in alone and len(alone) < 50
    loops = checks.selection(["backend/task_engine.py"])
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


def test_a_dotted_name_in_typescript_is_a_variable_not_a_bare_python_module(tmp_path):
    # `run.closest` in a UI file is a JavaScript variable. Read as `import run` (a sys.path module, since a script
    # imports run bare), it ran every run.py; #3335's e2e/website-tools/run.py (since renamed) reached the conftest, so every UI file
    # selected the whole suite. A shell script naming `python -m run.main` still runs it.
    source(tmp_path, "e2e/website-tools/run.py", "def main(): pass\n")
    source(tmp_path, "e2e/website-tools/practice.py", "import run\nrun.main()\n")
    source(tmp_path, "ui/app/page.ts", "const run = document.body;\nrun.closest('x');\n")
    source(tmp_path, "scripts/go.sh", "python -m run.main\n")
    graph = checks.DependencyMap(tmp_path)
    assert "run" in graph.bare
    assert "e2e/website-tools/run.py" not in graph.runs["ui/app/page.ts"]
    assert "e2e/website-tools/run.py" in graph.runs["scripts/go.sh"]


def test_docs_and_workflow_edits_run_the_tests_that_read_them_not_the_suite(tmp_path, mapped):
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
        "scripts/tests/test_release_workflow.py",
        "from pathlib import Path\nROOT = Path(__file__).parents[2]\n"
        "def test_x(): (ROOT / '.github' / 'workflows' / 'release.yml').read_text()\n",
    )
    for name in ("docs/api.md", "docs/other.md", ".github/workflows/release.yml", checks.PR_WORKFLOW):
        source(tmp_path, name, "text\n")

    def picked(*changed):
        return checks.selection(list(changed), root=tmp_path, core=[], added=[])["python"]

    assert picked("docs/other.md") == ["backend/tests/test_manual.py"]
    assert picked("docs/api.md") == ["backend/tests/test_api_guide.py", "backend/tests/test_manual.py"]
    # A top-level page that no test or script reads selects nothing, deleted or not.
    assert picked("CHANGELOG.md") == []
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
    assert checks.selection(["CHANGELOG.md"], added=[])["python"] == []
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
    # One VM holds WORKERS x (target - setup) seconds of one-worker test time, so it is never planned past the target;
    # never 0 VMs, never past the cap.
    per_python = checks.WORKERS * (checks.JOB_SECONDS - checks.SETUP_SECONDS["python"])
    assert checks.job_count(0, "python") == 1
    assert checks.job_count(per_python, "python") == 1 and checks.job_count(per_python + 1, "python") == 2
    assert checks.job_count(2 * per_python, "python") == 2
    assert checks.job_count(10**9, "python") == checks.MAX_JOBS
    # The recorded suite (Nov 9: about 25,800 one-worker seconds in 1,050 files) splits into VMs of about
    # TICO_CI_JOB_SECONDS each, setup included: 36 of them, under MAX_JOBS.
    suite = Counter({f"backend/tests/test_{i}.py": 1 for i in range(936)})
    split, loads = checks.groups(suite, {name: 25800 / 936 for name in suite})
    # Files are whole, so a VM can run one file past its target (a file's worth of about 30 s on each worker).
    assert len(split) == checks.job_count(25800, "python") == 36
    assert max(loads) / checks.WORKERS + checks.SETUP_SECONDS["python"] <= checks.JOB_SECONDS + 30
    # A small selection still takes one VM.
    assert len(checks.groups(Counter({"a.py": 3}), {"a.py": 200})[0]) == 1
    counts = Counter({f"t{i}.py": 1 for i in range(30)})
    split, loads = checks.groups(counts, {name: 100 for name in counts})
    assert len(split) == checks.job_count(3000, "python")
    assert max(loads) / checks.WORKERS + checks.SETUP_SECONDS["python"] <= checks.JOB_SECONDS
    assert sorted(sum(split, [])) == sorted(counts)

    names = [f"s{i}" for i in range(40)]
    listed = []
    monkeypatch.setattr(checks, "browser_scripts", lambda args, root=None: listed.append(args) or names)
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_api.py": 1}))
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
    assert command == ["node", "scripts/ui-tests.cts", "-j", str(checks.WORKERS), "chat"]
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
    (tmp_path / "start.py").write_text("x = 0\n")
    git("add", "-A")
    git("commit", "-qm", "Start")
    stale = git("rev-parse", "HEAD")
    git("checkout", "-qb", "feature")
    (tmp_path / "app.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "Product change")
    git("checkout", "-q", "main")
    (tmp_path / "other.py").write_text("y = 2\n")
    git("add", "-A")
    git("commit", "-qm", "Other change on main")
    main_tip = git("rev-parse", "HEAD")
    # GitHub checks out the PR merged into current main; the event's base sha is still the older tip.
    git("merge", "-q", "--no-edit", "feature")
    # changed_files, script_changes and selection bind ROOT at import, so they get this checkout as their root.
    # merge_parent gets it too; prepare and the diffs it runs are otherwise unchanged.
    monkeypatch.setattr(checks, "merge_parent", partial(checks.merge_parent, root=tmp_path))
    monkeypatch.setattr(checks, "changed_files", partial(checks.changed_files, root=tmp_path))
    monkeypatch.setattr(checks, "script_changes", partial(checks.script_changes, root=tmp_path))
    monkeypatch.setattr(checks, "selection", partial(checks.selection, root=tmp_path, core=[]))
    # image_checks needs the Dockerfile of a real checkout; this temporary repo has none.
    monkeypatch.setattr(checks, "image_checks", lambda changed, root=None: None)
    monkeypatch.setattr(checks, "collect", lambda: Counter({"scripts/tests/test_pr_checks.py": 1}))
    assert checks.merge_parent(stale) == main_tip
    plan = checks.prepare("pull_request", stale, tmp_path / "plan.json")
    assert plan["changed"] == ["app.py"]


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
            "pinned-tools-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('scripts/pinned_tool.sh') }}",
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
    assert sum(venv in str(job) for job in pr["jobs"].values()) == 4  # plan, tests, static, caddy-linux
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
    for files in (["backend/task_engine.py"], ["conftest.py"], ["ui/app/chat.ts"], [checks.PR_WORKFLOW]):
        pr, queue = checks.selection(files, "pull_request"), checks.selection(files, "merge_group")
        assert pr == queue, files


def test_a_merge_group_plans_its_own_selection_and_never_runs_the_pull_request_gates(tmp_path, monkeypatch, mapped):
    files = {"backend/tests/test_task_engine.py": 2, "backend/tests/test_auth.py": 10}
    monkeypatch.setattr(checks, "collect", lambda: Counter(files))

    def refuse(*args, **kwargs):
        raise AssertionError("a merge queue group is checked from its own base, not from a pull request's merge parent")

    monkeypatch.setattr(checks, "merge_parent", refuse)
    monkeypatch.setattr(checks, "image_checks", lambda changed: None)
    seen = []

    def fake_changed(base, head="HEAD", only=None):
        seen.append((base, head, only))
        return [] if only else ["backend/task_engine.py"]

    monkeypatch.setattr(checks, "changed_files", fake_changed)
    target = tmp_path / "plan.json"
    plan = checks.prepare("merge_group", "queue-base", target, head="queue-head")
    assert seen == [("queue-base", "queue-head", None), ("queue-base", "queue-head", "A")]
    assert plan["reason"].startswith("measured dependency map") and plan["python"] is not None
    expected = sorted(set(checks.selection(["backend/task_engine.py"])["python"]) & set(files))
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
    for name in ("tests", "rust-lint", "desktop-app", "recorder", "caddy-linux"):
        assert ready in jobs[name]["if"], name
    for name, output in (
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
        "checks",
        "rust-lint",
        "desktop-app",
        "recorder",
        "caddy-linux",
        "timings",
    }


def test_a_draft_starts_no_job_and_marking_it_ready_or_pushing_to_a_ready_pull_request_runs_the_checks():
    """Builders push a draft freely: no job runs on it. Marking it ready runs the pull request jobs, and so does each
    later push while it stays ready. `checks` is skipped on a draft, and GitHub never enqueues a draft."""
    import yaml

    doc = workflow_jobs()
    assert {"ready_for_review", "synchronize", "opened", "reopened"} <= set(doc[True]["pull_request"]["types"])
    for name, job in doc["jobs"].items():
        condition = str(job.get("if", ""))
        queue_only = "github.event_name == 'merge_group'" in condition and "pull_request" not in condition
        assert queue_only or "!github.event.pull_request.draft" in condition, name
    # On a ready pull request nothing but the draft flag holds back the plan, the static checks and the gate. The static
    # checks run on pull requests only: the merge queue runs them in the plan's merge commit checks.
    assert doc["jobs"]["plan"]["if"] == "${{ !github.event.pull_request.draft }}"
    assert (
        doc["jobs"]["static"]["if"] == "${{ github.event_name == 'pull_request' && !github.event.pull_request.draft }}"
    )
    assert doc["jobs"]["checks"]["if"] == "${{ always() && !github.event.pull_request.draft }}"
    # Every other workflow that runs on a pull request holds its jobs back on a draft too (a push to main still runs).
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        other = yaml.safe_load(path.read_text())
        triggers = other.get(True) or other.get("on") or {}
        if path.name == "pr-checks.yml" or "pull_request" not in triggers:
            continue
        assert "ready_for_review" in triggers["pull_request"].get("types", []), path.name
        for name, job in other["jobs"].items():
            condition = str(job.get("if", ""))
            assert "!github.event.pull_request.draft" in condition, f"{path.name}:{name}"


def test_a_failed_suite_leg_cancels_its_run_and_checks_still_reports():
    jobs = workflow_jobs()["jobs"]
    job = jobs["tests"]
    assert job["permissions"] == {"contents": "read", "actions": "write"}
    cancel = job["steps"][-1]
    # It names what failed first (every leg then ends "cancelled"), and the cancel is its last line.
    lines = cancel["run"].strip().splitlines()
    assert cancel["if"] == "failure()" and lines[-1] == 'gh run cancel "$GITHUB_RUN_ID"'
    assert any(line.startswith('echo "::error title=') for line in lines)
    env = {k: v for k, v in cancel["env"].items() if k != "SUITE"}
    assert env == {"GH_TOKEN": "${{ github.token }}", "GH_REPO": "${{ github.repository }}"}
    # Only the tests leg and the plan (which cancels superseded queue groups) may cancel; every other job keeps the
    # workflow's read-only token.
    assert {name for name, job in jobs.items() if "permissions" in job} == {"tests", "plan"}
    # The required gate still runs after a cancel, and a cancelled timing receipt never waits for a runner.
    assert "always()" in jobs["checks"]["if"] and "tests" in jobs["checks"]["needs"]
    assert "!cancelled()" in jobs["timings"]["if"]


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


def test_checks_is_the_one_gate_and_fails_on_a_failed_or_cancelled_need_while_skipped_passes(tmp_path):
    gate = workflow_jobs()["jobs"]["checks"]
    assert gate["name"] == "checks" and "always()" in gate["if"]
    needs = set(gate["needs"])
    assert needs == set(workflow_jobs()["jobs"]) - {"checks", "timings"}
    env = gate["steps"][0]["env"]
    names = {
        key: found.group(1) for key, value in env.items() if (found := re.search(r"needs\.([\w-]+)\.result", value))
    }
    assert set(names.values()) == needs
    # A cancelled need makes the gate ask the run's jobs which step failed; a stub gh answers for the API.
    (tmp_path / "gh").write_text("#!/bin/sh\necho 'python-6: Run selected tests'\n")
    (tmp_path / "gh").chmod(0o755)

    def gate_run(results):
        run_env = {
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            **{key: results[name] for key, name in names.items()},
        }
        return subprocess.run(
            ["bash", "-e", "-c", gate["steps"][0]["run"]], env=run_env, capture_output=True, text=True
        )

    def exit_code(results):
        return gate_run(results).returncode

    passing = dict.fromkeys(needs, "success")
    assert exit_code(passing) == 0
    assert exit_code({**passing, "tests": "skipped", "recorder": "skipped", "caddy-linux": "skipped"}) == 0
    assert exit_code({**passing, "tests": "failure"}) == 1
    assert exit_code({**passing, "caddy-linux": "cancelled"}) == 1
    assert exit_code({**passing, "plan": "skipped"}) == 0
    cancelled = gate_run({**passing, "tests": "cancelled"})
    assert cancelled.returncode == 1 and "::error::Failed: python-6: Run selected tests." in cancelled.stdout


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
        ["backend/task_engine.py"],
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


@pytest.mark.parametrize("seconds", [1, 959, 5000])
def test_no_planned_python_vm_runs_past_the_target_however_small_the_selection(seconds):
    # 959 one-worker seconds once folded into one VM planned at 390 s; a VM holds WORKERS x (target - setup) = 720 of
    # them, so the selection takes two VMs of about 270 s, and a selection under one VM's work takes one.
    counts = Counter({f"t{i}.py": 1 for i in range(seconds)})  # one-second files: the packing is exact
    split, loads = checks.groups(counts, {name: 1.0 for name in counts})
    assert len(split) == checks.job_count(seconds, "python") == max(1, math.ceil(seconds / 720))
    assert max(loads) / checks.WORKERS + checks.SETUP_SECONDS["python"] <= checks.JOB_SECONDS
    assert checks.overrun_warnings(loads, checks.SETUP_SECONDS["python"], checks.JOB_SECONDS, "Python") == []


def test_a_vm_planned_past_the_target_gets_a_warning():
    assert checks.overrun_warnings([720.0], checks.SETUP_SECONDS["python"], checks.JOB_SECONDS, "Python") == []
    lines = checks.overrun_warnings([960.0], checks.SETUP_SECONDS["python"], checks.JOB_SECONDS, "Python")
    assert len(lines) == 1 and lines[0].startswith("::warning::A Python VM is planned for 390s")
    # 480 one-worker seconds are 240 s of tests on the planner's four workers (the incremental browser VMs run -j 4 too).
    assert not checks.overrun_warnings([480.0], checks.SETUP_SECONDS["browser"], checks.JOB_SECONDS, "browser")
    assert checks.overrun_warnings([1000.0], checks.SETUP_SECONDS["browser"], checks.JOB_SECONDS, "browser")


def test_size_groups_plans_at_the_target_and_only_stretches_toward_an_explicit_cap():
    counts = Counter({f"backend/tests/test_{i}.py": 1 for i in range(346)})
    timings = {name: 6720 / 346 for name in counts}
    # The planner's cap is its target, so a ceiling never stretches a VM: the job count is reported, not forced down.
    assert checks.MAX_JOB_SECONDS == checks.JOB_SECONDS
    sized = checks.size_groups(counts, timings, fixed=2, ceiling=8)
    assert sized["target"] == checks.JOB_SECONDS and sized["tests"] == len(sized["split"]) + 2
    # A call that names a higher cap still raises the target toward it until the count fits the ceiling.
    stretched = checks.size_groups(counts, timings, fixed=2, ceiling=8, cap=480)
    assert checks.JOB_SECONDS < stretched["target"] <= 480 and stretched["tests"] <= 8
    # Already under the ceiling, the target stays at the usual one.
    small = checks.size_groups(Counter({"a.py": 1}), {"a.py": 100}, fixed=1, ceiling=8)
    assert small["target"] == checks.JOB_SECONDS and small["tests"] == 2
    # Past the cap the plan keeps the cap's VMs and reports the count it has.
    big = Counter({f"t{i}.py": 1 for i in range(1000)})
    capped = checks.size_groups(big, {name: 100 for name in big}, ceiling=8)
    assert capped["target"] == checks.MAX_JOB_SECONDS and capped["tests"] > 8


def test_a_full_queue_group_packs_at_the_target_and_a_narrowed_one_stays_under_the_ceiling():
    """Every VM is planned at the target (330 s, setup included), queue groups too. The recorded full suite (about 25,800
    one-worker seconds) takes about 36 Python VMs; a narrowed queue group (NARROW) takes at most its shard cap."""
    assert checks.CEILING["merge_group"] == 30 and checks.QUEUE_MAX_JOB_SECONDS == checks.JOB_SECONDS
    counts = Counter({f"backend/tests/test_{i}.py": 1 for i in range(930)})
    timings = {name: 25800 / 930 for name in counts}
    names = [f"s{i}" for i in range(32)]
    browser = (Counter(dict.fromkeys(names, 1)), dict.fromkeys(names, 50))
    queue = checks.size_groups(
        counts,
        timings,
        browser=browser,
        fixed=1,
        ceiling=checks.CEILING["merge_group"],
        cap=checks.QUEUE_MAX_JOB_SECONDS,
    )
    assert queue["target"] == checks.JOB_SECONDS and len(queue["split"]) == 36
    assert max(queue["loads"]) / checks.WORKERS + checks.SETUP_SECONDS["python"] <= checks.JOB_SECONDS + 30
    assert queue["tests"] == 36 + checks.job_count(1600, "browser") + 1
    # A small group stays at the usual target in one VM: 20 files of 90 s is one VM.
    small = Counter({f"t{i}.py": 1 for i in range(20)})
    sized = checks.size_groups(
        small, {n: 30 for n in small}, ceiling=checks.CEILING["merge_group"], cap=checks.QUEUE_MAX_JOB_SECONDS
    )
    assert sized["target"] == checks.JOB_SECONDS and len(sized["split"]) == 1


def test_queue_jobs_start_only_for_a_change_to_their_folders_and_all_start_for_an_unreadable_diff(mapped):
    assert checks.queue_jobs(["backend/task_engine.py", "ui/app/chat.ts"]) == dict.fromkeys(
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
    # esbuild builds the recorder's window: the root npm files run its job, a package.json deeper in the tree does not.
    for name in ("package.json", "package-lock.json"):
        assert checks.queue_jobs([name]) == {"rust_lint": False, "desktop": False, "recorder": True}, name
    assert not checks.queue_jobs(["ui/vendor/x/package.json"])["recorder"]
    # Anchored at the root: a folder named app/ or recorder/ deeper in the tree is not the desktop app or the recorder.
    assert checks.queue_jobs(
        ["backend/app/x.py", "docs/recorder/x.md", "scripts/tests/rust_checks.sh"]
    ) == dict.fromkeys(checks.QUEUE_JOB_PATHS, False)
    # A diff that could not be read (or is empty) never skips a job.
    assert checks.queue_jobs([]) == dict.fromkeys(checks.QUEUE_JOB_PATHS, True)


def test_the_plan_decides_the_queue_jobs_on_both_tiers_and_a_group_checks_its_merge_commit_without_a_static_vm(
    tmp_path, monkeypatch, mapped
):
    monkeypatch.setattr(checks, "collect", lambda: Counter({"backend/tests/test_task_engine.py": 2}))
    monkeypatch.setattr(checks, "image_checks", lambda changed: None)
    monkeypatch.setattr(
        checks,
        "changed_files",
        lambda base, head="HEAD", only=None: [] if only else ["app/src/main.rs", "backend/task_engine.py"],
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


def test_only_a_group_the_queue_already_dropped_is_cancelled():
    """A cancelled group that is still live fails `checks` and ejects its pull request (#3380), so the cancel step
    cancels a group only once GitHub has deleted its gh-readonly-queue ref; any other answer keeps it."""

    def answer(returncode, stderr):
        calls = []

        def run(args, **_):
            calls.append(args)
            return subprocess.CompletedProcess(args, returncode, "", stderr)

        return run, calls

    branch = "gh-readonly-queue/main/pr-3380-4f5617e0"
    run, calls = answer(1, "gh: Not Found (HTTP 404)\n")
    assert checks.queue_ref_dropped("o/r", branch, run=run)
    assert calls == [["gh", "api", f"repos/o/r/git/ref/heads/{branch}", "--silent"]]
    assert not checks.queue_ref_dropped("o/r", branch, run=answer(0, "")[0])  # still the live entry
    assert not checks.queue_ref_dropped("o/r", branch, run=answer(1, "gh: Server Error (HTTP 502)\n")[0])
    run, calls = answer(1, "gh: Not Found (HTTP 404)\n")
    assert not checks.queue_ref_dropped("o/r", "main", run=run) and calls == []


def test_checks_names_the_step_that_failed_when_a_leg_ended_cancelled():
    step = workflow_jobs()["jobs"]["checks"]["steps"][0]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert 'select(.conclusion == "failure")' in step["run"] and "cancelled from outside" in step["run"]
    assert step["run"].rstrip().endswith('exit "$status"')


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


# `scripts/pr_checks.py local`: a builder runs the tests the PR checks would select, on its own computer, before it marks
# a draft ready. Its last line is a receipt the review gate takes for that exact head (backend/test_receipts.py). These
# live here, not in a file of their own: a test file that imports pr_checks runs on every test file change.


LOCAL_PLAN = {
    "python": ["tests/test_app.py"],
    "browser": ["chat"],
    "caddy": False,
    "contracts": False,
    "conformance": False,
    "rust": [],
    "reason": "measured dependency map",
    "uncertain": [],
}


def local_git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=root, text=True
    ).strip()


@pytest.fixture
def branch(tmp_path, monkeypatch):
    """A branch with one change on top of main, the selection fixed, and every command recorded instead of run."""
    local_git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("x = 1\n")
    local_git(tmp_path, "add", "-A")
    local_git(tmp_path, "commit", "-qm", "Start")
    local_git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "app.py").write_text("x = 2\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass\n")
    local_git(tmp_path, "add", "-A")
    local_git(tmp_path, "commit", "-qm", "Change")
    seen = {}

    def select(changed, event="pull_request", **kwargs):
        seen.update(changed=changed, event=event, base=kwargs.get("base"))
        return dict(seen.get("plan", LOCAL_PLAN))

    monkeypatch.setattr(checks, "selection", select)
    monkeypatch.setattr(checks, "browser_scripts", lambda args, root=None: list(args))
    monkeypatch.delenv("TICO_HEAVY_REMOTE", raising=False)
    commands = []
    answers = {}
    during = {}  # what happens in the checkout while a command runs
    envs = []

    def execute(command, **kwargs):
        commands.append(command)
        envs.append(kwargs.get("env") or {})
        for key, act in during.items():
            if key in " ".join(command):
                act()
        key = next((k for k in answers if k in " ".join(command)), None)
        answer = answers.get(key, (0, ""))
        # A list answers each call in turn, its last item from then on.
        code, out = (answer.pop(0) if len(answer) > 1 else answer[0]) if isinstance(answer, list) else answer
        return SimpleNamespace(returncode=code, stdout=out, stderr="")

    return SimpleNamespace(
        root=tmp_path,
        head=local_git(tmp_path, "rev-parse", "HEAD"),
        seen=seen,
        commands=commands,
        answers=answers,
        during=during,
        envs=envs,
        run=lambda **kw: checks.local(base="main", root=tmp_path, execute=execute, **kw),
    )


def pytest_leg(command):
    """The pytest command and the report check of a Python leg, as heavy.sh is given them: bash -c '<pytest>; ...'."""
    script = command[command.index("-c") + 1]
    first, rest = script.split("; code=$?;", 1)
    return shlex.split(first), shlex.split(rest.split("exec ", 1)[1].split(";", 1)[0])


def receipt_line(out):
    return out.strip().splitlines()[-1]


def gate(line, head):
    """What the review gate reads from this line posted on the owner's ticket."""
    task = {"id": "t1", "owner_id": "bot:builder", "messages": [{"id": "m1", "sender_id": "bot:builder", "body": line}]}
    return test_receipts.at_head(task, head)[0]


def test_a_local_pass_runs_the_ci_selection_and_prints_a_receipt_the_review_gate_takes_at_that_head(branch, capsys):
    assert branch.run() == 0
    # The same selection a pull request's plan makes: the branch's own change, as a pull request event.
    assert branch.seen["changed"] == ["app.py", "tests/test_app.py"] and branch.seen["event"] == "pull_request"
    python, browser = branch.commands
    assert python[2:4] == ["bash", "-c"]
    test, check = pytest_leg(python)
    assert test[-1] == "tests/test_app.py" and "pytest" in test and test[test.index("-n") + 1] == "2"
    assert check[1:3] == ["scripts/pr_checks.py", "junit-ran"]  # a leg whose every test skipped is no pass
    assert "scripts/ui-tests.cts" in browser and browser[-1] == "chat"
    # Heavy local runs share the computer's slots (scripts/heavy.sh), as a bot's full suite does.
    assert all(c[1].endswith("scripts/heavy.sh") for c in branch.commands)
    line = receipt_line(capsys.readouterr().out)
    assert len(branch.head) == 40 and branch.head in line
    assert test_receipts.recognise(line) == {"head": branch.head, "status": "passed", "text": line}
    assert gate(line, branch.head) == "passed"
    assert gate(line, "f" * 40) == "stale"  # a later push needs a new receipt


def test_a_local_failure_stops_and_prints_a_failed_receipt_that_blocks_the_head(branch, capsys):
    branch.answers["pytest"] = (1, "")
    assert branch.run() == 1
    assert len(branch.commands) == 1  # the browser scripts never start after a red Python run
    line = receipt_line(capsys.readouterr().out)
    assert test_receipts.recognise(line)["status"] == "failed" and gate(line, branch.head) == "failed"


def test_a_leg_that_collects_nothing_fails_like_ci(branch, capsys):
    # pytest exits 5 when it runs no test, such as every module skipping at import; CI's run counts that as red.
    branch.answers["pytest"] = (5, "")
    assert branch.run() == 1
    line = receipt_line(capsys.readouterr().out)
    assert gate(line, branch.head) == "failed"


def test_uncommitted_changes_get_no_receipt_and_run_nothing(branch, capsys):
    (branch.root / "app.py").write_text("x = 3\n")
    assert branch.run() == 2
    out = capsys.readouterr()
    assert branch.commands == [] and branch.head not in out.out + out.err
    assert not test_receipts.SHA.search(out.out)


def test_an_untracked_file_gets_no_receipt_and_runs_nothing(branch, capsys):
    # An uncommitted module or fixture could make the tests pass where the named commit fails, whatever git status
    # is set to show.
    local_git(branch.root, "config", "status.showUntrackedFiles", "no")
    (branch.root / "tests" / "helper.py").write_text("x = 1\n")
    assert branch.run() == 2
    assert branch.commands == [] and not test_receipts.SHA.search(capsys.readouterr().out)


def test_a_commit_or_edit_during_the_run_gets_no_receipt_and_leaves_the_draft_alone(branch, capsys):
    view = {"number": 7, "headRefOid": branch.head, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    branch.answers["gh pr view"] = (0, json.dumps(view))
    for change in ("edit", "commit"):
        branch.commands.clear()

        def during(change=change):
            (branch.root / "app.py").write_text(f"x = {change!r}\n")
            if change == "commit":
                local_git(branch.root, "commit", "-qam", "During the run")

        branch.during["pytest"] = during
        assert branch.run(ready=True) == 2, change
        assert not any(c[:3] == ["gh", "pr", "ready"] for c in branch.commands), change
        assert not test_receipts.SHA.search(capsys.readouterr().out), change


def test_a_python_leg_that_runs_no_test_is_no_pass(tmp_path, monkeypatch):
    # As CI's run(): a leg whose every test skipped proves nothing. The check runs inside the leg's own command, so it
    # holds on a RunsOn VM too, where the report is written.
    report = tmp_path / "local-python.xml"
    assert not checks.junit_ran(report)
    report.write_text(
        '<testsuite><testcase name="a"><skipped/></testcase><testcase name="b"><skipped/></testcase></testsuite>'
    )
    assert not checks.junit_ran(report)
    monkeypatch.setattr("sys.argv", ["pr_checks.py", "junit-ran", str(report)])
    assert checks.main() == 1
    report.write_text('<testsuite><testcase name="a"><skipped/></testcase><testcase name="b"/></testsuite>')
    assert checks.junit_ran(report)
    monkeypatch.setattr("sys.argv", ["pr_checks.py", "junit-ran", str(report)])
    assert checks.main() == 0


def test_a_fail_fast_cancel_names_the_first_failing_tests(tmp_path, monkeypatch, capsys):
    # The leg that failed cancels the run, so it shows "cancelled" too; its error names what failed instead.
    (tmp_path / "python-7.xml").write_text(
        '<testsuite><testcase classname="backend.tests.test_thread_halfway" name="test_bounded"><failure message="x"/>'
        '</testcase><testcase classname="backend.tests.test_ok" name="test_fine"/>'
        '<testcase classname="backend.tests.test_b" name="test_err"><error message="y"/></testcase></testsuite>'
    )
    (tmp_path / "broken.xml").write_text("<testsuite")
    assert checks.junit_failed([tmp_path / "python-7.xml"], limit=1) == [
        "test_thread_halfway.py::test_bounded",
        "and 1 more",
    ]
    monkeypatch.setattr("sys.argv", ["pr_checks.py", "failed", str(tmp_path)])
    assert not checks.main()
    assert capsys.readouterr().out.strip() == "test_thread_halfway.py::test_bounded, test_b.py::test_err"
    workflow = (ROOT / ".github/workflows/pr-checks.yml").read_text()
    assert "pr_checks.py failed ci-results" in workflow and "::error title=${SUITE} failed::" in workflow


def test_a_leg_passes_only_when_pytest_passed_and_a_test_ran_and_keeps_pytests_own_code(tmp_path):
    # A stand-in interpreter answers pytest with $PYTEST and the report check with $CHECK.
    python = tmp_path / "python"
    python.write_text(
        '#!/bin/sh\ncase "$*" in *"-m pytest"*) exit "$PYTEST" ;; *junit-ran*) exit "$CHECK" ;; esac\nexit 9\n'
    )
    python.chmod(0o755)
    leg = checks.pytest_leg(str(python), 2, "python", ["tests/test_app.py"])
    for pytest_code, check_code, expected in ((0, 0, 0), (0, 1, 1), (1, 0, 1), (5, 0, 5), (5, 1, 5)):
        env = {**os.environ, "PYTEST": str(pytest_code), "CHECK": str(check_code)}
        assert subprocess.run(leg, cwd=tmp_path, env=env).returncode == expected, (pytest_code, check_code)


def test_an_inherited_pytest_addopts_never_reaches_the_selected_run(branch, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing")
    assert branch.run() == 0
    assert branch.envs[0] and "PYTEST_ADDOPTS" not in branch.envs[0]


def test_what_ci_alone_runs_is_named_and_the_receipt_still_passes(branch, monkeypatch, capsys):
    branch.seen["plan"] = {**LOCAL_PLAN, "conformance": True, "rust": ["runner-rs"], "browser": []}
    assert branch.run() == 0
    assert len(branch.commands) == 1  # no browser scripts were selected
    line = receipt_line(capsys.readouterr().out)
    assert "conformance" in line
    assert gate(line, branch.head) == "passed"


def test_contract_tests_run_as_the_pull_request_runs_them(branch):
    branch.seen["plan"] = {**LOCAL_PLAN, "python": [], "browser": [], "contracts": True}
    assert branch.run() == 0
    (command,) = branch.commands
    test, _ = pytest_leg(command)
    assert test[test.index("-m", test.index("pytest") + 1) + 1] == "slow"
    assert test[-len(checks.CONTRACT_TESTS) :] == list(checks.CONTRACT_TESTS)


def test_a_remote_run_names_the_interpreter_the_vm_has_not_this_computers_path(branch, monkeypatch):
    # scripts/heavy.sh sends the command to a RunsOn VM (TICO_HEAVY_REMOTE=1), whose venv is python3 on its PATH.
    assert branch.run() == 0
    test, check = pytest_leg(branch.commands[0])
    assert test[0] == check[0] == sys.executable
    branch.commands.clear()
    monkeypatch.setenv("TICO_HEAVY_REMOTE", "1")
    assert branch.run() == 0
    test, check = pytest_leg(branch.commands[0])
    assert test[:3] == ["python3", "-m", "pytest"] and check[0] == "python3"
    # The report path is relative and ignored, so the VM can write it and the checkout stays clean.
    report = next(a.split("=", 1)[1] for a in test if a.startswith("--junitxml="))
    assert report.startswith("ci-results/") and check[-1] == report


def test_a_change_that_selects_no_tests_still_gets_a_passing_receipt(branch, capsys):
    branch.seen["plan"] = {**LOCAL_PLAN, "python": [], "browser": []}
    assert branch.run() == 0
    assert branch.commands == []
    assert gate(receipt_line(capsys.readouterr().out), branch.head) == "passed"


def test_ready_marks_the_draft_ready_only_after_a_pass_at_the_pull_requests_own_head(branch, capsys):
    view = {"number": 7, "headRefOid": branch.head, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    branch.answers["gh pr view"] = (0, json.dumps(view))
    assert branch.run(ready=True) == 0
    assert branch.commands[-1][:4] == ["gh", "pr", "ready", "7"]
    assert gate(receipt_line(capsys.readouterr().out), branch.head) == "passed"


def test_ready_leaves_a_pull_request_that_is_already_ready_alone(branch):
    view = {"number": 7, "headRefOid": branch.head, "isDraft": False, "url": "https://github.com/acme/app/pull/7"}
    branch.answers["gh pr view"] = (0, json.dumps(view))
    assert branch.run(ready=True) == 0
    assert not any(c[:3] == ["gh", "pr", "ready"] for c in branch.commands)


def test_ready_refuses_before_testing_when_the_pull_request_head_is_not_this_commit(branch, capsys):
    view = {"number": 7, "headRefOid": "a" * 40, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    branch.answers["gh pr view"] = (0, json.dumps(view))
    assert branch.run(ready=True) == 1
    assert [c[:3] for c in branch.commands] == [["gh", "pr", "view"]]  # no tests, no ready
    assert "push" in capsys.readouterr().err


def test_ready_leaves_the_draft_alone_when_a_push_moved_its_head_during_the_run(branch, capsys):
    view = {"number": 7, "headRefOid": branch.head, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    moved = {**view, "headRefOid": "b" * 40}
    branch.answers["gh pr view"] = [(0, json.dumps(view)), (0, json.dumps(moved))]
    assert branch.run(ready=True) == 1
    assert not any(c[:3] == ["gh", "pr", "ready"] for c in branch.commands)
    out = capsys.readouterr()
    assert gate(receipt_line(out.out), branch.head) == "passed"  # the receipt still holds for the head it ran
    assert "b" * 40 in out.err


def test_a_remote_run_that_never_reported_a_result_prints_no_receipt(branch, capsys):
    # scripts/remote.py (TICO_HEAVY_REMOTE=1): 75 never dispatched (an unpushed head), 76 no result. Neither is a test
    # failure, and a failed receipt would block this head for good.
    for code in (75, 76):
        branch.commands.clear()
        branch.answers["pytest"] = (code, "")
        assert branch.run() == 2
        out = capsys.readouterr()
        assert not test_receipts.SHA.search(out.out) and gate(out.out, branch.head) in ("missing", "unrecognised")


def test_ready_after_a_failure_leaves_the_draft_a_draft(branch):
    view = {"number": 7, "headRefOid": branch.head, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    branch.answers["gh pr view"] = (0, json.dumps(view))
    branch.answers["pytest"] = (1, "")
    assert branch.run(ready=True) == 1
    assert not any(c[:3] == ["gh", "pr", "ready"] for c in branch.commands)


def test_the_command_line_runs_local_without_the_ci_only_plan_guard(monkeypatch):
    calls = []
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("TICO_PLAN_LOCAL", raising=False)
    monkeypatch.setattr(checks, "local", lambda **kwargs: calls.append(kwargs) or 0)
    monkeypatch.setattr("sys.argv", ["pr_checks.py", "local", "--base", "origin/main", "--jobs", "3", "--ready"])
    assert checks.main() == 0
    assert calls == [{"base": "origin/main", "jobs": 3, "ready": True}]


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


def light_tree(root):
    """leaf <- helper <- outer, each with its own test; the Caddy entry point runs the helper and a core browser script
    names outer, so each check sits a known number of levels from a change to leaf."""
    source(root, "backend/__init__.py", "")
    source(root, "backend/leaf.py", "LEAF = 1\n")
    source(root, "backend/helper.py", "from backend.leaf import LEAF\n")
    source(root, "backend/outer.py", "from backend.helper import LEAF\n")
    source(root, "backend/caddy_assets.py", "from backend.helper import LEAF\n")
    source(root, "backend/tests/test_leaf.py", "from backend.leaf import LEAF\ndef test_x(): pass\n")
    source(root, "backend/tests/test_helper.py", "from backend.helper import LEAF\ndef test_x(): pass\n")
    source(root, "backend/tests/test_outer.py", "from backend.outer import LEAF\ndef test_x(): pass\n")
    source(root, "ui/tests/page.cjs", "require('../../backend/outer.py');\n")
    smoke_tree(root)


def test_a_light_pull_request_runs_the_tests_a_change_reaches_directly_and_the_smoke_set(tmp_path):
    light_tree(tmp_path)
    smoke_tests, _ = checks.smoke(tmp_path)
    full = checks.selection(["backend/leaf.py"], root=tmp_path, core=["page"])
    reached = {"backend/tests/test_leaf.py", "backend/tests/test_helper.py", "backend/tests/test_outer.py"}
    assert reached <= set(full["python"]) and full["browser"] == ["page"] and full["caddy"]
    light = checks.narrow(full, ["backend/leaf.py"], "pull_request", root=tmp_path)
    # Only the test that imports leaf itself, and the smoke set because the plan dropped two; never more than the full
    # selection and the smoke set.
    assert set(light["python"]) == {"backend/tests/test_leaf.py"} | set(smoke_tests)
    assert set(light["python"]) <= set(full["python"]) | set(smoke_tests)
    record = light["narrow"]
    assert record["left_transitive"] == ["backend/tests/test_helper.py", "backend/tests/test_outer.py"]
    assert record["tier"]["backend/tests/test_leaf.py"] == 2 and {record["tier"][t] for t in smoke_tests} == {1}
    # The browser script and the Caddy job reach leaf only through other files: left for the queue.
    assert light["browser"] == [] and record["browser_left"] == ["page"] and not light["caddy"]
    # One level nearer, they run: outer is named by the script, helper is run by the Caddy entry point.
    outer = checks.narrow(
        checks.selection(["backend/outer.py"], root=tmp_path, core=["page"]),
        ["backend/outer.py"],
        "pull_request",
        root=tmp_path,
    )
    assert outer["browser"] == ["page"]
    helper = checks.narrow(
        checks.selection(["backend/helper.py"], root=tmp_path, core=["page"]),
        ["backend/helper.py"],
        "pull_request",
        root=tmp_path,
    )
    assert helper["caddy"] and "backend/tests/test_helper.py" in helper["python"]
    # A changed test file is tier 0. A change that drops nothing by depth carries the smoke set only for the cap: prepare
    # removes it when the cap drops nothing too.
    name = "backend/tests/test_outer.py"
    own = checks.narrow(checks.selection([name], root=tmp_path, core=[]), [name], "pull_request", root=tmp_path)
    assert set(own["python"]) == {name} | set(smoke_tests) and own["narrow"]["tier"][name] == 0
    assert not own["narrow"]["smoke"] and own["narrow"]["smoke_unless_capped"] == sorted(smoke_tests)
    # The selection is the same for both events (release validation's incremental runs use it too); main passes as is.
    assert checks.selection(["backend/leaf.py"], "merge_group", root=tmp_path, core=["page"]) == full
    main = checks.selection(["backend/leaf.py"], "push", root=tmp_path, core=[])
    assert checks.narrow(main, ["backend/leaf.py"], "push", root=tmp_path) is main
    # An empty diff is uncertain: its smoke-set plan passes unchanged.
    empty = checks.selection([], root=tmp_path, core=[])
    assert checks.narrow(empty, [], "pull_request", root=tmp_path) is empty


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
def test_a_migration_change_runs_the_upgrade_tests_even_in_a_light_plan(event, repo_map):
    # Nov 12: #3469 added a migration with an index on tasks.private. Its light plan kept only the tests one step from
    # the file, not test_task_privacy.py's legacy upgrade tests, and the release check found the crash instead.
    migration = "backend/migrations/20261112080000_needs_you_per_person.py"
    for changed in ([migration], ["backend/migrations/__init__.py"]):
        picked = checks.selection(changed, event, graph=repo_map, added=changed[:1] if changed == [migration] else [])
        light = checks.narrow(picked, changed, event, added=[], graph=repo_map)
        assert set(checks.UPGRADE_TESTS) <= set(light["python"]), changed
        assert all(light["narrow"]["tier"][t] == 0 for t in checks.UPGRADE_TESTS), changed
    assert all((ROOT / t).is_file() for t in checks.UPGRADE_TESTS)
    # Another change keeps its own plan: the upgrade tests it reaches are not lifted to always run.
    other = ["backend/health.py"]
    tier = checks.narrow(checks.selection(other, event, graph=repo_map), other, event, graph=repo_map)["narrow"]["tier"]
    assert not any(tier.get(t) == 0 for t in checks.UPGRADE_TESTS)


def test_a_light_plan_caps_its_python_tests_by_priority_and_a_changed_test_always_runs():
    tier = {"changed.py": 0, "smoke.py": 1, "near_short.py": 2, "near_long.py": 2, "far.py": 3}
    seconds = {"changed.py": 50, "smoke.py": 20, "near_short.py": 10, "near_long.py": 40, "far.py": 5}
    # changed 50 + smoke 20 + near_short 10 = 80 of 85; near_long does not fit, and the shorter far file after it does.
    kept, left = checks.cap_tests(list(tier), tier, seconds, budget=85)
    assert kept == sorted(["changed.py", "smoke.py", "near_short.py", "far.py"]) and left == ["near_long.py"]
    # Past the budget a changed test file and the smoke set still run; what is left is listed in priority order.
    kept, left = checks.cap_tests(list(tier), tier, seconds, budget=10)
    assert kept == ["changed.py", "smoke.py"] and left == ["near_short.py", "near_long.py", "far.py"]
    # The budget is four VMs of the job target of four workers, setup excluded.
    assert checks.narrow_budget(4) == 4 * checks.WORKERS * (
        checks.NARROW_SHARD_SECONDS - checks.SETUP_SECONDS["python"]
    )


def test_a_light_pull_request_plan_runs_at_most_four_python_vms_and_lists_what_it_left(tmp_path, monkeypatch):
    files = {f"backend/tests/test_{i:02}.py": 1 for i in range(40)}
    monkeypatch.setattr(checks, "collect", lambda: Counter(files))
    monkeypatch.setattr(checks, "merge_parent", lambda base: base)
    monkeypatch.setattr(checks, "image_checks", lambda changed: None)
    monkeypatch.setattr(checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["backend/x.py"])
    monkeypatch.setattr(checks, "committed_timings", lambda: dict.fromkeys(files, 600.0))  # ten minutes each
    monkeypatch.setattr(checks, "DependencyMap", lambda root: "graph")
    far = "backend/tests/test_far.py"

    def fake_selection(changed, event, base=None, added=None, graph=None, head="HEAD"):
        empty = {"browser": [], "caddy": False, "contracts": False, "conformance": False, "rust": []}
        return {**empty, "python": [*sorted(files), far], "reason": "measured", "uncertain": []}

    narrowed = []

    def fake_narrow(picked, changed, event, added=None, graph=None):
        narrowed.append((event, graph))
        tier = {name: 0 if name.endswith("_00.py") else 2 for name in files}
        record = {"event": event, "depth": 1, "shards": 4, "budget_seconds": checks.narrow_budget(4), "smoke": True}
        record["smoke_unless_capped"] = []
        return {**picked, "python": sorted(files), "narrow": {**record, "left_transitive": [far], "tier": tier}}

    monkeypatch.setattr(checks, "selection", fake_selection)
    monkeypatch.setattr(checks, "narrow", fake_narrow)
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", light=True)
    assert narrowed == [("pull_request", "graph")]  # one map for the selection and the narrowing
    python = [job for job in plan["matrix"]["include"] if job["suite"].startswith("python-")]
    kept = sorted(sum((job["files"] for job in python), []))
    assert 1 <= len(python) <= 4
    assert len(kept) == int(checks.narrow_budget(4) // 600) and "backend/tests/test_00.py" in kept
    left = plan["narrow"]["left_over_budget"]
    assert sorted(left + kept) == sorted(files) and "tier" not in plan["narrow"]
    text = summary.read_text()
    assert all(f"`{name}`" in text for name in left) and f"`{far}`" in text and "merge queue group" in text
    # Without --light, or for main, nothing is narrowed: the full selection runs.
    narrowed.clear()
    for event, light in (("pull_request", False), ("push", True)):
        assert checks.prepare(event, "base", tmp_path / "plan.json", light=light).get("narrow") is None, event
    assert narrowed == []
    # A queue group plans light too, from its own base and head, with one map.
    checks.prepare("merge_group", "queue-base", tmp_path / "plan.json", head="queue-head", light=True)
    assert narrowed == [("merge_group", "graph")]


def test_a_queue_group_runs_the_tests_two_levels_from_a_change_and_keeps_its_other_jobs(tmp_path, monkeypatch):
    light_tree(tmp_path)
    smoke_tests, _ = checks.smoke(tmp_path)
    full = checks.selection(["backend/leaf.py"], "merge_group", root=tmp_path, core=["page"])
    group = checks.narrow(full, ["backend/leaf.py"], "merge_group", root=tmp_path)
    # leaf's test (level 1) and helper's (level 2: it runs helper, which runs leaf); outer's is three levels away.
    assert set(group["python"]) == {"backend/tests/test_leaf.py", "backend/tests/test_helper.py"} | set(smoke_tests)
    record = group["narrow"]
    assert (
        record["left_transitive"] == ["backend/tests/test_outer.py"]
        and record["shards"] == checks.NARROW["merge_group"]["shards"]
    )
    assert record["tier"]["backend/tests/test_leaf.py"] == 2 and record["tier"]["backend/tests/test_helper.py"] == 3
    # A queue group keeps its browser scripts, Caddy job and contracts as the selection decided.
    assert (group["browser"], group["caddy"], group["contracts"]) == (full["browser"], full["caddy"], full["contracts"])
    assert "browser_left" not in record
    # A hub (more than HUB_TEST_USERS tests run it directly) is not followed to the second level.
    monkeypatch.setattr(checks, "HUB_TEST_USERS", 0)
    hub = checks.narrow(full, ["backend/leaf.py"], "merge_group", root=tmp_path)
    assert "backend/tests/test_helper.py" not in hub["python"]
    # A light queue group's summary says where the rest runs.
    assert "release validation" in checks.narrow_summary({**record, "left_over_budget": []})


def test_release_validation_selects_the_full_closure_so_it_runs_what_a_queue_group_leaves_out():
    """A queue group drops tests more than two levels away; release validation's incremental runs (every change since the
    last full validation, which runs once in 24 hours) select with pr_checks.selection, which narrowing never changes."""
    source = (ROOT / "scripts/release_increment.py").read_text()
    assert "pr_checks.selection(" in source and "narrow" not in source and "--light" not in source
    for name in ("ci.yml", "train.yml", "release.yml", "hotfix.yml"):
        assert "--light" not in (ROOT / ".github/workflows" / name).read_text(), name


def test_a_light_plan_adds_the_smoke_set_only_when_it_drops_a_test_and_packs_with_the_seconds_it_capped_by(
    tmp_path, monkeypatch
):
    """Codex review: the cap alone can drop tests, so the smoke set waits for it; and the VMs are packed with the same
    seconds the cap used (the committed table under the run's timings), so a VM is not planned from a guess."""
    smoke_tests, _ = checks.smoke(ROOT)
    direct = [f"backend/tests/test_direct_{i:02}.py" for i in range(14)]
    collected = Counter(dict.fromkeys([*direct, *smoke_tests], 1))
    monkeypatch.setattr(checks, "collect", lambda: collected)
    monkeypatch.setattr(checks, "merge_parent", lambda base: base)
    monkeypatch.setattr(checks, "image_checks", lambda changed: None)
    monkeypatch.setattr(checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["backend/x.py"])
    monkeypatch.setattr(checks, "DependencyMap", lambda root: "graph")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    size = {"n": 14}

    def fake_selection(changed, event, base=None, added=None, graph=None, head="HEAD"):
        empty = {"browser": [], "caddy": False, "contracts": False, "conformance": False, "rust": []}
        return {**empty, "python": direct[: size["n"]], "reason": "measured", "uncertain": []}

    def fake_narrow(picked, changed, event, added=None, graph=None):
        tier = dict.fromkeys(picked["python"], 2) | dict.fromkeys(smoke_tests, 1)
        record = {"event": event, "depth": 1, "shards": 4, "budget_seconds": checks.narrow_budget(4), "smoke": False}
        record |= {"left_transitive": [], "tier": tier, "smoke_unless_capped": sorted(smoke_tests)}
        return {**picked, "python": sorted({*picked["python"], *smoke_tests}), "narrow": record}

    monkeypatch.setattr(checks, "selection", fake_selection)
    monkeypatch.setattr(checks, "narrow", fake_narrow)
    # The smoke set is about two minutes in all (its files are small); the direct files are ten minutes each.
    monkeypatch.setattr(
        checks, "committed_timings", lambda: dict.fromkeys(direct, 600.0) | dict.fromkeys(smoke_tests, 20.0)
    )
    # Fourteen ten-minute files: the cap drops some, so the smoke set runs (it ranks before them).
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", light=True)
    assert plan["narrow"]["left_over_budget"] and plan["narrow"]["smoke"]
    assert set(smoke_tests) <= set(plan["python"])
    # Packed from the same seconds: no VM carries more than its share of the budget plus one file.
    python = [job for job in plan["matrix"]["include"] if job["suite"].startswith("python-")]
    loads = plan["estimated_seconds"][: len(python)]
    assert (
        len(python) == 4
        and max(loads) <= sum(loads) / 4 + 600
        and sum(loads) <= checks.narrow_budget(4) + 600 * len(smoke_tests)
    )
    # Two files fit: nothing is dropped, so the smoke set does not run.
    size["n"] = 2
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", light=True)
    assert plan["python"] == direct[:2] and not plan["narrow"]["smoke"] and not plan["narrow"]["left_over_budget"]


def test_a_queue_group_follows_shared_test_modules_and_reads_by_repository_listers(tmp_path):
    light_tree(tmp_path)
    source(tmp_path, "backend/tests/test_shared.py", "from backend.leaf import LEAF\ndef test_x(): pass\n")
    source(tmp_path, "backend/tests/test_reuses.py", "from backend.tests.test_shared import LEAF\ndef test_y(): pass\n")
    source(tmp_path, "backend/tests/test_lists.py", "import importlib\nimportlib.import_module(name)\n")
    full = checks.selection(["backend/leaf.py"], "merge_group", root=tmp_path, core=[])
    group = checks.narrow(full, ["backend/leaf.py"], "merge_group", root=tmp_path)
    # A test module that other tests import is followed like any module; a file that may load anything is level 1.
    assert {"backend/tests/test_reuses.py", "backend/tests/test_lists.py"} <= set(group["python"])
    light = checks.narrow(full, ["backend/leaf.py"], "pull_request", root=tmp_path)
    assert "backend/tests/test_lists.py" in light["python"] and "backend/tests/test_reuses.py" not in light["python"]
    # The queue group runs everything its pull request's light run did.
    assert set(light["python"]) <= set(group["python"])


def test_a_light_plan_packs_untimed_files_with_the_cap_estimate_and_keeps_the_smoke_set_past_the_budget(
    tmp_path, monkeypatch
):
    """Codex review, round 2: untimed files are packed with the seconds the cap estimated for them (not re-estimated
    from fewer files), and the smoke set runs even when changed tests alone fill the budget."""
    smoke_tests, _ = checks.smoke(ROOT)
    changed = [f"backend/tests/test_changed_{i:02}.py" for i in range(12)]
    collected = Counter(dict.fromkeys([*changed, *smoke_tests], 10))
    monkeypatch.setattr(checks, "collect", lambda: collected)
    monkeypatch.setattr(checks, "merge_parent", lambda base: base)
    monkeypatch.setattr(checks, "image_checks", lambda changed: None)
    monkeypatch.setattr(checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else changed)
    monkeypatch.setattr(checks, "DependencyMap", lambda root: "graph")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def fake_selection(changed_files, event, base=None, added=None, graph=None, head="HEAD"):
        empty = {"browser": [], "caddy": False, "contracts": False, "conformance": False, "rust": []}
        return {**empty, "python": [*changed, "backend/tests/test_far.py"], "reason": "measured", "uncertain": []}

    def fake_narrow(picked, changed_files, event, added=None, graph=None):
        tier = dict.fromkeys(changed, 0) | dict.fromkeys(smoke_tests, 1)
        record = {"event": event, "depth": 1, "shards": 4, "budget_seconds": checks.narrow_budget(4), "smoke": True}
        record |= {"left_transitive": ["backend/tests/test_far.py"], "tier": tier, "smoke_unless_capped": []}
        return {**picked, "python": sorted({*changed, *smoke_tests}), "narrow": record}

    monkeypatch.setattr(checks, "selection", fake_selection)
    monkeypatch.setattr(checks, "narrow", fake_narrow)
    # Twelve ten-minute changed tests fill the budget alone; the smoke set still runs.
    monkeypatch.setattr(checks, "committed_timings", lambda: dict.fromkeys(changed, 600.0))
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", light=True)
    assert set(smoke_tests) | set(changed) == set(plan["python"])
    # One timed file and the rest untimed: the VMs' planned load is the seconds the cap selected.
    monkeypatch.setattr(checks, "committed_timings", lambda: {changed[0]: 600.0})
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", light=True)
    python = [job for job in plan["matrix"]["include"] if job["suite"].startswith("python-")]
    assert sum(plan["estimated_seconds"][: len(python)]) == pytest.approx(plan["narrow"]["selected_seconds"], abs=1)


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
@pytest.mark.parametrize("light", [False, True])
def test_no_planned_test_job_runs_past_the_target_for_a_full_or_narrowed_selection(tmp_path, monkeypatch, event, light):
    """Every Python and browser VM of a plan finishes within the target (JOB_SECONDS, setup included, one file's margin),
    for the recorded full suite and for a narrowed plan (NARROW), in pull request and merge queue runs."""
    files = [f"backend/tests/test_{i:03}.py" for i in range(930)]
    names = [f"s{i}" for i in range(40)]
    monkeypatch.setattr(checks, "collect", lambda: Counter(dict.fromkeys(files, 1)))
    monkeypatch.setattr(checks, "committed_timings", lambda: {})
    monkeypatch.setattr(checks, "browser_scripts", lambda args, root=None: names)
    monkeypatch.setattr(checks, "image_checks", lambda changed, root=None: None)
    monkeypatch.setattr(checks, "merge_parent", lambda base: base)
    monkeypatch.setattr(checks, "script_changes", lambda base, head, changed, root=None: [])
    monkeypatch.setattr(checks, "changed_files", lambda base, head="HEAD", only=None: [] if only else ["backend/x.py"])
    monkeypatch.setattr(checks, "DependencyMap", lambda root: "graph")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    timings = tmp_path / "durations.json"
    timings.write_text(
        json.dumps({**{name: 25800 / 930 for name in files}, **{f"ui/tests/{n}.cjs": 60 for n in names}})
    )

    def fake_selection(changed, event, base=None, added=None, graph=None, head="HEAD"):
        empty = {"caddy": False, "contracts": False, "conformance": False, "rust": []}
        return {**empty, "python": list(files), "browser": list(names), "reason": "measured", "uncertain": []}

    def fake_narrow(picked, changed, event, added=None, graph=None):
        mode = checks.NARROW[event]
        record = {
            "event": event,
            "depth": mode["depth"],
            "shards": mode["shards"],
            "budget_seconds": checks.narrow_budget(mode["shards"]),
            "smoke": False,
            "smoke_unless_capped": [],
            "left_transitive": [],
            "tier": {name: 2 for name in picked["python"]},
        }
        return {**picked, "narrow": record}

    monkeypatch.setattr(checks, "selection", fake_selection)
    monkeypatch.setattr(checks, "narrow", fake_narrow)
    plan = checks.prepare(event, "base", tmp_path / "plan.json", timings, light=light)
    python = [job for job in plan["matrix"]["include"] if job["suite"].startswith("python-")]
    browser = [job for job in plan["matrix"]["include"] if job["suite"].startswith("browser-")]
    assert python and browser and len(python) <= checks.MAX_JOBS
    if light:
        # The narrowed plan caps its VMs at the event's shards, and never runs more than that many.
        assert len(python) <= checks.NARROW[event]["shards"]
    else:
        assert len(python) == checks.job_count(25800, "python")
    loads = plan["estimated_seconds"]
    python_loads, browser_loads = loads[: len(python)], loads[len(python) :]
    margin = 30  # a file's worth of one-worker time on each worker: files are whole
    assert max(python_loads) / checks.WORKERS + checks.SETUP_SECONDS["python"] <= checks.JOB_SECONDS + margin
    assert max(browser_loads) / checks.WORKERS + checks.SETUP_SECONDS["browser"] <= checks.JOB_SECONDS + margin
    assert plan["job_target_seconds"] == checks.JOB_SECONDS
    assert all(seconds <= checks.JOB_SECONDS + margin for seconds in [s / checks.WORKERS + 150 for s in python_loads])


def test_a_contracts_lane_whose_measured_files_overrun_is_split_and_warned(tmp_path, monkeypatch, capsys):
    # The three release contracts can carry measured seconds (their own slow tests). Files are whole, so each file gets
    # its own timed VM, named contracts-N and run with -m slow; a single file past one VM's work is warned, not hidden.
    picked = {"python": [], "browser": [], "caddy": False, "conformance": False, "rust": [], "contracts": True}
    monkeypatch.setattr(checks, "selection", lambda *a, **kw: dict(picked))
    monkeypatch.setattr(checks, "changed_files", lambda *a, **kw: [])
    monkeypatch.setattr(checks, "collect", Counter)
    timings = tmp_path / "durations.json"
    timings.write_text(json.dumps({name: 500.0 for name in checks.CONTRACT_TESTS}))
    plan = checks.prepare("pull_request", "base", tmp_path / "plan.json", timings_path=timings)
    jobs = [job for job in plan["matrix"]["include"] if job["suite"].startswith("contracts")]
    assert [job["suite"] for job in jobs] == ["contracts-1", "contracts-2", "contracts-3"]
    assert sorted(sum((job["files"] for job in jobs), [])) == sorted(checks.CONTRACT_TESTS)
    assert "::warning::" not in capsys.readouterr().out
    assert set(plan["estimated_seconds"]) <= {0.0}  # the contracts VMs do not change the python and browser loads
    timings.write_text(json.dumps({name: 1000.0 for name in checks.CONTRACT_TESTS}))  # 400 s a VM: past the target
    checks.prepare("pull_request", "base", tmp_path / "plan.json", timings_path=timings)
    assert "::warning::A contracts VM is planned for 400s" in capsys.readouterr().out
