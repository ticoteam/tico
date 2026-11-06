"""Optional release jobs carry unchanged apps forward without bypassing failed prerequisites."""
import os
import re
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
JOBS = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())["jobs"]
CORE = {"validated", "runner-changes", "release"}


def _runs(jobs, name, results, outputs, *, cancelled=False, inherited=()):
    def parents(name):
        needs = jobs[name].get("needs", [])
        return [needs] if isinstance(needs, str) else needs

    def ancestors(name):
        return set(parents(name)).union(*(ancestors(parent) for parent in parents(name)))

    # GitHub's implicit success() includes transitive ancestors, including the caller of a reusable workflow.
    success = not cancelled and all(result == "success" for result in
                                   [*(results[parent] for parent in ancestors(name)), *inherited])
    condition = jobs[name].get("if", "success()")
    if not re.search(r"\b(?:success|failure|cancelled|always)\(", condition) and not success:
        return False

    def value(match):
        parent, output = match.groups()
        assert parent in parents(name), f"{name} references a job outside its needs: {parent}"
        return repr(outputs.get(parent, {}).get(output, "") if output else results[parent])

    condition = condition.removeprefix("${{").removesuffix("}}").strip()
    condition = re.sub(r"needs\.([\w-]+)\.(?:outputs\.([\w-]+)|result)", value, condition)
    condition = re.sub(r"!(?!=)", " not ", condition).replace("&&", " and ").replace("||", " or ")
    return bool(eval(condition.strip(), {"__builtins__": {}},
                     {"cancelled": lambda: cancelled, "success": lambda: success}))


def _schedule(*, published=False, dry_run=False, build=True, enabled=True, failures=()):
    outputs = {
        "validated": {"published": "true" if published else "false",
                      "dry_run": "" if "validated" in failures else str(dry_run).lower()},
        **{name: {"build": str(build).lower()} for name in
           ("runner-changes", "desktop-changes", "recorder-changes")},
        "company-matrix": {"enabled": str(enabled).lower()},
    }
    results = {}
    for name in JOBS:
        if _runs(JOBS, name, results, outputs):
            results[name] = "failure" if name in failures else "success"
        else:
            results[name] = "skipped"
    return results


@pytest.mark.parametrize("published", [False, True], ids=["normal-release", "published-rebuild"])
def test_release_build_and_carry_forward_schedules(published):
    results = _schedule(published=published)
    assert {name for name, result in results.items() if result == "success"} == JOBS.keys() - (
        {"release"} if published else set())

    results = _schedule(published=published, build=False)
    assert {name for name, result in results.items() if result == "success"} == {
        "validated", "runner-changes", "desktop-changes", "recorder-changes", "desktop-assets", "recorder-assets",
        *([] if published else ["release"]),
    }
    assert _schedule(published=published, enabled=False)["companies"] == "skipped"


def test_dry_run_and_failed_validation_never_start_optional_jobs():
    dry = _schedule(dry_run=True)
    assert {name for name, result in dry.items() if result == "success"} == CORE
    failed = _schedule(failures={"validated"})
    assert failed["validated"] == "failure"
    assert all(result == "skipped" for name, result in failed.items() if name != "validated")


@pytest.mark.parametrize(("failed", "blocked"), [
    ("release", JOBS.keys() - CORE),
    ("runner-changes", {"macos", "macos-assets"}),
    ("macos", {"macos-assets"}),
    ("desktop-changes", {"desktop", "desktop-assets", "company-matrix", "companies"}),
    ("desktop", {"desktop-assets"}),
    ("company-matrix", {"companies"}),
    ("recorder-changes", {"recorder", "recorder-assets"}),
    ("recorder", {"recorder-assets"}),
])
def test_failed_prerequisites_block_dependents_even_with_outputs(failed, blocked):
    # A failed job can leave outputs behind. A published rebuild isolates runner-changes from release's own gate.
    results = _schedule(published=failed != "release", failures={failed})
    assert results[failed] == "failure"
    assert all(results[name] == "skipped" for name in blocked)


def test_cancelled_workflow_never_starts_optional_jobs():
    results = dict.fromkeys(JOBS, "success")
    outputs = {name: {"build": "true", "enabled": "true"} for name in JOBS}
    for name in JOBS.keys() - CORE:
        assert not _runs(JOBS, name, results, outputs, cancelled=True)


def test_company_publish_survives_skipped_release_and_requires_successful_build():
    jobs = yaml.safe_load((ROOT / ".github/workflows/company-app.yml").read_text())["jobs"]
    for result in ("success", "failure", "skipped", "cancelled"):
        assert _runs(jobs, "publish", {"build": result}, {}, inherited=("skipped",)) == (result == "success")
    assert not _runs(jobs, "publish", {"build": "success"}, {}, cancelled=True)


@pytest.fixture
def app_build_decision(tmp_path, monkeypatch):
    """Execute the workflow's actual diff step against a tiny release history; only GitHub is stubbed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Acme")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "release@acme.example")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Acme")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "release@acme.example")

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                              text=True, timeout=10).stdout.strip()

    git("init", "-q")
    git("commit", "-qm", "Previous release", "--allow-empty")
    git("tag", "v1.0.0")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$RELEASE_ASSETS"\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')

    def decide(kind, paths=(), *, force=False, assets="latest.json\nrecorder-latest.json"):
        git("checkout", "-qf", "--detach", "v1.0.0")
        for name in paths:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("changed\n")
        git("add", ".")
        git("commit", "-qm", "Release candidate", "--allow-empty")
        sha = git("rev-parse", "HEAD")
        git("tag", "-f", "v1.0.1")
        output = tmp_path / "outputs"
        output.write_text("")
        env = {**os.environ, "GITHUB_REF_NAME": "v1.0.1", "GITHUB_SHA": sha, "GITHUB_OUTPUT": str(output),
               "FORCE": str(force).lower(), "RELEASE_ASSETS": assets}
        script = next(step["run"] for step in JOBS[f"{kind}-changes"]["steps"] if step.get("id") == "diff")
        result = subprocess.run(["bash", "-e", "-c", script], cwd=repo, env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert values["previous"] == "v1.0.0"
        return values["build"] == "true"

    return decide


@pytest.mark.slow
@pytest.mark.parametrize(("kind", "paths"), [
    ("recorder", ["recorder/tests/updates.cjs", "recorder/tests/nested/fixture.json",
                  "recorder/src/network_tests.rs", "recorder/src/stream_tests.rs", "recorder/src/app/tests.rs",
                  "recorder/bench/assist_cases.json", "recorder/ui/preview/mock.js", "recorder/README.md",
                  "recorder/DEVELOPMENT.md", "recorder/vendor/anarlog/README.md", "recorder/ui/app/docs/guide.md"]),
    ("desktop", ["app/tests/update_hub.py", "app/tests/glib-backport/tests/variant_str_iter.rs",
                 "app/src/testdata/presigned-artifact-url.txt", "app/vendor/glib/tests/value.rs",
                 "app/vendor/glib/benches/gstring.rs", "app/README.md", "app/vendor/glib/README.md"]),
])
def test_support_only_changes_keep_the_previous_app_version(app_build_decision, kind, paths):
    assert not app_build_decision(kind, paths)


@pytest.mark.slow
@pytest.mark.parametrize(("kind", "paths"), [
    ("recorder", ["recorder/CHANGES.md", "recorder/src/app/recorder.rs", "recorder/ui/app/app.js",
                  "recorder/tauri.conf.json", "recorder/icons/icon.icns", "recorder/Cargo.lock",
                  "recorder/package-macos.sh", "recorder/src/new_tests.rs", "recorder/src/capture/tests.rs",
                  "scripts/app_release.py", ".github/workflows/recorder.yml"]),
    ("desktop", ["app/src/main.rs", "app/shell/setup.js", "app/tauri.conf.json", "app/icons/icon.icns",
                 "app/Cargo.lock", "app/vendor/glib/src/lib.rs", "scripts/app_release.py",
                 "scripts/company_apps.py", ".github/workflows/app.yml", ".github/workflows/company-app.yml"]),
])
def test_shipping_changes_still_build_even_alongside_docs(app_build_decision, kind, paths):
    for path in paths:
        assert app_build_decision(kind, ["recorder/README.md", "app/README.md", path]), path


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["desktop", "recorder"])
def test_unchanged_apps_build_when_forced_or_missing_previous_assets(app_build_decision, kind):
    assert not app_build_decision(kind)
    assert app_build_decision(kind, force=True)
    other_manifest = "latest.json" if kind == "recorder" else "recorder-latest.json"
    assert app_build_decision(kind, assets=other_manifest)
