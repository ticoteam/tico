"""Build, check and publish a new cloud machine base snapshot (runner_support/base_snapshot.py), against a fake boat CLI."""

import json
import shutil
import subprocess

import pytest

from runner_support import base_snapshot as bs

RELEASE, NAME = "v0.6.81", "tico-runner-base-0-6-81"
MISSING = json.dumps({"code": "not_found", "error": "no saved snapshot", "event": "error", "status": 404})


class FakeBoat:
    """Answers the boat commands the script runs. `saved` are the saved snapshot names; `fail` maps a step to a failure."""

    def __init__(self, saved=(), fail=(), shares=None):
        self.calls, self.saved, self.fail, self.shares = [], set(saved), set(fail), shares
        self.sandboxes, self.next_id = {}, 1

    def __call__(self, argv, **kw):
        assert argv[0] == "boat"
        args = argv[1:]
        self.calls.append(args)
        if args[:2] == ["snapshot", "shares"]:
            if self.shares:
                return self.shares
            if args[2] in self.saved:
                return done('{"shares": []}')
            return done("", MISSING, 1)
        if args[0] == "new":
            assert "--no-env" in args, "a build sandbox never gets the account's secrets"
            source = args[args.index("--from") + 1] if "--from" in args else None
            sandbox = f"sb{self.next_id}"
            self.next_id += 1
            self.sandboxes[sandbox] = source
            return done(json.dumps({"event": "creating"}) + "\n" + json.dumps({"event": "ready", "id": sandbox}))
        if args[0] == "exec":
            sandbox, script = args[1], args[-1]
            if "cloud_machine base" in script:
                return done(code=1 if "build" in self.fail else 0)
            step = "check-trial" if self.sandboxes[sandbox] else "check-build"
            if step in self.fail:
                return done("check failed: this runner is enrolled\n", code=1)
            return done("check passed\n")
        if args[0] == "snapshot":
            self.saved.add(args[2])
            return done(code=1 if "save" in self.fail else 0)
        if args[0] == "delete":
            assert args[2] == "--yes"
            return done()
        raise AssertionError(f"unexpected boat call {args}")

    def steps(self):
        return [" ".join(a[:2]) if a[0] == "snapshot" else a[0] for a in self.calls]


def done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def run(fake, *extra, release=RELEASE, name=NAME):
    said = []
    code = bs.main(["--release", release, "--name", name, *extra], boat=bs.Boat(run=fake, say=said.append))
    return code, "\n".join(said)


def test_builds_checks_saves_under_the_new_name_then_checks_what_was_saved():
    fake = FakeBoat(saved={"tico-runner-base"})
    code, said = run(fake)
    assert code == 0
    assert fake.steps() == [
        "snapshot shares",
        "new",
        "exec",  # the base step
        "exec",  # the check on the build sandbox
        "snapshot shares",  # the name is looked at again right before the save
        "snapshot sb1",
        "new",  # from the saved snapshot
        "exec",  # the same check on it
        "delete",
        "delete",
    ]
    assert fake.sandboxes == {"sb1": None, "sb2": NAME}
    assert ["delete", "sb1", "--yes"] in fake.calls and ["delete", "sb2", "--yes"] in fake.calls
    assert fake.saved == {"tico-runner-base", NAME}  # the snapshot HQ uses now is untouched
    build = fake.calls[2][-1]
    assert f"--branch {RELEASE}" in build and "cloud_machine base --user user" in build
    assert f"HQ_BOAT_BASE_SNAPSHOT={NAME}" in said and "setting it back" in said


@pytest.mark.parametrize("name", ["tico-runner-base", NAME])
def test_a_name_that_exists_is_never_replaced(name):
    fake = FakeBoat(saved={"tico-runner-base", NAME})
    code, _ = run(fake, name=name)
    assert code == 1
    assert fake.steps() == ["snapshot shares"]  # no sandbox, no save


def test_an_unclear_answer_about_the_name_is_refused():
    fake = FakeBoat(shares=done("", json.dumps({"code": "unauthorized", "status": 401}), 1))
    code, _ = run(fake)
    assert code == 1
    assert fake.steps() == ["snapshot shares"]


@pytest.mark.parametrize(
    "release,name",
    [("0.6.81", NAME), ("v0.6", NAME), ("main", NAME), (RELEASE, "tico-runner-base-0.6.81"), (RELEASE, "Tico Base")],
)
def test_bad_arguments_are_refused_before_boat(release, name):
    fake = FakeBoat()
    code, _ = run(fake, release=release, name=name)
    assert code == 1
    assert fake.calls == []


@pytest.mark.parametrize("step", ["build", "check-build", "save"])
def test_a_failure_before_the_save_publishes_nothing_and_cleans_up(step):
    fake = FakeBoat(fail={step})
    code, _ = run(fake)
    assert code == 1
    assert "new" in fake.steps() and fake.steps()[-1] == "delete"
    assert fake.steps().count("new") == 1  # never a sandbox from a snapshot that was not saved
    if step != "save":
        assert NAME not in fake.saved


def test_a_saved_snapshot_that_fails_its_check_is_reported_and_both_sandboxes_go():
    fake = FakeBoat(fail={"check-trial"})
    code, _ = run(fake)
    assert code == 1
    assert fake.steps()[-2:] == ["delete", "delete"]


def test_keep_leaves_the_sandboxes():
    fake = FakeBoat()
    code, said = run(fake, "--keep")
    assert code == 0
    assert "delete" not in fake.steps()
    assert "kept sandbox sb1" in said and "kept sandbox sb2" in said


def test_check_names_the_failure():
    fake = FakeBoat(fail={"check-build"})
    boat = bs.Boat(run=fake, say=lambda _: None)
    fake.sandboxes["sb9"] = None
    with pytest.raises(bs.Refused, match="this runner is enrolled"):
        boat.check("sb9", RELEASE)


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("script", [bs.build_script(RELEASE), bs.check_script(RELEASE, settle_s=5)])
def test_the_sandbox_scripts_parse(script):
    assert subprocess.run(["bash", "-n", "-c", script]).returncode == 0


def test_the_check_reads_the_marker_without_the_v():
    script = bs.check_script(RELEASE)
    assert '[ "$release" = 0.6.81 ]' in script and '[ "$tag" = v0.6.81 ]' in script
