import json

import pytest

from setup import backup, dns, state
from setup.tests import fakes
from setup.tests.fakes import aws_clients
from setup.tests.test_wizard import BASE, SECRET, go, secret_env  # noqa: F401


class Missing(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self, exists=False):
        self.exists, self.calls = exists, []

    def head_bucket(self, Bucket):
        if not self.exists:
            raise Missing("404")

    def __getattr__(self, name):
        return lambda **kw: self.calls.append((name, kw))

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]


class Iam(fakes.FakeIAM):
    def __init__(self, user_tags=None, keys=()):
        super().__init__()
        self.user_tags, self.keys = user_tags, list(keys)

    def get_user(self, UserName):
        if self.user_tags is None:
            raise self.exceptions.NoSuchEntityException()
        return {}

    def list_user_tags(self, UserName):
        return {"Tags": self.user_tags}

    def list_access_keys(self, UserName):
        return {"AccessKeyMetadata": self.keys}

    def create_access_key(self, UserName):
        self._rec("create_access_key", UserName=UserName)
        return {"AccessKey": {"AccessKeyId": "AKIANEW", "SecretAccessKey": "s3cr3t"}}


OURS = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": "tico-example-com"}]


def make(s3=None, iam=None, region="us-west-2"):
    s3, iam = s3 or FakeS3(), iam or Iam()
    values = backup.create_s3(s3, iam, domain="tico.example.com", region=region, account_id="123456789012",
                              name="tico-example-com", say=lambda _: None)
    return s3, iam, values


def test_the_bucket_is_versioned_encrypted_private_and_expires_old_versions():
    s3, iam, values = make()
    (created,) = s3.called("create_bucket")
    assert created["Bucket"] == "tico-backup-tico-example-com-123456789012"
    assert created["CreateBucketConfiguration"] == {"LocationConstraint": "us-west-2"}
    assert s3.called("put_bucket_versioning")[0]["VersioningConfiguration"] == {"Status": "Enabled"}
    assert s3.called("put_bucket_encryption")[0]["ServerSideEncryptionConfiguration"]["Rules"][0]["ApplyServerSideEncryptionByDefault"] == {"SSEAlgorithm": "AES256"}
    assert all(s3.called("put_public_access_block")[0]["PublicAccessBlockConfiguration"].values())
    (rule,) = s3.called("put_bucket_lifecycle_configuration")[0]["LifecycleConfiguration"]["Rules"]
    assert rule["NoncurrentVersionExpiration"]["NoncurrentDays"] == 3
    assert rule["Expiration"] == {"ExpiredObjectDeleteMarker": True}
    assert rule["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 1}
    assert '"aws:SecureTransport": "false"' in s3.called("put_bucket_policy")[0]["Policy"]
    assert values == {"TICO_BACKUP_URL": "s3://tico-backup-tico-example-com-123456789012/tico", "TICO_BACKUP_REGION": "us-west-2",
                      "LITESTREAM_ACCESS_KEY_ID": "AKIANEW", "LITESTREAM_SECRET_ACCESS_KEY": "s3cr3t"}


def test_the_iam_user_can_only_reach_that_bucket_and_is_tagged():
    _, iam, _ = make()
    assert iam.called("create_user")[0]["Tags"] == OURS
    statements = json.loads(iam.called("put_user_policy")[0]["PolicyDocument"])["Statement"]
    resources = {r for st in statements for r in ([st["Resource"]] if isinstance(st["Resource"], str) else st["Resource"])}
    assert resources == {"arn:aws:s3:::tico-backup-tico-example-com-123456789012", "arn:aws:s3:::tico-backup-tico-example-com-123456789012/*"}
    objects = next(st for st in statements if st["Resource"].endswith("/*"))
    assert {"s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"} <= set(objects["Action"])
    assert not any("iam:" in a or a == "s3:*" for st in statements for a in st["Action"])


def test_a_bucket_someone_else_owns_is_an_error_not_a_silent_success():
    class Forbidden(FakeS3):
        def head_bucket(self, Bucket):
            raise Missing("403")
    with pytest.raises(RuntimeError, match="403"):
        make(Forbidden())


def wizard_deps(s3, iam):
    ec2, _, ssm, factory = aws_clients(iam=iam)
    d = fakes.deps()
    r = fakes.FakeResolver({("tico.example.com", dns.A): ["203.0.113.9"]}, {"example.com": fakes.NETLIFY_NS})
    d.resolver, d.public_resolvers, d.aws_clients, d.aws_s3 = r, (lambda: [("Google", r)]), factory, (lambda region, profile: s3)
    return d, ssm


def test_aws_setup_creates_backup_storage_and_writes_its_key_to_the_server_env_only():
    s3, iam = FakeS3(), Iam()
    d, ssm = wizard_deps(s3, iam)
    code, out = go(["--target", "aws", "--yes", "--front-door", "caddy", *BASE], deps=d)
    assert code == 0, out
    env = ssm.called("put_parameter")[0]["Value"]
    assert "TICO_BACKUP_URL=s3://tico-backup-tico-example-com-123456789012/tico" in env and "LITESTREAM_SECRET_ACCESS_KEY=s3cr3t" in env
    assert "s3cr3t" not in out and "s3cr3t" not in json.dumps(state.load("tico.example.com")[0])
    assert "Note: No off-server backup" not in out

