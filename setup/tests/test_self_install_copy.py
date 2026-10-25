"""Exercise the actual SSH write script under dash, with synthetic paths and a stand-in chown."""
import os
import stat
import subprocess

import pytest

from setup import contract, remote, settings
from setup.tests.fakes import FakeShell
from setup.tests.test_preview_flags import env  # noqa: F401 (synthetic install verification, autouse)


def test_ssh_plan_copy_is_atomic_private_and_refuses_corrupt_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "REMOTE_DIR", str(tmp_path / "tico"))
    folder = tmp_path / "tico" / contract.COMPANY_FILE_DIR
    folder.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    chown = fake_bin / "chown"
    chown.write_text("#!/bin/sh\nexit 0\n")
    chown.chmod(0o755)
    shell = FakeShell()
    steps = remote.ssh_steps(shell, env_text="", compose=b"", compose_url="", company_file_data="selected-plan")
    steps[0].run()
    steps[2].run()
    (cmd,) = [cmd for cmd, data in shell.calls if data == b"selected-plan"]
    def write(data):
        return subprocess.run(["/bin/dash", "-c", cmd], input=data, capture_output=True,
                              env={**os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"]})
    assert write(b"selected-plan").returncode == 0
    plan = folder / contract.COMPANY_FILE_NAME
    assert plan.read_bytes() == b"selected-plan" and stat.S_IMODE(plan.stat().st_mode) == 0o600
    assert write(b"corrupted-plan").returncode != 0
    assert plan.read_bytes() == b"selected-plan" and not plan.with_suffix(".b64.new").exists()


def test_ssh_plan_read_failure_cannot_start_an_older_image():
    shell = FakeShell({"docker compose run": remote.Result(6, "", "image cannot read the plan")})
    steps = remote.ssh_steps(shell, env_text="", compose=b"", compose_url="", company_file_data="selected-plan")
    with pytest.raises(remote.StepFailed, match="cannot read the plan"):
        steps[-1].run()


def test_a_saved_legacy_plan_moves_out_of_the_aws_environment_on_retry(monkeypatch):
    from setup.tests.test_preview_flags import DOMAIN, go, world
    from setup.tests.test_self_install_plan import PlanS3, self_args
    from setup.tests.test_company_file import chart
    from backend import company_file as C
    from backend.company_file import starter
    from setup import state, envfile
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "test-private-client-secret")
    draft = chart(20)
    draft["company"]["name"] = "Acme Books"
    data = starter.pack(C.to_yaml(draft))
    s = settings.Settings(target="aws", domain=DOMAIN, company_file_data=data)
    # This is the old state shape: no bucket and a chart saved inside the private env.
    state.save(DOMAIN, s.public(), envfile.render(s.to_env()))
    d, _, _, ssm, _ = world()
    s3 = PlanS3()
    d.aws_s3 = lambda region, profile: s3
    code, out = go(self_args(), d)
    assert code == 0, out
    assert s3.called("put_object")[0]["Body"] == data.encode()
    assert "TICO_COMPANY_FILE_B64" not in envfile.parse(ssm.called("put_parameter")[0]["Value"])
