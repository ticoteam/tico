"""TICO_EXPECTED_AWS_ACCOUNT / TICO_EXPECTED_AWS_ROLE: the server and the Slack gateway refuse to start as another AWS
identity. STS is always stubbed here."""
import textwrap

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from backend import aws_identity
from backend.tests.test_replication import ROOT, prepare

ACCOUNT = "123456789012"
ASSUMED = f"arn:aws:sts::{ACCOUNT}:assumed-role/tico-server/i-0abc"


class FakeSTS:
    def __init__(self, account=ACCOUNT, arn=ASSUMED, error=None):
        self.identity, self.error, self.calls = {"Account": account, "Arn": arn}, error, 0

    def get_caller_identity(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.identity


def run(sts, **env):
    return aws_identity.check(env, client=lambda _: sts)


def test_unset_calls_nothing():
    sts = FakeSTS()
    assert run(sts) == (True, None) and sts.calls == 0
    assert run(sts, TICO_EXPECTED_AWS_ACCOUNT=" ") == (True, None) and sts.calls == 0


def test_matching_account_starts():
    ok, message = run(FakeSTS(), TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT)
    assert ok and ACCOUNT in message


def test_another_account_is_refused_naming_both():
    ok, message = run(FakeSTS(account="999999999999"), TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT)
    assert not ok and "999999999999" in message and ACCOUNT in message and "\n" not in message


@pytest.mark.parametrize("role", ["tico-server", f"arn:aws:iam::{ACCOUNT}:role/tico-server",
                                  f"arn:aws:iam::{ACCOUNT}:role/service/tico/tico-server"])
def test_role_by_name_or_arn_with_a_path(role):
    ok, message = run(FakeSTS(), TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT, TICO_EXPECTED_AWS_ROLE=role)
    assert ok and "tico-server" in message
    ok, _ = run(FakeSTS(arn=f"arn:aws:iam::{ACCOUNT}:role/tico-server"), TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT,
                TICO_EXPECTED_AWS_ROLE=role)
    assert ok


@pytest.mark.parametrize("arn", [f"arn:aws:sts::{ACCOUNT}:assumed-role/tico-admin/i-0abc",
                                 f"arn:aws:iam::{ACCOUNT}:user/tico-server"])
def test_another_role_or_a_user_is_refused(arn):
    ok, message = run(FakeSTS(arn=arn), TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT, TICO_EXPECTED_AWS_ROLE="tico-server")
    assert not ok and arn in message and "tico-server" in message


def test_no_credentials_or_no_answer_is_refused():
    ok, message = run(FakeSTS(error=NoCredentialsError()), TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT)
    assert not ok and "no AWS credentials" in message and ACCOUNT in message
    ok, message = run(FakeSTS(error=EndpointConnectionError(endpoint_url="https://sts.amazonaws.com")),
                      TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT)
    assert not ok and "could not confirm" in message
    denied = ClientError({"Error": {"Code": "InvalidClientTokenId", "Message": "bad"}}, "GetCallerIdentity")
    ok, message = run(FakeSTS(error=denied), TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT)
    assert not ok and "could not confirm" in message and "InvalidClientTokenId" in message


@pytest.mark.parametrize("env, needle", [
    ({"TICO_EXPECTED_AWS_ACCOUNT": "12345"}, "12-digit"),
    ({"TICO_EXPECTED_AWS_ACCOUNT": "1234-5678-9012"}, "12-digit"),
    ({"TICO_EXPECTED_AWS_ROLE": "tico-server"}, "TICO_EXPECTED_AWS_ACCOUNT"),
    ({"TICO_EXPECTED_AWS_ACCOUNT": ACCOUNT, "TICO_EXPECTED_AWS_ROLE": "bad role"}, "role name"),
    ({"TICO_EXPECTED_AWS_ACCOUNT": ACCOUNT, "TICO_EXPECTED_AWS_ROLE": "arn:aws:iam::999999999999:role/x"}, "999999999999"),
])
def test_bad_settings_are_refused_before_any_call(env, needle):
    sts = FakeSTS()
    ok, message = run(sts, **env)
    assert not ok and needle in message and sts.calls == 0


def test_the_client_has_short_timeouts(monkeypatch):
    import boto3
    seen = {}
    monkeypatch.setattr(boto3, "client", lambda service, **options: seen.update(service=service, **options))
    aws_identity.sts_client({})
    config = seen["config"]
    assert seen["service"] == "sts" and seen["region_name"] == "us-east-1"
    assert config.connect_timeout <= 10 and config.read_timeout <= 10 and config.retries["max_attempts"] <= 3


def test_a_mismatch_stops_prepare_before_the_volume_is_touched(tmp_path):
    fake = tmp_path / "fake" / "boto3"     # shadows boto3 for the entrypoint's python, so no real STS is reached
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(textwrap.dedent("""\
        class _STS:
            def get_caller_identity(self):
                return {"Account": "999999999999", "Arn": "arn:aws:sts::999999999999:assumed-role/other/s"}
        def client(service, **options):
            assert service == "sts"
            return _STS()
    """))
    result, data = prepare(tmp_path, TICO_EXPECTED_AWS_ACCOUNT=ACCOUNT, PYTHONPATH=f"{fake.parent}:{ROOT}",
                           AWS_CONFIG_FILE="/dev/null", AWS_SHARED_CREDENTIALS_FILE="/dev/null")
    assert result.returncode != 0
    assert f"refusing to start: AWS account is 999999999999, expected {ACCOUNT}" in result.stderr
    assert not (data / "hub.sqlite").exists()
