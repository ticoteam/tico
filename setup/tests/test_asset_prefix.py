"""The words in every AWS name, tag and path setup makes. Without --asset-prefix they are today's `tico` names, and an
existing install must keep finding its own resources by them, so these pin each one."""

import json

from setup import aws, backup, route53, signin
from setup.tests import fakes
from setup.tests.fakes import FakeRoute53, aws_clients
from setup.tests.helpers.plan_bucket import ClientError
from setup.tests.helpers.wizard import BASE, go, secret_env  # noqa: F401 (secret_env: autouse)

ACCOUNT = "123456789012"


class S3:
    """Every S3 call, recorded; a bucket exists once it is created."""

    def __init__(self):
        self.calls, self.tagsets = [], {}

    def _rec(self, name, kw):
        self.calls.append((name, kw))

    def head_bucket(self, **kw):
        self._rec("head_bucket", kw)
        if kw["Bucket"] not in self.tagsets:
            raise ClientError("404")

    def create_bucket(self, **kw):
        self._rec("create_bucket", kw)
        self.tagsets[kw["Bucket"]] = []

    def put_bucket_tagging(self, **kw):
        self._rec("put_bucket_tagging", kw)
        self.tagsets[kw["Bucket"]] = kw["Tagging"]["TagSet"]

    def get_bucket_tagging(self, **kw):
        self._rec("get_bucket_tagging", kw)
        return {"TagSet": self.tagsets[kw["Bucket"]]}

    def __getattr__(self, name):
        return lambda **kw: self._rec(name, kw)

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]


class IAM(fakes.FakeIAM):
    """The backup user's calls too: a user exists once it is created."""

    def __init__(self, role_tags=None):
        super().__init__(role_tags)
        self.users = {}

    def get_user(self, UserName):
        if UserName not in self.users:
            raise self.exceptions.NoSuchEntityException()
        return {}

    def create_user(self, **kw):
        self._rec("create_user", **kw)
        self.users[kw["UserName"]] = kw["Tags"]

    def list_user_tags(self, UserName):
        return {"Tags": self.users[UserName]}

    def list_access_keys(self, UserName):
        return {"AccessKeyMetadata": []}

    def create_access_key(self, UserName):
        self._rec("create_access_key", UserName=UserName)
        return {"AccessKey": {"AccessKeyId": "AKIANEW", "SecretAccessKey": "s3cr3t"}}


def deployed(p):
    ec2, iam, ssm, _ = aws_clients()
    d = aws.Deployer(p, ec2, iam, ssm, ACCOUNT, say=lambda m: None, sleep=lambda s: None)
    d.put_env("TICO_DOMAIN=x\n")
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\n")
    return d, ec2, iam, ssm


def created_backup(prefix_kw=None):
    s3, iam = S3(), IAM()
    values = backup.create_s3(
        s3,
        iam,
        domain="tico.example.com",
        region="us-west-2",
        account_id=ACCOUNT,
        name="acme",
        say=lambda _: None,
        **(prefix_kw or {}),
    )
    return s3, iam, values


def test_the_default_server_names_tags_and_policies_are_todays():
    p = aws.plan_aws(domain="tico.example.com", front_door="caddy", region="us-east-1", name="acme")
    d, ec2, iam, ssm = deployed(p)
    tags = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": "acme"}]
    named = tags + [{"Key": "Name", "Value": "tico-acme"}]
    (sg,) = ec2.called("create_security_group")
    assert (sg["GroupName"], sg["Description"], sg["TagSpecifications"][0]["Tags"]) == (
        "tico-acme",
        "Tico server",
        named,
    )
    assert ec2.called("allocate_address")[0]["TagSpecifications"][0]["Tags"] == named
    (run,) = ec2.called("run_instances")
    assert [t["Tags"] for t in run["TagSpecifications"]] == [named, named, named]
    assert run["IamInstanceProfile"] == {"Name": "tico-acme"}
    (role,) = iam.called("create_role")
    assert (role["RoleName"], role["Tags"]) == ("tico-acme", tags)
    (inline,) = iam.called("put_role_policy")
    assert (inline["RoleName"], inline["PolicyName"]) == ("tico-acme", "tico-env")
    assert json.loads(inline["PolicyDocument"])["Statement"] == [
        {
            "Effect": "Allow",
            "Action": "ssm:GetParameter",
            "Resource": f"arn:aws:ssm:us-east-1:{ACCOUNT}:parameter/tico/acme/env",
        }
    ]
    assert iam.called("create_instance_profile") == [{"InstanceProfileName": "tico-acme", "Tags": tags}]
    assert iam.called("add_role_to_instance_profile") == [{"InstanceProfileName": "tico-acme", "RoleName": "tico-acme"}]
    assert d.param_name == "/tico/acme/env" and ssm.called("put_parameter")[0]["Name"] == "/tico/acme/env"
    assert ssm.called("add_tags_to_resource") == [
        {"ResourceType": "Parameter", "ResourceId": "/tico/acme/env", "Tags": tags}
    ]
    text = "\n".join(p.lines())
    for want in (
        "Security group tico-acme:",
        "IAM role tico-acme with AmazonSSMManagedInstanceCore",
        "SSM SecureString /tico/acme/env holds",
        "Tags on everything: ManagedBy=tico-setup, tico-setup-name=acme",
    ):
        assert want in text


def test_the_default_destroy_finds_and_deletes_todays_names():
    tags = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": "acme"}]
    ec2 = fakes.FakeEC2(extra_instances=[{"InstanceId": "i-1", "Tags": tags}])
    iam, ssm = fakes.FakeIAM(role_tags=tags), fakes.FakeSSM(param_tags=tags)
    found = aws.discover(ec2, iam, ssm, "acme")
    assert found == {"instance": ["i-1"], "iam-role": ["tico-acme"], "ssm-parameter": ["/tico/acme/env"]}
    aws.destroy(ec2, iam, ssm, found, say=lambda m: None, sleep=lambda s: None)
    assert iam.called("delete_role_policy") == [{"RoleName": "tico-acme", "PolicyName": "tico-env"}]
    assert iam.called("delete_instance_profile") == [{"InstanceProfileName": "tico-acme"}]


def test_the_default_backup_names_are_todays():
    s3, iam, values = created_backup()
    bucket = f"tico-backup-tico-example-com-{ACCOUNT}"
    assert s3.called("create_bucket")[0]["Bucket"] == bucket
    rules = s3.called("put_bucket_lifecycle_configuration")[0]["LifecycleConfiguration"]["Rules"]
    assert [(r["ID"], r["Filter"]) for r in rules] == [
        ("tico-backups", {"Prefix": ""}),
        ("litestream", {"Prefix": "tico/000"}),
    ]
    assert iam.called("create_user")[0]["UserName"] == "tico-backup-acme"
    (pol,) = iam.called("put_user_policy")
    assert (pol["UserName"], pol["PolicyName"]) == ("tico-backup-acme", "tico-backup")
    assert values["TICO_BACKUP_URL"] == f"s3://{bucket}/tico"
    assert s3.called("put_bucket_tagging")[0]["Tagging"]["TagSet"] == [
        {"Key": "ManagedBy", "Value": "tico-setup"},
        {"Key": "tico-setup-name", "Value": "acme"},
    ]


def test_the_default_route53_comments_are_todays():
    r53 = FakeRoute53("example.com")
    from setup.dns import Record

    route53.upsert(r53, "Z1", [Record("A", "tico.example.com", "203.0.113.9")])
    route53.delete(r53, "Z1", [{"Name": "tico.example.com.", "Type": "A"}])
    assert [c["ChangeBatch"]["Comment"] for c in r53.called("change_resource_record_sets")] == [
        "tico setup",
        "tico setup destroy",
    ]


def test_the_default_sign_in_client_name_is_tico():
    assert "Application type: Web application. Name it Tico." in signin.PROVIDERS["google"].steps
    assert signin.PROVIDERS["microsoft"].steps[0].startswith("Register an application named Tico;")


def test_the_default_wizard_plan_and_destroy_name_todays_resources():
    code, out = go(["--dry-run", "--target", "aws", "--front-door", "caddy", "--name", "acme", *BASE])
    assert code == 0, out
    for want in (
        "Security group tico-acme:",
        "IAM role tico-acme ",
        "SSM SecureString /tico/acme/env",
        "ManagedBy=tico-setup, tico-setup-name=acme",
        "S3 bucket tico-backup-tico-example-com-<account> in us-east-1",
        "to s3://tico-backup-tico-example-com-<account>/tico",
    ):
        assert want in out
    ec2, iam, ssm, factory = aws_clients()
    d = fakes.deps()
    d.aws_clients = factory
    code, out = go(["destroy", "--name", "acme", "--dry-run"], deps=d)
    assert code == 0 and "Nothing tagged ManagedBy=tico-setup, tico-setup-name=acme in us-east-1." in out


def test_the_default_runner_names_are_todays():
    code, out = go(
        [
            "runner",
            "--dry-run",
            "--non-interactive",
            "--cloud",
            "aws",
            "--server-url",
            "https://tico.example.com",
            "--label",
            "build-1",
        ]
    )
    assert code == 0, out
    for want in (
        "Security group tico-runner-build-1:",
        "IAM role tico-runner-build-1 ",
        "SSM SecureString /tico/runner-build-1/env",
        "ManagedBy=tico-setup, tico-setup-name=runner-build-1",
    ):
        assert want in out
