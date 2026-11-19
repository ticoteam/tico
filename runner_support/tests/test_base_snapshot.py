"""Build, check and publish a new cloud machine base snapshot (runner_support/base_snapshot.py), against a fake boat CLI."""

import json
import shutil
import subprocess

import pytest

from runner_support import base_snapshot as bs

RELEASE, STEM = "v0.6.81", "tico-runner-base-0-6-81"
MISSING = json.dumps({"code": "not_found", "error": "no saved snapshot", "event": "error", "status": 404})
BUILD_TOKEN = "a" * 64


class FakeBoat:
    """Answers the boat commands the script runs, keeping saved snapshots the way boat does: a save replaces.

    `fail` names steps that fail: build, check-build, save, check-trial, new-trial, new-trial-late (fails after
    printing the sandbox id), new-timeout, delete-timeout:<id>. `before_save` runs just before a save lands."""

    def __init__(self, saved=None, fail=(), shares=None, before_save=None):
        self.calls, self.saved, self.fail, self.shares = [], dict(saved or {}), set(fail), shares
        self.sandboxes, self.stamps, self.next_id, self.before_save = {}, {}, 1, before_save
        self.deleted = []

    def __call__(self, argv, **kw):
        assert argv[0] == "boat"
        args = argv[1:]
        self.calls.append(args)
        if args[:2] == ["snapshot", "shares"]:
            if self.shares:
                return self.shares
            return done('{"shares": []}') if args[2] in self.saved else done("", MISSING, 1)
        if args[0] == "new":
            assert "--no-env" in args, "a build sandbox never gets the account's secrets"
            source = args[args.index("--from") + 1] if "--from" in args else None
            if "new-timeout" in self.fail:
                raise subprocess.TimeoutExpired(argv, kw["timeout"])
            sandbox = f"sb{self.next_id}"
            self.next_id += 1
            self.sandboxes[sandbox] = source
            if source:
                self.stamps[sandbox] = self.saved[source]
                if "new-trial" in self.fail:
                    return done("", "no capacity", 1)
                if "new-trial-late" in self.fail:
                    return done(json.dumps({"event": "creating", "id": sandbox}), "boot failed", 1)
            return done(json.dumps({"event": "creating"}) + "\n" + json.dumps({"event": "ready", "id": sandbox}))
        if args[0] == "exec":
            sandbox, script = args[1], args[-1]
            if "cloud_machine base" in script:
                self.stamps[sandbox] = script.split("echo ", 1)[1].split(" |", 1)[0]
                return done(code=1 if "build" in self.fail else 0)
            if script == bs.TOKEN_HASHES:
                return done(BUILD_TOKEN + "\n")
            step = "check-trial" if self.sandboxes[sandbox] else "check-build"
            if step in self.fail:
                return done("check failed: this runner is enrolled\n", code=1)
            if f"= {self.stamps.get(sandbox)} ]" not in script:
                return done("check failed: the image is not the one this run built\n", code=1)
            if step == "check-trial":
                assert BUILD_TOKEN in script, "the trial checks the build's box token is gone"
            return done("check passed\n")
        if args[0] == "snapshot":
            if self.before_save:
                self.before_save(self, args[2])
            self.saved[args[2]] = self.stamps[args[1]]
            return done(code=1 if "save" in self.fail else 0)
        if args[0] == "delete":
            assert args[2] == "--yes"
            if f"delete-timeout:{args[1]}" in self.fail:
                raise subprocess.TimeoutExpired(argv, kw["timeout"])
            self.deleted.append(args[1])
            return done()
        raise AssertionError(f"unexpected boat call {args}")

    def steps(self):
        return [" ".join(a[:2]) if a[0] == "snapshot" else a[0] for a in self.calls]


def done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def run(fake, *extra, release=RELEASE, name=STEM):
    said = []
    code = bs.main(["--release", release, "--name", name, *extra], boat=bs.Boat(run=fake, say=said.append))
    return code, "\n".join(said)


def published(fake):
    return [n for n in fake.saved if n.startswith(STEM + "-")]


def test_builds_checks_saves_under_a_new_name_then_checks_what_was_saved():
    fake = FakeBoat(saved={"tico-runner-base": "old"})
    code, said = run(fake)
    assert code == 0
    assert fake.steps() == [
        "snapshot shares",
        "new",
        "exec",  # the box token's hash, before base takes the line out
        "exec",  # the base step
        "exec",  # the check on the build sandbox
        "snapshot shares",  # the name is looked at again right before the save
        "snapshot sb1",
        "new",  # from the saved snapshot
        "exec",  # the same check on it
        "delete",
        "delete",
    ]
    [name] = published(fake)
    assert len(name) <= 63 and name[len(STEM) + 1 :].isalnum()
    assert fake.sandboxes == {"sb1": None, "sb2": name}
    assert fake.deleted == ["sb1", "sb2"]
    assert fake.saved["tico-runner-base"] == "old"  # the snapshot HQ uses now is untouched
    build = next(a[-1] for a in fake.calls if a[0] == "exec" and "cloud_machine base" in a[-1])
    assert f"--branch {RELEASE}" in build and "cloud_machine base --user user" in build
    assert f"HQ_BOAT_BASE_SNAPSHOT={name}" in said and "setting it back" in said


def test_two_runs_with_the_same_name_never_publish_over_each_other():
    """boat replaces on a save. A second run that saves between the first one's last look and its save lands
    somewhere else, and each run's check proves its snapshot is the one it built."""
    fake = FakeBoat()

    def other_run(fake, _name):
        fake.before_save = None
        assert run(fake)[0] == 0

    fake.before_save = other_run
    code, _ = run(fake)
    assert code == 0
    names = published(fake)
    assert len(names) == 2
    assert len(set(fake.saved[n] for n in names)) == 2  # two images, neither replaced


def test_a_replaced_snapshot_fails_its_check_and_says_not_to_use_it(capsys):
    """Another save landing on this run's name between its save and its trial: the stamp no longer matches."""
    fake = FakeBoat()
    fake.before_save = lambda f, _name: f.stamps.update(sb1="someone else's")
    code, _ = run(fake)
    assert code == 1
    assert "the image is not the one this run built" in capsys.readouterr().err
    assert fake.deleted == ["sb1", "sb2"]


def test_a_name_that_exists_is_never_replaced(monkeypatch):
    monkeypatch.setattr(bs, "published_name", lambda stem: f"{stem}-00000000")
    fake = FakeBoat(saved={f"{STEM}-00000000": "theirs"})
    code, _ = run(fake)
    assert code == 1
    assert fake.steps() == ["snapshot shares"]  # no sandbox, no save
    assert fake.saved == {f"{STEM}-00000000": "theirs"}


def test_a_name_taken_during_the_build_stops_before_the_save(monkeypatch):
    monkeypatch.setattr(bs, "published_name", lambda stem: f"{stem}-00000000")
    fake = FakeBoat()
    real = fake.__call__
    looks = []

    def take_it(argv, **kw):
        if argv[1:3] == ["snapshot", "shares"]:
            looks.append(1)
            if len(looks) == 2:
                fake.saved[argv[3]] = "theirs"
        return real(argv, **kw)

    code = bs.main(["--release", RELEASE, "--name", STEM], boat=bs.Boat(run=take_it, say=lambda _: None))
    assert code == 1
    assert fake.saved == {f"{STEM}-00000000": "theirs"}
    assert fake.deleted == ["sb1"]


@pytest.mark.parametrize(
    "answer",
    [
        done("", json.dumps({"code": "unauthorized", "status": 401}), 1),
        done("", json.dumps({"code": "sandbox_missing", "status": 404}), 1),  # a 404 for something else
        done("", "Bad gateway", 1),
    ],
)
def test_an_unclear_answer_about_the_name_is_refused(answer):
    fake = FakeBoat(shares=answer)
    code, _ = run(fake)
    assert code == 1
    assert fake.steps() == ["snapshot shares"]


@pytest.mark.parametrize(
    "release,name",
    [
        ("0.6.81", STEM),
        ("v0.6", STEM),
        ("main", STEM),
        (RELEASE, "tico-runner-base-0.6.81"),
        (RELEASE, "Tico Base"),
        (RELEASE, "a" * 55),  # no room for the suffix
    ],
)
def test_bad_arguments_are_refused_before_boat(release, name):
    fake = FakeBoat()
    code, _ = run(fake, release=release, name=name)
    assert code == 1
    assert fake.calls == []


@pytest.mark.parametrize("step", ["build", "check-build", "save"])
def test_a_failure_before_the_trial_publishes_nothing_usable_and_cleans_up(step):
    fake = FakeBoat(fail={step})
    code, _ = run(fake)
    assert code == 1
    assert fake.steps().count("new") == 1  # never a sandbox from a snapshot that was not saved
    assert fake.deleted == ["sb1"]
    if step != "save":
        assert published(fake) == []


@pytest.mark.parametrize("step", ["check-trial", "new-trial", "new-trial-late"])
def test_a_saved_snapshot_that_fails_its_check_is_named_as_unusable(step, capsys):
    fake = FakeBoat(fail={step})
    code, _ = run(fake)
    assert code == 1
    [name] = published(fake)
    err = capsys.readouterr().err
    assert f"snapshot {name} was saved but failed its check, so do not point HQ at it" in err
    # A create that printed its id before failing is deleted too.
    assert fake.deleted == (["sb1"] if step == "new-trial" else ["sb1", "sb2"])


def test_a_delete_that_times_out_does_not_stop_the_next_and_is_named(capsys):
    fake = FakeBoat(fail={"delete-timeout:sb1", "check-trial"})
    code, said = run(fake)
    assert code == 1
    assert fake.deleted == ["sb2"]  # tried on its own after sb1's timed out
    assert "sandbox sb1 was not deleted and still costs money: boat delete sb1 --yes" in said
    err = capsys.readouterr().err
    assert "failed its check" in err and "Traceback" not in err  # the run's own error is the one reported


def test_a_create_that_times_out_says_where_to_look(capsys):
    fake = FakeBoat(fail={"new-timeout"})
    code, _ = run(fake)
    assert code == 1
    assert "look for a sandbox named" in capsys.readouterr().err
    assert fake.deleted == []


def test_keep_leaves_the_sandboxes():
    fake = FakeBoat()
    code, said = run(fake, "--keep")
    assert code == 0
    assert "delete" not in fake.steps()
    assert "kept sandbox sb1" in said and "kept sandbox sb2" in said


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize(
    "script",
    [
        bs.build_script(RELEASE, "ab12"),
        bs.check_script(RELEASE, "ab12"),
        bs.check_script(RELEASE, "ab12", settle_s=5, build_token_hashes=[BUILD_TOKEN]),
        bs.TOKEN_HASHES,
    ],
)
def test_the_sandbox_scripts_parse(script):
    assert subprocess.run(["bash", "-n", "-c", script]).returncode == 0


@pytest.mark.skipif(not shutil.which("getent"), reason="needs getent")
def test_the_check_fails_plainly_for_a_missing_user():
    p = subprocess.run(
        ["bash", "-c", bs.check_script(RELEASE, "ab12", user="no-such-user-acme")], capture_output=True, text=True
    )
    assert p.returncode == 1
    assert p.stdout.strip() == "check failed: there is no user no-such-user-acme with a home directory"


def test_the_trial_check_compares_token_hashes_never_values(tmp_path):
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("export ASCII_TOKEN=box-secret\n")
    hashes = subprocess.run(
        ["bash", "-c", bs.TOKEN_HASHES.replace("~", str(tmp_path))], capture_output=True, text=True
    ).stdout.split()
    assert len(hashes) == 1 and "box-secret" not in hashes[0]
    script = bs.check_script(RELEASE, "ab12", build_token_hashes=hashes)
    assert hashes[0] in script and "box-secret" not in script
