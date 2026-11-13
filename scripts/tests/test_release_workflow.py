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


def _runs(jobs, name, results, outputs, *, cancelled=False, inherited=(), ref="v0.6.1"):
    def parents(name):
        needs = jobs[name].get("needs", [])
        return [needs] if isinstance(needs, str) else needs

    def ancestors(name):
        return set(parents(name)).union(*(ancestors(parent) for parent in parents(name)))

    # GitHub's implicit success() includes transitive ancestors, including the caller of a reusable workflow.
    success = not cancelled and all(
        result == "success" for result in [*(results[parent] for parent in ancestors(name)), *inherited]
    )
    condition = jobs[name].get("if", "success()")
    if not re.search(r"\b(?:success|failure|cancelled|always)\(", condition) and not success:
        return False

    def value(match):
        parent, output = match.groups()
        assert parent in parents(name), f"{name} references a job outside its needs: {parent}"
        return repr(outputs.get(parent, {}).get(output, "") if output else results[parent])

    condition = condition.removeprefix("${{").removesuffix("}}").strip()
    condition = re.sub(r"needs\.([\w-]+)\.(?:outputs\.([\w-]+)|result)", value, condition)
    condition = condition.replace("github.ref_name", repr(ref))
    condition = re.sub(r"!(?!=)", " not ", condition).replace("&&", " and ").replace("||", " or ")
    return bool(
        eval(
            condition.strip(),
            {"__builtins__": {}},
            {
                "cancelled": lambda: cancelled,
                "success": lambda: success,
                "always": lambda: True,
                "contains": lambda haystack, needle: needle in haystack,
            },
        )
    )


def _schedule(*, published=False, dry_run=False, build=True, enabled=True, failures=(), ref="v0.6.1"):
    outputs = {
        "validated": {
            "published": "true" if published else "false",
            "dry_run": "" if "validated" in failures else str(dry_run).lower(),
        },
        **{name: {"build": str(build).lower()} for name in ("runner-changes", "desktop-changes", "recorder-changes")},
        "company-matrix": {"enabled": str(enabled).lower()},
    }
    results = {}
    for name in JOBS:
        if _runs(JOBS, name, results, outputs, ref=ref):
            results[name] = "failure" if name in failures else "success"
        else:
            results[name] = "skipped"
    return results


@pytest.mark.parametrize("published", [False, True], ids=["normal-release", "published-rebuild"])
def test_release_build_and_carry_forward_schedules(published):
    results = _schedule(published=published)
    assert {name for name, result in results.items() if result == "success"} == JOBS.keys() - (
        {"release"} if published else set()
    )

    results = _schedule(published=published, build=False)
    assert {name for name, result in results.items() if result == "success"} == {
        "validated",
        "runner-changes",
        "desktop-changes",
        "recorder-changes",
        "desktop-assets",
        "recorder-assets",
        *([] if published else ["release"]),
    }
    assert _schedule(published=published, enabled=False)["companies"] == "skipped"


@pytest.mark.parametrize("ref", ["v0.6.0-rc.1"])
def test_a_prerelease_builds_the_generic_apps_and_no_company_app(ref):
    # A company's installed apps update from its own bucket's latest.json: a prerelease must never move it.
    results = _schedule(ref=ref)
    assert results["company-matrix"] == results["companies"] == "skipped"
    assert {"desktop", "desktop-assets", "recorder", "recorder-assets", "macos", "macos-assets"} <= {
        name for name, result in results.items() if result == "success"
    }


def test_dry_run_and_failed_validation_never_start_optional_jobs():
    dry = _schedule(dry_run=True)
    assert {name for name, result in dry.items() if result == "success"} == CORE
    failed = _schedule(failures={"validated"})
    assert failed["validated"] == "failure"
    assert all(result == "skipped" for name, result in failed.items() if name != "validated")


@pytest.mark.parametrize(
    ("failed", "blocked"),
    [
        ("release", JOBS.keys() - CORE),
        ("runner-changes", {"macos", "macos-assets"}),
        ("macos", {"macos-assets"}),
        ("desktop-changes", {"desktop", "desktop-assets", "company-matrix", "companies"}),
        ("desktop", {"desktop-assets"}),
        ("company-matrix", {"companies"}),
        ("recorder-changes", {"recorder", "recorder-assets"}),
        ("recorder", {"recorder-assets"}),
    ],
)
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
    # The icon check sets available=true only when its download works; build and publish both wait for it.
    passed = {"icon": {"available": "true"}}
    for result in ("success", "failure", "skipped", "cancelled"):
        expected = result == "success"
        assert _runs(jobs, "publish", {"icon": "success", "build": result}, passed, inherited=("skipped",)) == expected
    assert not _runs(jobs, "publish", {"icon": "success", "build": "success"}, passed, cancelled=True)
    # A failed or skipped icon job never leaves a build or publish to run, even though its build could succeed.
    for icon in ("failure", "skipped", "cancelled"):
        assert not _runs(jobs, "build", {"icon": icon}, {})
        assert not _runs(jobs, "publish", {"icon": icon, "build": "success"}, {})
    unavailable = {"icon": {"available": "false"}}
    assert _runs(jobs, "build", {"icon": "success"}, passed)
    assert not _runs(jobs, "build", {"icon": "success"}, unavailable)
    assert not _runs(jobs, "publish", {"icon": "success", "build": "success"}, unavailable)
    assert jobs["build"]["needs"] == "icon"
    assert set(jobs["publish"]["needs"]) == {"icon", "build"}


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
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()

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
        env = {
            **os.environ,
            "GITHUB_REF_NAME": "v1.0.1",
            "GITHUB_SHA": sha,
            "GITHUB_OUTPUT": str(output),
            "FORCE": str(force).lower(),
            "RELEASE_ASSETS": assets,
        }
        script = next(step["run"] for step in JOBS[f"{kind}-changes"]["steps"] if step.get("id") == "diff")
        result = subprocess.run(
            ["bash", "-e", "-c", script], cwd=repo, env=env, capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        assert values["previous"] == "v1.0.0"
        return values["build"] == "true"

    return decide


@pytest.mark.slow
@pytest.mark.parametrize(
    ("kind", "paths"),
    [
        (
            "recorder",
            [
                "recorder/tests/updates.cts",
                "recorder/tests/nested/fixture.json",
                "recorder/src/network_tests.rs",
                "recorder/src/stream_tests.rs",
                "recorder/src/app/tests.rs",
                "recorder/bench/assist_cases.json",
                "recorder/ui/preview/mock.ts",
                "recorder/README.md",
                "recorder/DEVELOPMENT.md",
                "recorder/vendor/anarlog/README.md",
                "recorder/ui/app/docs/guide.md",
            ],
        ),
        (
            "desktop",
            [
                "app/tests/update_hub.py",
                "app/tests/glib-backport/tests/variant_str_iter.rs",
                "app/src/testdata/presigned-artifact-url.txt",
                "app/vendor/glib/tests/value.rs",
                "app/vendor/glib/benches/gstring.rs",
                "app/README.md",
                "app/vendor/glib/README.md",
            ],
        ),
    ],
)
def test_support_only_changes_keep_the_previous_app_version(app_build_decision, kind, paths):
    assert not app_build_decision(kind, paths)


@pytest.mark.slow
@pytest.mark.parametrize(
    ("kind", "paths"),
    [
        (
            "recorder",
            [
                "recorder/CHANGES.md",
                "recorder/src/app/recorder.rs",
                "recorder/ui/app/app.ts",
                "recorder/tauri.conf.json",
                "recorder/icons/icon.icns",
                "recorder/Cargo.lock",
                "recorder/package-macos.sh",
                "recorder/src/new_tests.rs",
                "recorder/src/capture/tests.rs",
                "recorder/build-ui.cjs",
                "scripts/app_release.py",
                ".github/workflows/recorder.yml",
                "package.json",
                "package-lock.json",
            ],
        ),
        (
            "desktop",
            [
                "app/src/main.rs",
                "app/shell/setup.js",
                "app/tauri.conf.json",
                "app/icons/icon.icns",
                "app/Cargo.lock",
                "app/vendor/glib/src/lib.rs",
                "scripts/app_release.py",
                "scripts/company_apps.py",
                ".github/workflows/app.yml",
                ".github/workflows/company-app.yml",
            ],
        ),
    ],
)
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


# ---- The release train (train.yml), hotfixes (hotfix.yml) and the validations they dispatch (ci.yml) ----


def _workflow(name):
    data = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    return data, data[True]  # PyYAML reads the `on:` key as True


def test_validation_is_dispatch_only_and_keeps_the_title_release_yml_finds():
    from scripts import release_train

    ci, on = _workflow("ci.yml")
    assert set(on) == {"workflow_dispatch"}
    assert (
        ci["run-name"]
        == "Validate ${{ inputs.candidate_sha }} as ${{ inputs.version }} from ${{ inputs.previous_release }}"
        "${{ inputs.anchor != '' && format(' (incremental on {0})', inputs.anchor) || '' }}"
    )
    assert release_train.title("s", "v", "p") == "Validate s as v from p"
    assert ci["concurrency"] == {"group": "manual-release-validation", "cancel-in-progress": False}


def test_an_incremental_validation_is_a_plan_on_an_anchor_and_the_gate_runs_only_what_it_lists():
    ci, on = _workflow("ci.yml")
    inputs = on["workflow_dispatch"]["inputs"]
    # The inputs are optional: an empty anchor is the full validation, unchanged. The dispatch stays small: the anchor,
    # the carried failed tests and the anchor's release; the candidate job makes the plan from its own checkout.
    for name in ("anchor", "carried", "anchor_previous"):
        assert inputs[name]["default"] == "" and inputs[name].get("required") is not True
    assert "plan" not in inputs
    jobs = ci["jobs"]
    assert "matrix" in jobs["candidate"]["outputs"] and "plan" in jobs["candidate"]["outputs"]
    plan_step = next(step for step in jobs["candidate"]["steps"] if step.get("id") == "plan")
    assert plan_step["if"] == "inputs.anchor != ''" and "release_increment.py plan" in plan_step["run"]
    assert "--root candidate" in plan_step["run"] and "--carried" in plan_step["run"]
    # The gate's VMs come from the candidate's plan (release_increment.matrix): the full matrix when the plan is empty.
    assert jobs["gate"]["strategy"]["matrix"] == "${{ fromJSON(needs.candidate.outputs.matrix) }}"
    assert jobs["gate"]["strategy"]["fail-fast"] is False
    # The measured timings run only in a full validation.
    assert jobs["test-timings"]["if"] == "inputs.anchor == ''"
    steps = {step.get("name"): step for step in jobs["gate"]["steps"] if step.get("name")}
    assert "--suite" in steps["Run the candidate's unchanged release gate, this part"]["run"]


def test_an_incremental_validation_runs_hosted_except_its_product_and_desktop_parts():
    """An incremental validation's jobs run on GitHub-hosted runners; its product and desktop VMs need the 35 GiB disk
    and the Docker images, so they stay on RunsOn. A full validation keeps the configured runners."""
    ci, _ = _workflow("ci.yml")
    jobs = ci["jobs"]
    for name in ("candidate", "native-linux", "images"):
        assert "inputs.anchor != ''" in str(jobs[name]["runs-on"]), name
        assert "runs-on=" in str(jobs[name]["runs-on"]), name  # a full validation still runs on RunsOn
    gate = str(jobs["gate"]["runs-on"])
    assert "inputs.anchor != ''" in gate and "matrix.part != 'product'" in gate and "matrix.part != 'desktop'" in gate
    assert "runs-on=" in gate
    # The hosted disk clean-up step is reused, gated on the same mode: no new step text.
    for name in ("native-linux", "images"):
        step = next(step for step in jobs[name]["steps"] if step.get("name", "").startswith("Free disk"))
        assert step["if"] == "vars.TICO_CI_HOSTED == 'true' || inputs.anchor != ''", name
    # The gate's hosted parts are the same mode as its runs-on: its clean-up step follows that condition.
    gate_step = next(step for step in jobs["gate"]["steps"] if step.get("name", "").startswith("Free disk"))
    assert gate_step["run"] == "sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc"
    assert gate_step["if"] == (
        "vars.TICO_CI_HOSTED == 'true' || (inputs.anchor != '' && matrix.part != 'product' && matrix.part != 'desktop')"
    )


def test_the_full_validation_ceiling_is_hosted_and_every_runson_job_waits_for_it():
    """A ceiling of 10 full validations a UTC day, manual dispatches included: a hosted job counts today's full runs
    before any RunsOn VM starts, and every job that would start a VM waits for it."""
    ci, on = _workflow("ci.yml")
    jobs = ci["jobs"]
    assert jobs["ceiling"]["runs-on"] == "ubuntu-24.04"
    assert jobs["ceiling"]["permissions"] == {"actions": "read"}
    step = next(step for step in jobs["ceiling"]["steps"] if "ceiling reached" in step.get("run", ""))
    assert step["if"] == "inputs.anchor == ''" and step["env"]["CAP"] == "10"
    assert "$CAP full runs today; ceiling reached" in step["run"] and "created=$today" in step["run"]

    def waits_for_ceiling(name):
        needs = jobs[name].get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        return "ceiling" in needs or any(waits_for_ceiling(dep) for dep in needs)

    vms = [name for name, job in jobs.items() if "runs-on=" in str(job["runs-on"])]
    assert "candidate" in vms and all(waits_for_ceiling(name) for name in vms)


def test_the_train_checks_out_the_whole_tree_it_diffs_and_never_keeps_its_token():
    train, _ = _workflow("train.yml")
    checkout = next(step for step in train["jobs"]["train"]["steps"] if step.get("id") == "checkout")
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}
    assert "sparse-checkout" not in checkout["with"]


def test_train_triggers_match_the_script_and_every_acting_job_shares_one_lane():
    from scripts import release_train

    train, on = _workflow("train.yml")
    # No release windows: the only schedule is the backstop; pushes and finished validations drive the train.
    assert [entry["cron"] for entry in on["schedule"]] == [release_train.BACKSTOP_CRON]
    assert not hasattr(release_train, "CRONS") and not hasattr(release_train, "window_start")
    guard_env = train["jobs"]["guard"]["steps"][-1]["env"]
    assert guard_env["SCHEDULE"] == "${{ github.event.schedule }}"
    # Who approves a release is a repository variable both jobs read (docs/releasing.md#approval).
    assert guard_env["RELEASE_NEEDS_PERSON"] == "${{ vars.RELEASE_NEEDS_PERSON }}"
    assert train["jobs"]["train"]["env"]["RELEASE_NEEDS_PERSON"] == "${{ vars.RELEASE_NEEDS_PERSON }}"
    assert on["push"] == {"branches": ["main"]}
    assert on["workflow_run"] == {"workflows": ["Release validation"], "types": ["completed"], "branches": ["main"]}
    assert "workflow_dispatch" in on and "concurrency" not in train
    guard = train["jobs"]["guard"]
    # The guard is read-only and outside the lane: a push with nothing to do never queues behind or displaces a hotfix.
    assert "concurrency" not in guard and "environment" not in guard
    assert set(guard["permissions"].values()) == {"read"}
    hotfix, hotfix_on = _workflow("hotfix.yml")
    for job in (train["jobs"]["train"], hotfix["jobs"]["hotfix"]):
        assert job["concurrency"] == {"group": "release-train", "cancel-in-progress": False}
        assert job["environment"] == "release"
        assert "github.ref == 'refs/heads/main'" in job["if"]
        # Read access to pull requests and checks is for the release notes an approval issue carries.
        assert job["permissions"] == {
            "actions": "write",
            "contents": "write",
            "issues": "write",
            "packages": "write",
            "pull-requests": "read",
            "checks": "read",
        }
        for name in ("CANARY_URL", "STABLE_URL", "CANARY_UPDATE_KEY", "STABLE_UPDATE_KEY", "RELEASE_HOLD"):
            assert name in job["env"]
        assert "RELEASE_TAG_TOKEN" not in job["env"]
        # Logged in to ghcr.io before the script moves the images' latest tags.
        assert any("docker/login-action" in step.get("uses", "") for step in job["steps"])
    assert train["jobs"]["train"]["needs"] == "guard"
    inputs = hotfix_on["workflow_dispatch"]["inputs"]
    assert inputs["reason"]["required"] is True
    assert inputs["promote_stable"] == {**inputs["promote_stable"], "type": "boolean", "default": True}
    assert inputs["override_hold"] == {**inputs["override_hold"], "type": "boolean", "default": False}
    assert "--override-hold \"$OVERRIDE_HOLD\"" in hotfix["jobs"]["hotfix"]["steps"][-2]["run"]
    # Free text reaches the shell only through the environment, never pasted into a script.
    for name in ("train.yml", "hotfix.yml"):
        data, _ = _workflow(name)
        for job in data["jobs"].values():
            for step in job.get("steps", []):
                assert not re.search(r"inputs\.reason|workflow_run\.(display_title|html_url)", step.get("run", "")), (
                    name
                )


def test_the_train_starts_release_yml_on_the_tag_with_inputs_it_declares():
    from scripts import release_train
    import inspect

    _, on = _workflow("release.yml")
    declared = set(on["workflow_dispatch"]["inputs"])
    sent = re.findall(r'dispatch\("release\.yml", version, (\{[^}]*\})\)', inspect.getsource(release_train))
    assert sent and all(set(eval(literal)) <= declared for literal in sent)
    assert all(eval(literal)["latest"] == "false" for literal in sent)
    # latest=false: published, but neither the latest release nor the images' latest tags until the canary is healthy.
    release = JOBS["release"]
    assert release["env"]["LATEST"] == "${{ github.event_name != 'workflow_dispatch' || inputs.latest }}"
    assert on["workflow_dispatch"]["inputs"]["latest"]["default"] is True
    steps = {step.get("name"): step.get("run", "") for step in release["steps"]}
    assert (
        '[ "$LATEST" = false ] || tags+=(-t "ghcr.io/ticoteam/$image:latest")'
        in steps["Point the version tags at the validated images"]
    )
    assert '[ "$LATEST" = false ] && flags+=(--latest=false)' in steps["Publish the release"]
    # On a tag ref, a dispatched run takes its commit and version from the tag, as a push does.
    validated = JOBS["validated"]["steps"][0]["run"]
    assert 'echo "sha=$GITHUB_SHA"; echo "version=$GITHUB_REF_NAME"' in validated
    assert JOBS["desktop-changes"]["steps"][-1]["env"]["FORCE"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.desktop }}"
    )


@pytest.mark.parametrize(
    "train, shipped, shots_run",
    [("success", "true", True), ("failure", "true", True), ("success", "false", False), ("failure", "", False)],
    ids=["shipped", "shipped-but-train-failed", "not-shipped", "train-died-before-reporting"],
)
def test_shots_run_for_a_shipped_candidate_and_never_hold_the_train_or_the_canary(train, shipped, shots_run):
    jobs = _workflow("train.yml")[0]["jobs"]
    outputs = {"train": {"shipped": shipped}}
    results = {"guard": "success", "train": train}
    assert _runs(jobs, "shots", results, outputs) is shots_run
    results["shots"] = "success" if shots_run else "skipped"
    assert _runs(jobs, "approval-shots", results, outputs) is shots_run
    # The issue says the pictures could not be made when the shots job failed, so it runs after a failed shots job too.
    results["shots"] = "failure"
    assert _runs(jobs, "approval-shots", results, outputs) is shots_run


def test_shots_and_their_issue_update_are_scoped_jobs_that_nothing_else_waits_for():
    train, _ = _workflow("train.yml")
    jobs = train["jobs"]
    # Only the approval issue update waits for the shots; the train job and the canary never do.
    assert {name for name, job in jobs.items() if "shots" in str(job.get("needs", ""))} == {"approval-shots"}
    assert jobs["train"]["needs"] == "guard"
    shots = jobs["shots"]
    assert shots["needs"] == "train" and shots["uses"] == "./.github/workflows/release-shots.yml"
    assert shots["with"] == {
        "base": "${{ needs.train.outputs.base }}",
        "head": "${{ needs.train.outputs.sha }}",
        "version": "${{ needs.train.outputs.version }}",
    }
    assert shots["permissions"] == {"contents": "write"}
    assert not {"runs-on", "concurrency", "environment", "steps"} & set(shots)
    approval = jobs["approval-shots"]
    assert approval["needs"] == ["train", "shots"] and approval["if"] == shots["if"]
    assert approval["runs-on"] == "ubuntu-24.04" and approval["timeout-minutes"] <= 10
    assert approval["permissions"] == {"actions": "read", "contents": "read", "issues": "write"}
    assert "concurrency" not in approval and "environment" not in approval
    assert "RELEASE_TAG_TOKEN" not in approval.get("env", {})
    # The pictures' download is allowed to fail: the issue then says so, with this run.
    download = next(step for step in approval["steps"] if "download-artifact" in step.get("uses", ""))
    assert download["continue-on-error"] is True
    assert download["with"]["name"] == "release-shots-${{ needs.train.outputs.version }}"
    put = approval["steps"][-1]
    put_pictures = (
        'python3 scripts/release_train.py shots-into-approval "$VERSION" "$RUNNER_TEMP/release-shots/release-shots.md"'
    )
    assert put["run"] == put_pictures
    assert put["env"] == {"VERSION": "${{ needs.train.outputs.version }}"}


def test_the_pictures_artifact_is_the_one_release_shots_uploads_on_a_spot_4cpu_runner():
    shots, on = _workflow("release-shots.yml")
    assert shots["permissions"] == {}
    job = shots["jobs"]["shots"]
    assert job["permissions"] == {"contents": "write"}
    assert job["outputs"]["artifact"] == "release-shots-${{ inputs.version }}"
    assert on["workflow_call"]["outputs"]["artifact"]["value"] == "${{ jobs.shots.outputs.artifact }}"
    upload = next(step for step in job["steps"] if "upload-artifact" in step.get("uses", ""))
    assert upload["with"]["name"] == "release-shots-${{ inputs.version }}"
    assert "spot=false" not in job["runs-on"] and "runner=4cpu-linux-x64" in job["runs-on"]
    train, _ = _workflow("train.yml")
    assert all("macos" not in str(job.get("runs-on", "")) for job in train["jobs"].values())


def _loads(path):
    """The repo scripts a script needs at import: ROOT / "....py" paths it loads and sibling modules it imports."""
    text = path.read_text()
    needed = set(re.findall(r'ROOT / "([^"]+\.py)"', text))
    for name in re.findall(r"^(?:import|from) (\w+)", text, re.M):
        sibling = path.parent / f"{name}.py"
        if sibling.exists():
            needed.add(str(sibling.relative_to(ROOT)))
    return needed
