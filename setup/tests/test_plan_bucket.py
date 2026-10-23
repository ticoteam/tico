"""--plan-bucket (Tico's hosted servers): a starter plan too big for the env's 8 KB parameter goes to the server by S3,
one object per install at plans/<name>/<sha256>; the server copies it to a file at first boot, checks its sha256, and
setup deletes the object once the server is up. Fake AWS only, plus the copy step run here with a stand-in `aws`."""
import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from backend import company_file as C
from backend.company_file import starter
from setup import aws, contract, envfile, remote, settings, wizard
from setup.tests.test_preview_flags import CREATE, NAME, env, go, world  # noqa: F401 (env: its secrets, autouse)
from setup.ui import MissingInput

BUCKET = "tico-preview-planbucket-abc123"


class FakeS3:
    def __init__(self, region, log, fail=None):
        self.region, self.log, self.fail = region, log, fail

    def put_object(self, **kw):
        self.log.append(("put_object", kw))

    def delete_object(self, **kw):
        self.log.append(("delete_object", kw))
        if self.fail:
            raise self.fail


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """A whole 300-seat chart: far over the env's 8 KB and over what one env string may hold once it grows."""
    from backend.tests.test_company_file import chart
    draft = chart(300)
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    path = tmp_path_factory.mktemp("plan") / "tico.company.yaml"
    path.write_text(C.to_yaml(draft))
    return path


def hosted(path, *extra, fail=None):
    d, ec2, iam, ssm, r53 = world()
    log, regions = [], []

    def s3(region, profile):
        regions.append(region)
        log.append(("instances so far", len(ec2.called("run_instances"))))
        return FakeS3(region, log, fail)
    d.aws_s3 = s3
    code, out = go([*CREATE, "--company-file", str(path), *extra], d)
    return code, out, ec2, iam, ssm, log, regions


def test_a_300_seat_chart_reaches_the_server_by_the_plan_bucket_and_never_by_its_env(big):
    code, out, ec2, iam, ssm, log, regions = hosted(big, "--plan-bucket", BUCKET, "--plan-bucket-region", "us-west-2")
    assert code == 0, out
    assert regions == ["us-west-2"]
    calls = [name for name, _ in log]
    assert calls == ["instances so far", "put_object", "delete_object"] and log[0][1] == 0     # put before the launch
    put, deleted = log[1][1], log[2][1]
    body = put["Body"].decode()
    sha = hashlib.sha256(put["Body"]).hexdigest()
    assert put["Bucket"] == BUCKET and put["Key"] == f"plans/{NAME}/{sha}"
    assert deleted == {"Bucket": BUCKET, "Key": put["Key"]}                                     # gone once it is up
    assert len(body) > starter.ENV_PACKED and len(starter.unpack(body)["bots"]) == 300
    (param,) = ssm.called("put_parameter")
    env = envfile.parse(param["Value"])
    assert "TICO_COMPANY_FILE_B64" not in env and "Tier" not in param and len(param["Value"]) < 4096
    assert env["TICO_COMPANY_FILE_S3"] == f"s3://{BUCKET}/{put['Key']}" and env["TICO_COMPANY_FILE_SHA256"] == sha
    assert env["TICO_COMPANY_FILE_S3_REGION"] == "us-west-2" and env["TICO_SAMPLE_COMPANY"] == "off"
    assert env["TICO_COMPANY_FILE"] == "/run/tico/company-file/plan.b64"
    # The server's own role reads its own plan only; the boundary limits, it never grants.
    (inline,) = iam.called("put_role_policy")
    statements = json.loads(inline["PolicyDocument"])["Statement"]
    assert {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{BUCKET}/plans/{NAME}/*"} in statements
    user_data = ec2.called("run_instances")[0]["UserData"]
    assert body not in user_data + out and "aws s3 cp" in user_data


def test_the_retry_keeps_the_plan_on_this_computer_and_puts_it_again(big, monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_SETUP_HOME", str(tmp_path))
    from setup import state
    code, out, *_ = hosted(big, "--plan-bucket", BUCKET)
    assert code == 0, out
    saved, env_text = state.load(f"{NAME}.preview.example")
    resumed = settings.from_saved(saved, env_text)
    assert resumed.plan_bucket == BUCKET and len(starter.unpack(resumed.company_file_data)["bots"]) == 300
    assert envfile.parse(envfile.render(resumed.to_env()))["TICO_COMPANY_FILE_S3"].startswith(f"s3://{BUCKET}/plans/{NAME}/")


def test_a_new_owner_link_keeps_the_plan_on_this_computer(big, monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_SETUP_HOME", str(tmp_path))
    from setup import state
    d, *_ = world()
    d.aws_s3 = lambda region, profile: FakeS3(region, [])
    assert go([*CREATE, "--company-file", str(big), "--plan-bucket", BUCKET], d)[0] == 0
    code, out = go(["owner-link", "--domain", f"{NAME}.preview.example"], d)
    assert code == 0, out
    resumed = settings.from_saved(*state.load(f"{NAME}.preview.example"))
    assert resumed.plan_bucket == BUCKET and len(starter.unpack(resumed.company_file_data)["bots"]) == 300


def test_a_server_set_up_before_the_plan_bucket_gets_the_read_of_its_plan_on_reuse(big):
    d, ec2, iam, ssm, r53 = world()
    assert go(CREATE, d)[0] == 0
    d.aws_s3 = lambda region, profile: FakeS3(region, [])
    code, out = go([*CREATE, "--company-file", str(big), "--plan-bucket", BUCKET], d)
    assert code == 0, out
    assert len(ec2.called("run_instances")) == 1 and "reusing it" in out
    # Its pull-env.sh may predate the copy step, so the re-run copies the plan itself before Compose starts.
    (refresh,) = [c for c in ssm.commands if "pull-env.sh" in c and "docker compose up" in c]
    assert refresh.index("pull-env.sh") < refresh.index("aws s3 cp") < refresh.index("docker compose up")
    statements = json.loads(iam.called("put_role_policy")[-1]["PolicyDocument"])["Statement"]
    assert {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{BUCKET}/plans/{NAME}/*"} in statements


# A compose.yaml from before the plan bucket: no TICO_COMPANY_FILE and no folder mounted for it.
OLD_COMPOSE = b"name: tico\nservices:\n  server:\n    image: ghcr.io/ticoteam/tico:${TICO_TAG:-latest}\n    environment:\n" \
              b"      TICO_COMPANY_FILE_B64:\n"


def answer(ssm, rule):
    """Each SSM command gets rule(command) -> (code, stdout, stderr)."""
    def invocation(**kw):
        code, out, err = rule(ssm.commands[-1])
        return {"Status": "Success" if code == 0 else "Failed", "ResponseCode": code, "StandardOutputContent": out,
                "StandardErrorContent": err}
    ssm.get_command_invocation = invocation


def test_a_server_from_before_the_plan_bucket_gets_this_compose_and_image_before_its_plan_is_deleted(big):
    d, ec2, iam, ssm, r53 = world()
    d.compose_local = lambda: OLD_COMPOSE
    assert go(CREATE, d)[0] == 0
    new = wizard._local_compose()
    assert b"TICO_COMPANY_FILE:" in new and b"/run/tico/company-file:ro" in new
    d.compose_local = lambda: new
    log, sent = [], len(ssm.commands)
    d.aws_s3 = lambda region, profile: FakeS3(region, log)
    code, out = go([*CREATE, "--company-file", str(big), "--plan-bucket", BUCKET], d)
    assert code == 0, out
    (refresh,) = [c for c in ssm.commands[sent:] if "pull-env.sh" in c]
    written = re.search(r"echo (\S+) \| base64 -d \| gunzip > \S+/compose\.yaml", refresh)
    assert written and gzip.decompress(base64.b64decode(written.group(1))) == new
    # This compose, then the env and the plan, then this release's image, which must read the plan before it starts and
    # once it runs.
    steps = [refresh.index(s) for s in (written.group(0), "pull-env.sh", "aws s3 cp", "docker compose pull",
                                        "docker compose run", "docker compose up", "docker compose exec")]
    assert steps == sorted(steps)
    assert [name for name, _ in log] == ["put_object", "delete_object"]


def test_a_re_run_that_cannot_copy_check_or_start_the_plan_keeps_it_in_the_bucket_and_says_why(big, monkeypatch, tmp_path):
    for n, line in enumerate(("tico-setup: FAILED: the starter plan from s3://b/k has sha256 x, not y; not starting Tico",
                              "tico-setup: FAILED: the running app does not read the starter plan")):
        monkeypatch.setenv("TICO_SETUP_HOME", str(tmp_path / str(n)))
        d, ec2, iam, ssm, r53 = world()
        assert go(CREATE, d)[0] == 0
        log = []
        d.aws_s3 = lambda region, profile: FakeS3(region, log)
        answer(ssm, lambda c: (3, "", line) if "pull-env.sh" in c else (0, "", ""))
        code, out = go([*CREATE, "--company-file", str(big), "--plan-bucket", BUCKET], d)
        assert code != 0 and line in out
        assert [name for name, _ in log] == ["put_object"]


def test_without_a_compose_file_beside_setup_a_re_run_takes_a_pinned_commit_but_not_a_branch(big, monkeypatch, tmp_path):
    sha = "0123456789abcdef0123456789abcdef01234567"
    for n, (ref, pinned) in enumerate(((sha, True), ("main", False), (sha[:12], False))):
        monkeypatch.setenv("TICO_SETUP_HOME", str(tmp_path / str(n)))
        d, ec2, iam, ssm, r53 = world()
        assert go(CREATE, d)[0] == 0
        d.compose_local = lambda: None
        log, sent = [], len(ssm.commands)
        d.aws_s3 = lambda region, profile: FakeS3(region, log)
        code, out = go([*CREATE, "--company-file", str(big), "--plan-bucket", BUCKET, "--compose-ref", ref], d)
        refresh = [c for c in ssm.commands[sent:] if "pull-env.sh" in c]
        if pinned:
            assert code == 0, out
            assert contract.compose_url(sha) in refresh[0] and [name for name, _ in log] == ["put_object", "delete_object"]
        else:
            assert code != 0 and not refresh and [name for name, _ in log] == ["put_object"]


def test_a_re_run_finishes_a_first_boot_that_could_not_copy_its_plan(big):
    d, ec2, iam, ssm, r53 = world()
    log = []
    d.aws_s3 = lambda region, profile: FakeS3(region, log)
    failed = "tico-setup: FAILED: cannot copy the starter plan from s3://b/k"

    def first_boot(c, fixed=False):
        if c.startswith("test -f"):
            return 1, "", ""                                    # no .bootstrapped
        if "grep" in c and "pull-env.sh" not in c:
            return 0, failed, ""                                # its log ends with the failure
        return (0, "", "") if fixed else (4, "", failed)
    answer(ssm, first_boot)
    code, out = go([*CREATE, "--company-file", str(big), "--plan-bucket", BUCKET], d)
    assert code != 0 and failed in out and "re-run" in out
    assert [name for name, _ in log] == ["put_object"]           # kept for the re-run
    # The cause is fixed (the role may read it now); the same command again finishes the boot instead of stopping at it.
    answer(ssm, lambda c: first_boot(c, fixed=True))
    sent = len(ssm.commands)
    code, out = go([*CREATE, "--company-file", str(big), "--plan-bucket", BUCKET], d)
    assert code == 0, out
    assert len(ec2.called("run_instances")) == 1
    (finish,) = [c for c in ssm.commands[sent:] if "pull-env.sh" in c]
    assert finish.index("aws s3 cp") < finish.index("docker compose up") < finish.index("touch ") and ".bootstrapped" in finish
    assert [name for name, _ in log] == ["put_object", "put_object", "delete_object"]


def test_a_re_run_never_takes_over_a_first_boot_that_is_still_running(tmp_path, monkeypatch):
    root = tmp_path / "opt"
    root.mkdir()
    monkeypatch.setattr(contract, "REMOTE_DIR", str(root))
    monkeypatch.setattr(remote, "BOOT_LOG", str(tmp_path / "tico-setup.log"))
    script = "\n".join(["set -eu", *remote.finish_boot_guard_lines(), "echo finishing"]) + "\n"

    def rerun(log: str):
        (tmp_path / "tico-setup.log").write_text(log)
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    running = rerun("tico-setup: start\n")
    assert running.returncode != 0 and "still running" in running.stderr and "finishing" not in running.stdout
    assert rerun("tico-setup: start\ntico-setup: FAILED: cannot copy the starter plan from s3://b/k\n").stdout == "finishing\n"
    (root / ".bootstrapped").touch()
    assert rerun("tico-setup: start\ntico-setup: done\n").stdout == "finishing\n"
    # wait_ready counts only a boot whose log ends in its failure as stopped, and keeps waiting on one still going.
    from setup.tests.fakes import FakeSSM
    ssm = FakeSSM()
    answer(ssm, lambda c: (1, "", ""))
    deployer = aws.Deployer(aws.plan_aws(domain="a.example", front_door="caddy", region="us-east-1", name=NAME),
                            None, None, ssm, "123456789012", sleep=lambda _: None)
    deployer.find_instance = lambda: {"InstanceId": "i-0abc"}
    assert deployer.wait_ready(timeout=0.05) is False and not deployer.boot_failed
    assert all("tail -n 1" in c for c in ssm.commands if "grep" in c)


def test_a_saved_plan_bucket_does_not_follow_a_re_run_off_aws(big):
    from setup import cli
    from setup.tests.fakes import CaptureIO
    args = cli.parser().parse_args(["--target", "ssh", "--ssh", "root@203.0.113.9", "--domain", "tico.acme.example",
                                    "--non-interactive", "--yes"])
    saved = lambda data: settings.Settings(target="aws", plan_bucket=BUCKET, plan_bucket_region="us-west-2",
                                           company_file_data=data)
    from backend.tests.test_company_file import chart
    draft = chart(3)
    draft["company"].update(name="Acme Finance", about="We help small firms manage their books.")
    small = starter.pack(C.to_yaml(draft), starter.ENV_PACKED)
    s = wizard.gather(CaptureIO(), args, saved(small), False)
    assert s.target == "ssh" and s.plan_bucket == s.plan_bucket_region == ""
    assert "TICO_COMPANY_FILE_S3" not in s.to_env() and s.to_env()["TICO_COMPANY_FILE_B64"] == small
    io = CaptureIO()
    s = wizard.gather(io, args, saved(wizard_pack(big)), False)               # the saved plan stays behind, said once
    assert s.company_file_data == "" and "TICO_COMPANY_FILE_B64" not in s.to_env() and "Use this team" in io.text
    args.company_file = "tico.company.yaml"                                   # one passed again is refused
    with pytest.raises(MissingInput, match="without --company-file"):
        wizard.gather(CaptureIO(), args, saved(wizard_pack(big)), False)


def wizard_pack(path):
    return starter.pack(path.read_text(), starter.MAX_PACKED)


def test_without_the_plan_bucket_a_chart_over_the_env_limit_is_refused_before_any_cloud_change(big):
    d, ec2, iam, ssm, r53 = world()
    code, out = go([*CREATE, "--company-file", str(big)], d)
    assert code != 0 and "without --company-file" in out
    assert not ssm.called("put_parameter") and not ec2.called("run_instances")


def test_a_301_seat_chart_gets_the_plain_300_error_even_by_the_plan_bucket(big, tmp_path):
    extra = "  - id: one-too-many\n    template: bookkeeping\n    title: One Too Many\n"
    over = tmp_path / "over.yaml"
    over.write_text(big.read_text().replace("employees:\n", "employees:\n" + extra, 1))
    with pytest.raises(MissingInput, match="more than 300 entries"):
        wizard.gather_company_file(argparse.Namespace(company_file=str(over), company=None, plan_bucket=BUCKET),
                                   settings.Settings(preset="recommended"))


def test_the_plan_bucket_is_for_aws_only_and_takes_bucket_and_region_names(big):
    d, *_ = world()
    for argv, said in ((["--plan-bucket", "Not_A_Bucket"], "S3 bucket name"),
                       (["--plan-bucket", BUCKET, "--plan-bucket-region", "west"], "AWS region")):
        code, out = go([*CREATE, "--company-file", str(big), *argv], d)
        assert code != 0 and said in out
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.9", "--domain", "tico.example.com", "--plan-bucket", BUCKET,
                    "--non-interactive", "--yes"], d)
    assert code != 0 and "for a server on AWS" in out


def test_a_plan_the_bucket_will_not_delete_leaves_the_working_server_up_and_says_so(big):
    code, out, *_ = hosted(big, "--plan-bucket", BUCKET, fail=ClientError("AccessDenied"))
    assert code == 0, out
    assert "Could not delete the starter plan from the plan bucket (AccessDenied)" in out
    deployer = aws.Deployer(aws.plan_aws(domain="a.example", front_door="caddy", region="us-east-1", name=NAME,
                                         plan_bucket=BUCKET), None, None, None, "123456789012")
    with pytest.raises(ValueError):
        deployer.put_plan(FakeS3("us-west-2", []), "plans/pv-other/x", "data")


def test_a_boot_that_stopped_itself_ends_the_wait_with_its_line():
    from setup.tests.fakes import FakeSSM

    class Stopped(FakeSSM):
        def get_command_invocation(self, **kw):
            line = "tico-setup: FAILED: the starter plan from s3://b/k has sha256 x, not y; not starting Tico"
            if "grep" in self.commands[-1]:
                return {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": line, "StandardErrorContent": ""}
            return {"Status": "Failed", "ResponseCode": 1, "StandardOutputContent": "", "StandardErrorContent": ""}
    said = []
    deployer = aws.Deployer(aws.plan_aws(domain="a.example", front_door="caddy", region="us-east-1", name=NAME),
                            None, None, Stopped(), "123456789012", say=said.append, sleep=lambda _: None)
    deployer.find_instance = lambda: {"InstanceId": "i-0abc"}
    assert deployer.wait_ready(timeout=5) is False and "not starting Tico" in said[-1]


# --- the copy at first boot, run here: pull-env.sh's plan lines with a stand-in `aws` and `chown` -----------------------

def boot(tmp_path, monkeypatch, plan: bytes, sha: str, existing: bytes | None = None, fail: str = ""):
    root = tmp_path / "opt"
    monkeypatch.setattr(contract, "REMOTE_DIR", str(root))
    bin_dir, source = tmp_path / "bin", tmp_path / "bucket-object"
    bin_dir.mkdir()
    source.write_bytes(plan)
    # aws s3 cp --only-show-errors --region R SRC DST: the bucket object is `source`; it logs each call.
    (bin_dir / "aws").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/aws.log\n'
                                 'f="$TICO_TEST_AWS_FAIL_ONCE"; if [ -n "$f" ] && [ ! -e "$f" ]; then : > "$f"; exit 1; fi\n'
                                 f'cp {source} "$7"\n')
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "chown").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/chown.log\n')
    if fail:
        (bin_dir / fail).write_text("#!/bin/sh\nexit 1\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    folder = root / contract.COMPANY_FILE_DIR
    folder.mkdir(parents=True)
    if existing is not None:
        (folder / contract.COMPANY_FILE_NAME).write_bytes(existing)
    (root / ".env").write_text(f"TICO_COMPANY_FILE_S3=s3://{BUCKET}/plans/{NAME}/{sha}\nTICO_COMPANY_FILE_S3_REGION=us-west-2\n"
                               f"TICO_COMPANY_FILE_SHA256={sha}\n")
    script = "set -eu\numask 077\n" + "\n".join(remote.plan_copy_lines()) + "\n"
    run = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"})
    calls = (tmp_path / "aws.log").read_text() if (tmp_path / "aws.log").exists() else ""
    return run, folder / contract.COMPANY_FILE_NAME, calls


def test_the_server_copies_the_plan_once_private_and_read_only(tmp_path, monkeypatch):
    plan = b"H4sIAAAAAAAA/plan"
    run, copy, calls = boot(tmp_path, monkeypatch, plan, hashlib.sha256(plan).hexdigest())
    assert run.returncode == 0, run.stderr
    assert copy.read_bytes() == plan and stat.S_IMODE(copy.stat().st_mode) == 0o400
    assert (tmp_path / "chown.log").read_text().split() == ["10001:10001", str(copy) + ".new"]
    assert calls.split() == ["s3", "cp", "--only-show-errors", "--region", "us-west-2", f"s3://{BUCKET}/plans/{NAME}/"
                             + hashlib.sha256(plan).hexdigest(), str(copy) + ".new"]


def test_the_copy_waits_out_a_role_that_is_not_allowed_yet(tmp_path, monkeypatch):
    plan = b"H4sIAAAAAAAA/plan"
    monkeypatch.setenv("TICO_TEST_AWS_FAIL_ONCE", str(tmp_path / "failed-once"))
    run, copy, calls = boot(tmp_path, monkeypatch, plan, hashlib.sha256(plan).hexdigest())
    assert run.returncode == 0, run.stderr
    assert copy.read_bytes() == plan and calls.count("s3 cp") == 2


def test_a_sha256_mismatch_fails_the_boot_loudly_and_leaves_no_plan(tmp_path, monkeypatch):
    run, copy, _ = boot(tmp_path, monkeypatch, b"tampered", "0" * 64)
    assert run.returncode == 3 and "FAILED: the starter plan" in run.stderr and "not starting Tico" in run.stderr
    assert not copy.exists() and not Path(str(copy) + ".new").exists()


def test_a_copy_already_in_place_is_kept_once_the_bucket_object_is_gone(tmp_path, monkeypatch):
    plan = b"H4sIAAAAAAAA/plan"
    run, copy, calls = boot(tmp_path, monkeypatch, b"never fetched", hashlib.sha256(plan).hexdigest(), existing=plan)
    assert run.returncode == 0, run.stderr
    assert copy.read_bytes() == plan and calls == ""


def test_the_re_run_script_finishes_a_stopped_boot_only_once_the_app_reads_its_plan(tmp_path, monkeypatch):
    old_plan, plan = b"H4sIAAAAAAAA/old-plan", b"H4sIAAAAAAAA/plan"
    sha = hashlib.sha256(plan).hexdigest()
    root, bin_dir, log = tmp_path / "opt", tmp_path / "bin", tmp_path / "tico-setup.log"
    monkeypatch.setattr(contract, "REMOTE_DIR", str(root))
    monkeypatch.setattr(remote, "BOOT_LOG", str(log))
    root.mkdir()
    bin_dir.mkdir()
    (tmp_path / "object").write_bytes(plan)
    (root / "pull-env.sh").write_text(f"#!/bin/sh\nprintf 'TICO_COMPANY_FILE_S3=s3://{BUCKET}/k\\nTICO_COMPANY_FILE_S3_REGION=us-west-2\\n"
                                      f"TICO_COMPANY_FILE_SHA256={sha}\\n' > {root}/.env\n")
    # `docker compose run|exec ... -c CODE SHA` runs CODE with this checkout's app reader against the mounted folder;
    # an image from before TICO_COMPANY_FILE cannot.
    repo = Path(remote.__file__).resolve().parent.parent
    plan_file = root / contract.COMPANY_FILE_DIR / contract.COMPANY_FILE_NAME
    docker = (f'echo "$@" >> {tmp_path}/docker.log\ncase "$2" in run|exec)\n  [ -z "$TICO_TEST_OLD_IMAGE" ] || exit 1\n'
              f'  while [ "$1" != "-c" ]; do shift; done\n  cd {repo} && TICO_COMPANY_FILE={plan_file} exec {sys.executable} "$@";;\nesac')
    stubs = {"aws": f'cp {tmp_path}/object "$7"', "chown": '[ -z "$TICO_TEST_CHOWN_FAILS" ]', "sleep": ":", "docker": docker}
    for name, body in stubs.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
    for f in [*bin_dir.iterdir(), root / "pull-env.sh"]:
        f.chmod(0o755)
    script = remote.rerun_script(compose=b"name: tico\n", compose_url="", plan=True)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("TICO_")}

    def rerun(**extra):
        log.write_text("tico-setup: start\ntico-setup: FAILED: cannot copy the starter plan from s3://b/k\n")
        (tmp_path / "docker.log").write_text("")
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                              env={**clean, "PATH": f"{bin_dir}:{os.environ['PATH']}", **extra})

    def calls():
        return (tmp_path / "docker.log").read_text().splitlines()
    old = rerun(TICO_TEST_OLD_IMAGE="1")                          # the image ignores TICO_COMPANY_FILE
    assert old.returncode == 6 and "does not read the starter plan" in old.stderr
    assert "compose up -d" not in calls()                         # refused before it starts, not after
    assert not (root / ".bootstrapped").exists() and log.read_text().splitlines()[-1].startswith("tico-setup: FAILED")
    plan_file.unlink()
    plan_file.write_bytes(old_plan)                               # an older plan, and the new one cannot be made the server's
    held = rerun(TICO_TEST_CHOWN_FAILS="1")
    assert held.returncode != 0 and plan_file.read_bytes() == old_plan and "compose up -d" not in calls()
    done = rerun()
    assert done.returncode == 0, done.stderr
    assert (root / "compose.yaml").read_bytes() == b"name: tico\n" and plan_file.read_bytes() == plan
    assert (root / ".bootstrapped").exists() and "finished by a re-run" in log.read_text().splitlines()[-1]
    steps = [c.split()[1] for c in calls()]
    assert steps == ["pull", "run", "up", "exec"]


def test_a_staged_plan_that_cannot_be_owned_or_locked_stops_the_boot_and_keeps_the_plan_before_it(tmp_path, monkeypatch):
    old, new = b"H4sIAAAAAAAA/old-plan", b"H4sIAAAAAAAA/new-plan"
    for tool in ("chown", "chmod"):
        here = tmp_path / tool
        here.mkdir()
        run, copy, _ = boot(here, monkeypatch, new, hashlib.sha256(new).hexdigest(), existing=old, fail=tool)
        assert run.returncode != 0 and "tico-setup: FAILED" in run.stderr
        assert copy.read_bytes() == old and not Path(str(copy) + ".new").exists()


def test_the_app_side_check_passes_only_on_the_selected_plan(tmp_path):
    """remote.PLAN_READ as the server runs it: the app's own reader, checked against the plan's sha256."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"H4sIAAAAAAAA/plan-a")
    b.write_bytes(b"H4sIAAAAAAAA/plan-b")
    repo = Path(remote.__file__).resolve().parent.parent

    def check(path, sha, **extra):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("TICO_")}
        return subprocess.run([sys.executable, "-c", remote.PLAN_READ, sha], cwd=repo, capture_output=True,
                              env={**clean, "TICO_COMPANY_FILE": str(path), **extra}).returncode
    want = hashlib.sha256(b.read_bytes()).hexdigest()
    assert check(b, want) == 0
    assert check(a, want) != 0                                    # an older plan left in place
    assert check(tmp_path / "missing", want) != 0
    assert check(b, want, TICO_COMPANY_FILE_B64="H4sIAAAAAAAA/env-plan") != 0


def test_every_aws_boot_makes_the_folder_compose_mounts_even_without_a_plan():
    script = remote.bootstrap_script(os_family="ubuntu", domain="a.example", front_door="caddy", compose=None,
                                     compose_url="https://example.com/compose.yaml", env_param=("/tico/a/env", "us-east-1"))
    folder = f"{contract.REMOTE_DIR}/{contract.COMPANY_FILE_DIR}"
    assert f"mkdir -p {folder}" in script and f"chown 10001:10001 {folder}" in script and f"chmod 700 {folder}" in script
    assert script.index("pull-env.sh\n") < script.index("docker compose up")
