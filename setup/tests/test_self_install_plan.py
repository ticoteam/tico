"""Full self-install charts, using synthetic AWS and SSH only. No live account or server."""
import hashlib
import json
import stat
import subprocess

import pytest

from backend import company_file as C
from backend.company_file import starter
from setup import aws, contract, envfile, remote, settings, state
from setup.tests.fakes import FakeShell
from setup.tests.test_plan_bucket import ClientError, big  # noqa: F401
from setup.tests.test_preview_flags import CREATE, DOMAIN, NAME, env, go, world  # noqa: F401


def self_args():
    args = list(CREATE)
    for flag in ("--hosted", "--hosted-zone", "--preset", "--aws-permissions-boundary"):
        i = args.index(flag)
        del args[i:i + 2]
    return args


class PlanS3:
    def __init__(self):
        self.calls, self.exists, self.tagset = [], False, []

    def head_bucket(self, **kw):
        self.calls.append(("head_bucket", kw))
        if not self.exists:
            raise ClientError("404")

    def create_bucket(self, **kw):
        self.calls.append(("create_bucket", kw))
        assert not self.exists
        self.exists = True

    def put_bucket_tagging(self, **kw):
        self.calls.append(("put_bucket_tagging", kw))
        self.tagset = kw["Tagging"]["TagSet"]

    def get_bucket_tagging(self, **kw):
        self.calls.append(("get_bucket_tagging", kw))
        return {"TagSet": self.tagset}

    def __getattr__(self, name):
        def record(**kw):
            self.calls.append((name, kw))
        return record

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]


# The blank chart is the model every new install starts from: its seat count is whatever the catalog holds now.
MODEL_SEATS = len(C.Turn(C.blank()).finish()["bots"])


@pytest.mark.parametrize("seats", [MODEL_SEATS, 300])
def test_self_aws_carries_full_chart_without_a_bucket_flag_and_retries_without_the_file(seats, request, tmp_path):
    path = tmp_path / "model.yaml"
    if seats == 300:
        path.write_text(request.getfixturevalue("big").read_text())
    else:
        draft = C.Turn(C.blank()).finish()
        assert len(draft["bots"]) == seats  # the whole model chart, not a shortened test chart
        draft["company"]["name"] = "Acme Finance"
        path = tmp_path / "model-a.yaml"
        path.write_text(C.to_yaml(draft))
    d, ec2, iam, ssm, _ = world()
    s3 = PlanS3()
    d.aws_s3 = lambda region, profile: s3
    code, out = go([*self_args(), "--company-file", str(path)], d)
    assert code == 0, out
    (put,) = s3.called("put_object")
    assert len(starter.unpack(put["Body"].decode())["bots"]) == seats
    assert len(put["Body"]) > starter.ENV_PACKED
    assert put["Bucket"].startswith("tico-setup-plans-")
    (param,) = ssm.called("put_parameter")
    server_env = envfile.parse(param["Value"])
    assert "TICO_COMPANY_FILE_B64" not in server_env and len(param["Value"].encode()) < 4096
    assert server_env["TICO_COMPANY_FILE_SHA256"] == hashlib.sha256(put["Body"]).hexdigest()
    assert put["Body"].decode() not in out + ec2.called("run_instances")[0]["UserData"]
    saved, local_env = state.load(DOMAIN)
    assert saved["plan_bucket_managed"] and "company_file_data" not in saved
    assert settings.from_saved(saved, local_env).company_file_data.encode() == put["Body"]
    assert stat.S_IMODE((state.home() / DOMAIN / "env").stat().st_mode) == 0o600
    assert s3.called("delete_object") == [{"Bucket": put["Bucket"], "Key": put["Key"]}]
    config_names = [n for n, _ in s3.calls]
    assert config_names.index("put_bucket_lifecycle_configuration") < config_names.index("put_object")
    (acl,) = s3.called("put_public_access_block")
    assert all(acl["PublicAccessBlockConfiguration"].values())
    assert acl["ExpectedBucketOwner"] == "123456789012"
    (life,) = s3.called("put_bucket_lifecycle_configuration")
    assert life["LifecycleConfiguration"]["Rules"][0]["Expiration"] == {"Days": 1}
    (policy,) = iam.called("put_role_policy")
    assert json.loads(policy["PolicyDocument"])["Statement"][-1] == {
        "Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{put['Bucket']}/plans/{NAME}/*"}
    # The file can disappear after setup saves it; retries recover the full private chart and bucket.
    path.unlink()
    code, retry_out = go(self_args(), d)
    assert code == 0, retry_out
    assert len(s3.called("create_bucket")) == 1 and len(ec2.called("run_instances")) == 1
    assert s3.called("put_object") == [put, put]


@pytest.mark.parametrize("target", ["aws", "ssh"])
def test_301_seats_gets_the_plain_300_error_before_any_cloud_or_ssh_write(target, big, tmp_path):
    path = tmp_path / "over.yaml"
    path.write_text(big.read_text().replace("employees:\n", "employees:\n  - id: too-many\n    template: revenue-accountant\n    title: Extra\n", 1))
    d, ec2, _, ssm, _ = world()
    d.ssh_shell = lambda *a: pytest.fail("must validate before connecting")
    args = self_args() if target == "aws" else ["--target", "ssh", "--domain", DOMAIN]
    code, out = go([*args, "--company-file", str(path)], d)
    assert code != 0 and "more than 300 entries" in out
    assert "without --company-file" not in out and "8 KB" not in out
    assert not ssm.called("put_parameter") and not ec2.called("run_instances")


def test_ssh_carries_the_chart_on_stdin_with_private_env_and_saved_retry(big):
    d, *_ = world()
    shell = FakeShell()
    d.ssh_shell = lambda *a: shell
    args = ["--target", "ssh", "--ssh", "root@203.0.113.9", "--domain", DOMAIN, "--auth", "oidc",
            "--issuer", "https://accounts.example", "--client-id", "client", "--owner-email", "a@example.com",
            "--server-ip", "203.0.113.9", "--backup", "local", "--non-interactive", "--yes"]
    code, out = go([*args, "--company-file", str(big)], d)
    assert code == 0, out
    packed = starter.pack(big.read_text(), starter.MAX_PACKED)
    writes = [(cmd, data) for cmd, data in shell.calls if data == packed.encode()]
    assert len(writes) == 1
    cmd, _ = writes[0]
    assert "sha256sum" in cmd and "chown 10001:10001" in cmd and "chmod 600" in cmd
    assert packed not in out + "\n".join(shell.cmds())
    (env_write,) = [data.decode() for cmd, data in shell.calls if data and "/.env.new" in cmd]
    e = envfile.parse(env_write)
    assert "TICO_COMPANY_FILE_B64" not in e and "TICO_COMPANY_FILE_S3" not in e
    assert e["TICO_COMPANY_FILE_DIR"] == "/opt/tico/company-file"
    assert e["TICO_COMPANY_FILE"] == "/run/tico/company-file/plan.b64"
    (start,) = [cmd for cmd in shell.cmds() if "docker compose pull" in cmd]
    assert start.index("docker compose run") < start.index("docker compose up") < start.index("docker compose exec")
    code, retry_out = go(args, d)
    assert code == 0, retry_out
    assert len([data for _, data in shell.calls if data == packed.encode()]) == 2


@pytest.mark.parametrize("region", ["us-east-1", "us-west-2"])
def test_managed_bucket_region_privacy_and_no_unowned_adoption(region):
    d = aws.Deployer(aws.plan_aws(domain="a.example", front_door="caddy", region=region,
                                 name=NAME, plan_bucket="tico-setup-plans-test"), None, None, None, "123456789012")
    s3 = PlanS3()
    d.ensure_plan_bucket(s3, region)
    (create,) = s3.called("create_bucket")
    assert create == {"Bucket": "tico-setup-plans-test", **({} if region == "us-east-1" else {
        "CreateBucketConfiguration": {"LocationConstraint": region}})}
    assert s3.called("put_bucket_encryption")[0]["ServerSideEncryptionConfiguration"]["Rules"][0][
        "ApplyServerSideEncryptionByDefault"] == {"SSEAlgorithm": "AES256"}
    assert s3.called("put_bucket_ownership_controls")[0]["OwnershipControls"]["Rules"] == [{
        "ObjectOwnership": "BucketOwnerEnforced"}]
    s3.tagset = []
    before = len(s3.calls)
    with pytest.raises(SystemExit, match="refusing to reuse"):
        d.ensure_plan_bucket(s3, region)
    assert [n for n, _ in s3.calls[before:]] == ["head_bucket", "get_bucket_tagging"]


def test_bucket_access_denial_and_incomplete_privacy_never_upload_or_launch(big):
    for failing in ("head_bucket", "put_public_access_block", "put_bucket_lifecycle_configuration"):
        d, ec2, _, ssm, _ = world()
        s3 = PlanS3()
        def denied(**kw):
            raise ClientError("AccessDenied")
        setattr(s3, failing, denied)
        d.aws_s3 = lambda region, profile: s3
        code, out = go([*self_args(), "--company-file", str(big)], d)
        assert code != 0, out
        assert not s3.called("put_object") and not ec2.called("run_instances") and not ssm.called("put_parameter")
