"""An AWS server updated in place (Update now, or scripts/install.sh on it) moves TICO_TAG in its .env only. A re-run of
setup rewrites the stored parameter and pull-env.sh copies it over that .env, so a re-run to change one setting must keep
the newer version the server runs, in the parameter too, and never put the old one back."""

import pytest

from setup import contract, envfile
from setup.tests.helpers.plan_bucket import big  # noqa: F401 (fixture)
from setup.tests.helpers.preview_flags import CREATE, env, go, world  # noqa: F401 (env: its secrets, autouse)


def answer(ssm, rule):
    """Each SSM command gets rule(command) -> (code, stdout, stderr)."""

    def invocation(**kw):
        code, out, err = rule(ssm.commands[-1])
        status = "Success" if code == 0 else "Failed"
        return {"Status": status, "ResponseCode": code, "StandardOutputContent": out, "StandardErrorContent": err}

    ssm.get_command_invocation = invocation


def server_runs(ssm, tag):
    """The server's .env says TICO_TAG=<tag>; every other command succeeds."""
    answer(ssm, lambda c: (0, f"{tag}\n", "") if "s/^TICO_TAG=//p" in c else (0, "", ""))


def stored_tag(ssm):
    return envfile.parse(ssm.called("put_parameter")[-1]["Value"]).get("TICO_TAG")


@pytest.mark.parametrize(
    "setup_version, updated_to",
    [
        ("v1.2.0", "v1.3.0"),
        ("v0.6.0-rc.16", "v0.6.0-rc.17"),
        ("v0.6.0-rc.9", "v0.6.0-rc.10"),
        ("v0.6.0-rc.17", "v0.6.0"),
    ],
)
def test_a_re_run_after_an_update_keeps_the_updated_version(monkeypatch, tmp_path, setup_version, updated_to):
    monkeypatch.setenv("TICO_SETUP_HOME", str(tmp_path))
    d, ec2, iam, ssm, r53 = world()
    assert go([*CREATE, "--tico-version", setup_version], d)[0] == 0
    assert stored_tag(ssm) == setup_version
    server_runs(ssm, updated_to)  # updated on the server since; the stored copy still says setup_version
    sent = len(ssm.commands)
    code, out = go([*CREATE, "--tico-version", setup_version], d)
    assert code == 0, out
    assert len(ec2.called("run_instances")) == 1
    assert stored_tag(ssm) == updated_to and f"updated to {updated_to}" in out
    # The version is read before the parameter is written, and the refresh (pull-env.sh) runs after it.
    cmds = ssm.commands[sent:]
    read = next(i for i, c in enumerate(cmds) if "s/^TICO_TAG=//p" in c)
    refresh = next(i for i, c in enumerate(cmds) if "pull-env.sh" in c)
    assert read < refresh
    # A later re-run, with no server answer, keeps it too: the saved install remembers it.
    server_runs(ssm, "")
    assert go([*CREATE], d)[0] == 0
    assert stored_tag(ssm) == updated_to


@pytest.mark.parametrize(
    "newer, running",
    [
        ("v1.4.0", "v1.3.0"),
        ("v1.4.0", "v1.4.0"),
        ("v1.4.0", "v1.4.0-rc.3"),
        ("v0.7.0-rc.1", "v0.6.0"),
        ("v0.6.0-rc.10", "v0.6.0-rc.9"),
        ("v1.4.0", "latest"),
        ("v1.4.0", ""),
    ],
)
def test_a_re_run_with_a_newer_setup_moves_the_server_on(newer, running):
    d, ec2, iam, ssm, r53 = world()
    assert go([*CREATE, "--tico-version", "v0.5.0"], d)[0] == 0
    server_runs(ssm, running)
    code, out = go([*CREATE, "--tico-version", newer], d)
    assert code == 0, out
    assert stored_tag(ssm) == newer


def test_a_server_that_cannot_be_read_keeps_this_setups_version():
    d, ec2, iam, ssm, r53 = world()
    assert go([*CREATE, "--tico-version", "v1.2.0"], d)[0] == 0
    answer(ssm, lambda c: (1, "", "no") if "s/^TICO_TAG=//p" in c else (0, "", ""))
    code, out = go([*CREATE, "--tico-version", "v1.2.0"], d)
    assert code == 0, out
    assert stored_tag(ssm) == "v1.2.0" and "Could not read the version" in out


def test_a_stopped_server_keeps_this_setups_version():
    d, ec2, iam, ssm, r53 = world()
    assert go([*CREATE, "--tico-version", "v1.2.0"], d)[0] == 0
    send = ssm.send_command

    def stopped(**kw):
        if "s/^TICO_TAG=//p" in kw["Parameters"]["commands"][0]:
            raise ssm.exceptions.InvalidInstanceId()
        return send(**kw)

    ssm.send_command = stopped
    code, out = go([*CREATE, "--tico-version", "v1.2.0"], d)
    assert code == 0, out
    assert stored_tag(ssm) == "v1.2.0" and "Could not read the version" in out


class FakeS3:
    def put_object(self, **kw):
        pass

    def delete_object(self, **kw):
        pass


def test_a_kept_version_brings_its_own_releases_compose_file(big):  # noqa: F811
    """With the plan bucket the re-run writes compose.yaml; the update had put the newer release's there, so the re-run
    fetches that release's, not this setup's older copy."""
    d, ec2, iam, ssm, r53 = world()
    assert go([*CREATE, "--tico-version", "v1.2.0"], d)[0] == 0
    d.aws_s3 = lambda region, profile: FakeS3()
    server_runs(ssm, "v1.3.0")
    sent = len(ssm.commands)
    code, out = go([*CREATE, "--tico-version", "v1.2.0", "--company-file", str(big), "--plan-bucket", "b-1"], d)
    assert code == 0, out
    (refresh,) = [c for c in ssm.commands[sent:] if "pull-env.sh" in c]
    assert contract.compose_url("v1.3.0") in refresh and "base64 -d | gunzip" not in refresh.split("pull-env.sh")[0]
