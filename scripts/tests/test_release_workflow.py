"""Optional release jobs survive skipped publication without bypassing failed prerequisites."""
import re
from pathlib import Path

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
