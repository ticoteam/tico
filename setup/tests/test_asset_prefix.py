"""The words in every AWS name, tag and path setup makes. Without --asset-prefix they are today's `tico` names, and an
existing install must keep finding its own resources by them, so these pin each one. With --asset-prefix hub no AWS
name, tag, path, policy or bucket says tico; names on the server itself (/opt/tico, TICO_* settings) stay."""

import gzip
import json
import re

import pytest

from backend import company_file as C
from backend.tests.helpers.company_file import chart
from setup import aws, backup, dns, remote, route53, runner_setup, signin, state, wizard
from setup.settings import Settings
from setup.ui import MissingInput
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
        self.users = {}  # name: {"UserId", "Tags"}

    def get_user(self, UserName):
        if UserName not in self.users:
            raise self.exceptions.NoSuchEntityException()
        return {"User": {"UserName": UserName, "UserId": self.users[UserName]["UserId"]}}

    def create_user(self, **kw):
        self._rec("create_user", **kw)
        self.users[kw["UserName"]] = {"UserId": f"AIDA{kw['UserName']}", "Tags": kw["Tags"]}
        return {"User": {"UserName": kw["UserName"], "UserId": f"AIDA{kw['UserName']}"}}

    def list_user_tags(self, UserName):
        return {"Tags": self.users[UserName]["Tags"]}

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


# --- the two bugs fixed with the flag -------------------------------------------------------------------------------


def test_the_plan_names_the_backup_user_from_name_as_setup_creates_it():
    code, out = go(["--dry-run", "--target", "aws", "--front-door", "caddy", "--name", "acme", *BASE])
    assert code == 0, out
    assert "IAM user tico-backup-acme allowed" in out and "tico-backup-tico-example-com allowed" not in out


def test_backup_storage_takes_name_and_otherwise_the_saved_one():
    s3, iam = S3(), IAM()
    d = fakes.deps()
    d.aws_clients = lambda region, profile: (None, iam, None, ACCOUNT)
    d.aws_s3 = lambda region, profile: s3
    argv = ["backup-storage", "--domain", "tico.example.com", "--aws-region", "us-west-2"]
    assert go([*argv, "--name", "acme"], deps=d)[0] == 0
    assert go(argv, deps=d)[0] == 0  # its own saved name
    assert state.load_backup("tico.example.com")["name"] == "acme"
    state.save("other.example.com", {"domain": "other.example.com", "aws_name": "saved-name", "asset_prefix": "tico"})
    assert go(["backup-storage", "--domain", "other.example.com", "--aws-region", "us-west-2"], deps=d)[0] == 0
    assert [c["UserName"] for c in iam.called("create_user")] == ["tico-backup-acme", "tico-backup-saved-name"]
    code, out = go([*argv, "--name", "other"], deps=d)
    assert code == 2 and "tico.example.com is set up with --name acme" in out
    code, out = go([*argv, "--name", "Not_A_Name"], deps=d)
    assert code == 2 and "--name is 1 to 40" in out


def test_backup_storage_for_an_install_saved_before_the_flag_keeps_its_old_backup_user():
    # Before --asset-prefix, backup-storage always named its user after the domain, whatever --name the server had.
    s3, iam = S3(), IAM()
    d = fakes.deps()
    d.aws_clients = lambda region, profile: (None, iam, None, ACCOUNT)
    d.aws_s3 = lambda region, profile: s3
    state.save("old.example.com", {"domain": "old.example.com", "aws_name": "custom", "aws_instance_id": "i-1"})
    assert go(["backup-storage", "--domain", "old.example.com", "--aws-region", "us-west-2"], deps=d)[0] == 0
    assert [c["UserName"] for c in iam.called("create_user")] == ["tico-backup-old-example-com"]
    assert state.load_backup("old.example.com")["name"] == "old-example-com"


# --- --asset-prefix hub -------------------------------------------------------------------------------------------

HUB = aws.Names("hub")
TICO_TAGS = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": "hotmic"}]
HUB_TAGS = [{"Key": "ManagedBy", "Value": "hub-setup"}, {"Key": "hub-setup-name", "Value": "hotmic"}]


def test_the_prefix_is_checked_and_a_saved_one_wins():
    assert aws.asset_prefix(None, {}) == "tico" and aws.asset_prefix("hub", {}) == "hub"
    assert aws.asset_prefix(None, {"domain": "x"}) == "tico"  # saved before the flag existed: tico names
    assert aws.asset_prefix(None, {"asset_prefix": "hub"}) == aws.asset_prefix("hub", {"asset_prefix": "hub"}) == "hub"
    with pytest.raises(MissingInput, match="named with --asset-prefix hub, and it cannot change"):
        aws.asset_prefix("tico", {"asset_prefix": "hub"})
    with pytest.raises(MissingInput, match="named with --asset-prefix tico"):
        aws.asset_prefix("hub", {"domain": "x"})
    for ok in ("a", "hub", "acme-2", "abcdefghij"):
        assert aws.asset_prefix(ok, {}) == ok
    for bad in ("Hub", "2hub", "hub-", "-hub", "hub_x", "abcdefghijk", "h.b"):
        with pytest.raises(MissingInput, match="1 to 10 lowercase"):
            aws.asset_prefix(bad, {})
    for reserved in ("aws", "awsx", "ssm-a", "sthree", "sg", "sg-x", "sg-hub", "xn--a", "a--b"):
        with pytest.raises(MissingInput, match="would make names AWS refuses"):
            aws.asset_prefix(reserved, {})
    assert aws.asset_prefix("sgx", {}) == "sgx" and aws.asset_prefix("my-co", {}) == "my-co"


@pytest.mark.parametrize("prefix", ["a", "hub", "abcdefghij", "z-9"])
def test_the_longest_names_fit_every_aws_limit(prefix):
    n, name, domain = aws.Names(prefix), "n" * 39 + "9", "x" * 60 + ".example.com"
    bucket_re = re.compile(r"(?!xn--|sthree-)[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")  # this setup never uses dots
    for b in (n.backup_bucket(domain, ACCOUNT), n.plan_bucket("f" * 32)):
        assert bucket_re.fullmatch(b) and "--" not in b, b
    iam_re = re.compile(r"[\w+=,.@-]{1,64}")
    for iam_name in (n.resource(name), n.backup_user(name), n.role_policy, n.backup_policy):
        assert iam_re.fullmatch(iam_name), iam_name
    assert n.backup_user(name) == f"{prefix}-backup-{name}"  # fits without the [:64] cut
    sg = n.resource(name)
    assert len(sg) <= 255 and not sg.startswith("sg-")
    param = n.param(name)
    assert re.fullmatch(r"(/[a-zA-Z0-9_.-]+){3}", param) and not re.match(r"/(aws|ssm)", param, re.I)
    assert len(param) <= 1011 and param.count("/") <= 15


def test_hub_server_names_tags_and_policies():
    p = aws.plan_aws(
        domain="hub.hotmicapp.com",
        front_door="caddy",
        region="us-west-2",
        name="hotmic",
        plan_bucket="hub-setup-plans-abc",
        prefix="hub",
    )
    d, ec2, iam, ssm = deployed(p)
    named = HUB_TAGS + [{"Key": "Name", "Value": "hub-hotmic"}]
    (sg,) = ec2.called("create_security_group")
    assert (sg["GroupName"], sg["Description"], sg["TagSpecifications"][0]["Tags"]) == ("hub-hotmic", "Server", named)
    assert ec2.called("allocate_address")[0]["TagSpecifications"][0]["Tags"] == named
    (run,) = ec2.called("run_instances")
    assert [t["Tags"] for t in run["TagSpecifications"]] == [named, named, named]
    assert run["IamInstanceProfile"] == {"Name": "hub-hotmic"}
    assert iam.called("create_role")[0]["RoleName"] == "hub-hotmic"
    (inline,) = iam.called("put_role_policy")
    assert inline["PolicyName"] == "hub-env"
    assert [s["Resource"] for s in json.loads(inline["PolicyDocument"])["Statement"]] == [
        f"arn:aws:ssm:us-west-2:{ACCOUNT}:parameter/hub/hotmic/env",
        "arn:aws:s3:::hub-setup-plans-abc/plans/hotmic/*",
    ]
    assert iam.called("create_instance_profile") == [{"InstanceProfileName": "hub-hotmic", "Tags": HUB_TAGS}]
    assert ssm.called("put_parameter")[0]["Name"] == d.param_name == "/hub/hotmic/env"
    assert ssm.called("add_tags_to_resource")[0]["Tags"] == HUB_TAGS
    text = "\n".join(p.lines())
    assert "Security group hub-hotmic:" in text and "IAM role hub-hotmic " in text
    assert "SSM SecureString /hub/hotmic/env" in text and "ManagedBy=hub-setup, hub-setup-name=hotmic" in text
    assert "tico" not in text.lower()
    # The boot script reads the prefixed parameter, which is the one the role may read.
    script = remote.bootstrap_script(
        os_family="ubuntu",
        domain="hub.hotmicapp.com",
        front_door="caddy",
        compose=None,
        compose_url="https://example.invalid/compose.yaml",
        env_param=(d.param_name, "us-west-2"),
    )
    assert "aws ssm get-parameter --region us-west-2 --name /hub/hotmic/env " in script
    runner = runner_setup.bootstrap_script(os_family="ubuntu", tag="latest", env_param=(d.param_name, "us-west-2"))
    assert "--name /hub/hotmic/env " in runner


class FilteredEC2(fakes.FakeEC2):
    """Answers describe_* by the tag filters it is given, as EC2 does, and records them."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.filters = []

    def _match(self, rows, Filters):
        self.filters.append(Filters)
        want = {f["Name"][4:]: f["Values"] for f in Filters if f["Name"].startswith("tag:")}
        return [
            r
            for r in rows
            if all({t["Key"]: t["Value"] for t in r.get("Tags", [])}.get(k) in v for k, v in want.items())
        ]

    def describe_instances(self, Filters):
        return {"Reservations": [{"Instances": self._match(self.instances, Filters)}]}

    def describe_security_groups(self, Filters):
        return {"SecurityGroups": self._match(self.sgs, Filters)}

    def describe_addresses(self, Filters):
        return {"Addresses": self._match(self.eips, Filters)}


def test_hub_discover_and_destroy_find_only_hub_tagged_resources():
    ec2 = FilteredEC2(
        extra_instances=[{"InstanceId": "i-tico", "Tags": TICO_TAGS}, {"InstanceId": "i-hub", "Tags": HUB_TAGS}],
        extra_sgs=[{"GroupId": "sg-tico", "Tags": TICO_TAGS}, {"GroupId": "sg-hub", "Tags": HUB_TAGS}],
    )
    ec2.eips = [
        {"AllocationId": "eip-tico", "PublicIp": "198.51.100.1", "Tags": TICO_TAGS},
        {"AllocationId": "eip-hub", "PublicIp": "203.0.113.9", "Tags": HUB_TAGS},
    ]
    asked = []

    class Iam(fakes.FakeIAM):
        def list_role_tags(self, RoleName):
            asked.append(RoleName)
            return {"Tags": HUB_TAGS}

    class Ssm(fakes.FakeSSM):
        def list_tags_for_resource(self, **kw):
            asked.append(kw["ResourceId"])
            return {"TagList": HUB_TAGS}

    iam, ssm = Iam(), Ssm()
    found = aws.discover(ec2, iam, ssm, "hotmic", HUB)
    assert found == {
        "instance": ["i-hub"],
        "elastic-ip": ["eip-hub"],
        "security-group": ["sg-hub"],
        "iam-role": ["hub-hotmic"],
        "ssm-parameter": ["/hub/hotmic/env"],
    }
    assert asked == ["hub-hotmic", "/hub/hotmic/env"]
    assert aws.addresses(ec2, "hotmic", HUB) == {"203.0.113.9"}
    assert all(
        {"Name": "tag:ManagedBy", "Values": ["hub-setup"]} in f
        and {"Name": "tag:hub-setup-name", "Values": ["hotmic"]} in f
        for f in ec2.filters
    )
    assert "tico" not in json.dumps(ec2.filters)
    aws.destroy(ec2, iam, ssm, found, say=lambda m: None, sleep=lambda s: None, names=HUB)
    assert iam.called("delete_role_policy") == [{"RoleName": "hub-hotmic", "PolicyName": "hub-env"}]
    assert ec2.called("terminate_instances") == [{"InstanceIds": ["i-hub"]}]
    # The default never sees the hub install, and a re-run with hub never reuses the tico one.
    assert aws.discover(ec2, fakes.FakeIAM(), fakes.FakeSSM(), "hotmic")["instance"] == ["i-tico"]
    p = aws.plan_aws(domain="hub.hotmicapp.com", front_door="caddy", region="us-west-2", name="hotmic", prefix="hub")
    d = aws.Deployer(p, ec2, fakes.FakeIAM(), fakes.FakeSSM(), ACCOUNT, say=lambda m: None, sleep=lambda s: None)
    assert d.find_instance()["InstanceId"] == "i-hub" and d.ensure_sg() == "sg-hub" and d.ensure_eip()[0] == "eip-hub"


def two_installs():
    """A tico and a hub install with the same --name in one account, as AWS would hold them."""
    ec2 = FilteredEC2(
        extra_instances=[{"InstanceId": "i-tico", "Tags": TICO_TAGS}, {"InstanceId": "i-hub", "Tags": HUB_TAGS}],
        extra_sgs=[{"GroupId": "sg-tico", "Tags": TICO_TAGS}, {"GroupId": "sg-hub", "Tags": HUB_TAGS}],
    )
    ec2.eips = [
        {"AllocationId": "eip-tico", "PublicIp": "198.51.100.1", "Tags": TICO_TAGS},
        {"AllocationId": "eip-hub", "PublicIp": "203.0.113.9", "Tags": HUB_TAGS},
    ]
    iam = IAM()
    iam.roles = {"tico-hotmic": TICO_TAGS, "hub-hotmic": HUB_TAGS}

    ssm = fakes.FakeSSM()
    ssm.params = {"/tico/hotmic/env": TICO_TAGS, "/hub/hotmic/env": HUB_TAGS}
    d = fakes.deps()
    d.aws_clients = lambda region, profile: (ec2, iam, ssm, ACCOUNT)
    return d, ec2, iam, ssm


def deleted(ec2, iam, ssm):
    return {
        "instances": [i for kw in ec2.called("terminate_instances") for i in kw["InstanceIds"]],
        "eips": [kw["AllocationId"] for kw in ec2.called("release_address")],
        "sgs": [kw["GroupId"] for kw in ec2.called("delete_security_group")],
        "roles": [kw["RoleName"] for kw in iam.called("delete_role")],
        "profiles": [kw["InstanceProfileName"] for kw in iam.called("delete_instance_profile")],
        "policies": [(kw["RoleName"], kw["PolicyName"]) for kw in iam.called("delete_role_policy")],
        "params": [kw["Name"] for kw in ssm.called("delete_parameter")],
    }


@pytest.mark.parametrize(
    ("argv", "prefix", "other"),
    [
        (["--name", "hotmic", "--asset-prefix", "tico", "--aws-region", "us-east-1"], "tico", "hub"),
        (["--name", "hotmic", "--asset-prefix", "hub", "--aws-region", "us-east-1"], "hub", "tico"),
        (["--domain", "hub.hotmicapp.com"], "hub", "tico"),  # the saved install's prefix, account and region
        # saved before the flag (and the account) existed: named in full
        (
            ["--domain", "old.example.com", "--name", "hotmic", "--asset-prefix", "tico", "--aws-region", "us-east-1"],
            "tico",
            "hub",
        ),
    ],
)
def test_destroy_deletes_only_the_install_of_its_own_prefix(argv, prefix, other):
    hub = {"domain": "hub.hotmicapp.com", "aws_name": "hotmic", "asset_prefix": "hub", "target": "aws"}
    state.save("hub.hotmicapp.com", hub | {"aws_account_id": ACCOUNT, "aws_region": "us-east-1"})
    state.save("old.example.com", {"domain": "old.example.com", "aws_name": "hotmic"})
    d, ec2, iam, ssm = two_installs()
    code, out = go(["destroy", *argv, "--yes"], deps=d)
    assert code == 0 and f"Tagged {prefix}-setup-name=hotmic in us-east-1:" in out, out
    assert deleted(ec2, iam, ssm) == {
        "instances": [f"i-{prefix}"],
        "eips": [f"eip-{prefix}"],
        "sgs": [f"sg-{prefix}"],
        "roles": [f"{prefix}-hotmic"],
        "profiles": [f"{prefix}-hotmic"],
        "policies": [(f"{prefix}-hotmic", f"{prefix}-env")],
        "params": [f"/{prefix}/hotmic/env"],
    }
    assert other not in json.dumps(ec2.filters)


def test_hub_backup_names():
    s3, iam, values = created_backup({"names": HUB})
    bucket = f"hub-backup-tico-example-com-{ACCOUNT}"  # the domain's slug, as given
    assert s3.called("create_bucket")[0]["Bucket"] == bucket
    rules = s3.called("put_bucket_lifecycle_configuration")[0]["LifecycleConfiguration"]["Rules"]
    assert [(r["ID"], r["Filter"]) for r in rules] == [
        ("hub-backups", {"Prefix": ""}),
        ("litestream", {"Prefix": "hub/000"}),
    ]
    (pol,) = iam.called("put_user_policy")
    assert (pol["UserName"], pol["PolicyName"]) == ("hub-backup-acme", "hub-backup")
    resources = {s["Resource"] for s in json.loads(pol["PolicyDocument"])["Statement"]}
    assert resources == {f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"}
    assert values["TICO_BACKUP_URL"] == f"s3://{bucket}/hub"
    assert (
        iam.called("create_user")[0]["Tags"]
        == s3.called("put_bucket_tagging")[0]["Tagging"]["TagSet"]
        == [
            {"Key": "ManagedBy", "Value": "hub-setup"},
            {"Key": "hub-setup-name", "Value": "acme"},
        ]
    )
    lines = "\n".join(backup.aws_plan_lines("hub.hotmicapp.com", "us-west-2", "hotmic", HUB, ACCOUNT))
    assert f"S3 bucket hub-backup-hub-hotmicapp-com-{ACCOUNT} in us-west-2" in lines
    assert (
        "IAM user hub-backup-hotmic allowed" in lines and f"to s3://hub-backup-hub-hotmicapp-com-{ACCOUNT}/hub" in lines
    )
    assert "tico" not in lines.lower()


def test_hub_route53_comments_and_sign_in_client_name():
    r53 = FakeRoute53("hotmicapp.com")
    route53.upsert(r53, "Z1", [dns.Record("A", "hub.hotmicapp.com", "203.0.113.9")], HUB)
    route53.delete(r53, "Z1", [{"Name": "hub.hotmicapp.com.", "Type": "A"}], HUB)
    comments = [c["ChangeBatch"]["Comment"] for c in r53.called("change_resource_record_sets")]
    assert comments == ["hub setup", "hub setup destroy"]
    assert "Application type: Web application. Name it hub." in signin.steps(signin.PROVIDERS["google"], "hub")
    assert signin.steps(signin.PROVIDERS["microsoft"], "hub")[0].startswith("Register an application named hub;")
    assert signin.steps(signin.PROVIDERS["google"]) == signin.PROVIDERS["google"].steps


# --- a whole hub install, its re-runs, runner, doctor, backup-storage and destroy, against fake AWS ------------------

DOMAIN, ZONE, HOTMIC_ACCOUNT = "hub.hotmicapp.com", "hotmicapp.com", "803561570732"


class Spy:
    """Passes every call through to the fake, keeping its arguments: what would have been sent to AWS."""

    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name == "exceptions" or not callable(attr):
            return attr

        def call(*a, **kw):
            self._log.append((name, kw))
            return attr(*a, **kw)

        return call


def hub_world():
    ec2, iam, ssm, _ = aws_clients(ec2=FilteredEC2(), iam=IAM())
    s3, r53, log = S3(), FakeRoute53(ZONE), []
    r = fakes.FakeResolver({(DOMAIN, dns.A): ["203.0.113.9"]}, {ZONE: fakes.R53_NS})
    d = fakes.deps()
    d.resolver, d.public_resolvers = r, (lambda: [("Google", r)])
    d.aws_clients = lambda region, profile: (Spy(ec2, log), Spy(iam, log), Spy(ssm, log), HOTMIC_ACCOUNT)
    d.aws_s3 = lambda region, profile: Spy(s3, log)
    d.route53_client = lambda profile: Spy(r53, log)
    return d, ec2, iam, ssm, s3, log


def hub_args(tmp_path, *extra):
    path = tmp_path / "plan.yaml"
    draft = chart(5)
    draft["company"]["name"] = "HotMic"
    path.write_text(C.to_yaml(draft))
    return [
        "--non-interactive",
        "--yes",
        "--cloud",
        "aws",
        "--aws-region",
        "us-west-2",
        "--domain",
        DOMAIN,
        "--front-door",
        "caddy",
        "--auth",
        "google",
        "--client-id",
        "cid.apps.googleusercontent.com",
        "--owner-email",
        "me@hotmicapp.com",
        "--company-file",
        str(path),
        *extra,
    ]


def strings(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from strings(v)
    elif isinstance(value, bytes):
        yield (gzip.decompress(value) if value[:2] == b"\x1f\x8b" else value).decode()
    elif isinstance(value, str):
        yield value


# What runs or is written on the server itself, sent inside three AWS arguments: the boot script (run_instances
# UserData), a re-run's script (ssm send_command) and the .env (the parameter's Value). Its paths, log, settings and
# release names say tico by design (they are not AWS names), so exactly these are taken out before looking for tico.
ON_SERVER = {("run_instances", "UserData"), ("send_command", "Parameters"), ("put_parameter", "Value")}
ON_SERVER_TICO = re.compile(
    r"/opt/tico|/run/tico/|/var/log/tico-setup\.log|tico-setup:|TICO_[A-Z0-9_]+|github\.com/ticoteam/tico/"
    r"|tico-bundle-|tico-hosted-assets-v1|\bTico\b|Written by `tico setup`"
)


def test_a_hub_install_sends_no_tico_in_any_aws_name_tag_path_policy_or_bucket(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    code, out = go(argv, deps=d)
    assert code == 0, out
    assert state.load(DOMAIN)[0]["asset_prefix"] == "hub"
    code, out = go(argv[: argv.index("--asset-prefix")], deps=d)  # the re-run, without the flag
    assert code == 0 and "already exists; reusing it" in out, out
    code, out = go(["destroy", "--domain", DOMAIN, "--dns", "--yes"], deps=d)
    assert code == 0 and "Tagged hub-setup-name=hotmic in us-west-2:" in out, out
    assert "IAM user (hub-backup-hotmic)" in out
    sent = {name for name, _ in log}
    for want in (
        "create_security_group",
        "allocate_address",
        "run_instances",
        "create_role",
        "put_role_policy",
        "create_instance_profile",
        "put_parameter",
        "add_tags_to_resource",
        "create_bucket",
        "put_bucket_lifecycle_configuration",
        "put_bucket_policy",
        "create_user",
        "put_user_policy",
        "put_object",
        "change_resource_record_sets",
        "send_command",
        "terminate_instances",
        "delete_role_policy",
    ):
        assert want in sent  # the sweep covers every kind of asset
    on_server = []
    for name, kw in log:
        for key, value in kw.items():
            for s in strings(value):
                if (name, key) in ON_SERVER:
                    on_server.append(s)
                    s = ON_SERVER_TICO.sub("", s)
                assert "tico" not in s.lower(), (name, key, s[:300])
    user_data = "\n".join(on_server)
    assert "--name /hub/hotmic/env " in user_data and "/tico/hotmic" not in user_data
    assert f"TICO_BACKUP_URL=s3://hub-backup-hub-hotmicapp-com-{HOTMIC_ACCOUNT}/hub" in user_data
    assert re.search(r"TICO_COMPANY_FILE_S3=s3://hub-setup-plans-[0-9a-f]{32}/plans/hotmic/[0-9a-f]{64}", user_data)


def test_later_runs_read_the_saved_prefix_and_refuse_another(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    before = len(log)
    for argv in (
        hub_args(tmp_path, "--asset-prefix", "tico"),
        ["doctor", "--domain", DOMAIN, "--asset-prefix", "tico"],
        ["destroy", "--domain", DOMAIN, "--asset-prefix", "tico", "--yes"],
        ["backup-storage", "--domain", DOMAIN, "--aws-region", "us-west-2", "--asset-prefix", "other"],
        ["runner", "--cloud", "aws", "--server-url", f"https://{DOMAIN}", "--label", "b1", "--asset-prefix", "tico"],
    ):
        code, out = go([*argv, "--non-interactive"] if argv[0] in ("doctor", "runner") else argv, deps=d)
        assert code == 2 and "named with --asset-prefix hub, and it cannot change" in out, (argv, out)
    assert len(log) == before  # refused before any AWS call
    # backup-storage, from the same computer: the saved prefix and name.
    assert go(["backup-storage", "--domain", DOMAIN, "--aws-region", "us-west-2"], deps=d)[0] == 0
    assert iam.called("create_user")[-1]["UserName"] == "hub-backup-hotmic"
    # The runner of that server: its plan uses the saved prefix.
    code, out = go(
        [
            "runner",
            "--dry-run",
            "--non-interactive",
            "--cloud",
            "aws",
            "--server-url",
            f"https://{DOMAIN}",
            "--label",
            "b1",
        ],
        deps=d,
    )
    assert code == 0, out
    assert "Security group hub-runner-b1:" in out and "SSM SecureString /hub/runner-b1/env" in out
    assert "ManagedBy=hub-setup, hub-setup-name=runner-b1" in out and "tico-runner" not in out


def test_a_first_hub_run_that_fails_at_backup_keeps_the_prefix_for_its_retry(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()

    def refused(**kw):
        raise ClientError("AccessDenied")

    d.aws_s3 = lambda region, profile: type("S3", (S3,), {"put_bucket_policy": staticmethod(refused)})()
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)
    assert code == 1 and "Could not create the backup storage" in out
    assert "backup-storage --domain hub.hotmicapp.com --aws-region us-west-2 --name hotmic --asset-prefix hub" in out
    assert state.load(DOMAIN)[0]["asset_prefix"] == "hub"


def test_a_hub_runner_saves_its_prefix_and_keeps_it(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://other.example"]
    argv += ["--label", "b1", "--aws-region", "us-west-2"]
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    assert go([*argv, "--asset-prefix", "hub"], deps=d)[0] == 0
    assert state.load_runners()[0]["asset_prefix"] == "hub"
    assert ssm.called("put_parameter")[0]["Name"] == "/hub/runner-b1/env"
    assert iam.called("create_role")[0]["RoleName"] == "hub-runner-b1"
    code, out = go([*argv, "--asset-prefix", "tico"], deps=d)
    assert code == 2 and "named with --asset-prefix hub" in out
    assert go(argv, deps=d)[0] == 0 and ssm.called("put_parameter")[-1]["Name"] == "/hub/runner-b1/env"


# --- never touch what this setup did not make ---------------------------------------------------------------------


def test_a_backup_bucket_another_prefix_made_is_refused_before_any_write():
    """hub + backup-foo.example.com and hub-backup + foo.example.com spell the same bucket."""
    s3, iam = S3(), IAM()
    kw = dict(region="us-west-2", account_id=ACCOUNT, say=lambda _: None)
    backup.create_s3(s3, iam, domain="backup-foo.example.com", name="a", names=HUB, **kw)
    bucket = HUB.backup_bucket("backup-foo.example.com", ACCOUNT)
    assert aws.Names("hub-backup").backup_bucket("foo.example.com", ACCOUNT) == bucket
    before, users = len(s3.calls), dict(iam.users)
    with pytest.raises(MissingInput, match=f"S3 bucket {bucket} .*another --asset-prefix"):
        backup.create_s3(s3, iam, domain="foo.example.com", name="b", names=aws.Names("hub-backup"), **kw)
    assert [n for n, _ in s3.calls[before:]] == ["head_bucket", "get_bucket_tagging"]
    assert iam.users == users and len(iam.called("put_user_policy")) == 1
    assert all(c[1].get("ExpectedBucketOwner") == ACCOUNT for c in s3.calls[before:])


def test_an_untagged_backup_bucket_is_refused_and_a_new_one_is_tagged_first():
    s3, iam = S3(), IAM()
    bucket = aws.DEFAULT_NAMES.backup_bucket("tico.example.com", ACCOUNT)
    s3.tagsets[bucket] = []  # exists, made by someone else
    with pytest.raises(MissingInput, match=f"S3 bucket {bucket} "):
        backup.create_s3(
            s3, iam, domain="tico.example.com", region="us-west-2", account_id=ACCOUNT, name="acme", say=print
        )
    assert [n for n, _ in s3.calls] == ["head_bucket", "get_bucket_tagging"] and not iam.calls
    s3, _, _ = created_backup()
    writes = [n for n, _ in s3.calls if n not in ("head_bucket", "get_bucket_tagging")]
    assert writes[:2] == ["create_bucket", "put_bucket_tagging"]


def test_a_runner_takes_its_own_servers_prefix_not_another_servers_runner_of_the_same_name(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    old = {"server_url": "https://a.example", "label": "b1", "target": "aws", "aws_region": "us-west-2"}
    old |= {"aws_profile": "", "instance_id": "i-a", "name": "runner-b1"}  # from before the flag: tico
    state.save_runner("runner-b1", old)
    state.save(DOMAIN, {"domain": DOMAIN, "aws_name": "hotmic", "asset_prefix": "hub"})
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--cloud", "aws", "--server-url", f"https://{DOMAIN}", "--label", "b1"]
    argv += ["--aws-region", "us-west-2"]
    code, out = go([*argv, "--dry-run"], deps=d)
    assert code == 0 and "Security group hub-runner-b1:" in out, out
    assert go([*argv, "--yes"], deps=d)[0] == 0
    assert ssm.called("put_parameter")[0]["Name"] == "/hub/runner-b1/env"
    runners = {r["server_url"]: r for r in state.load_runners()}
    assert runners["https://a.example"] == old and runners[f"https://{DOMAIN}"]["asset_prefix"] == "hub"
    # Another tico server's runner b1 in that region would be a.example's runner: refused before any AWS call.
    state.save("c.example.com", {"domain": "c.example.com"})
    before = len(log)
    argv[argv.index(f"https://{DOMAIN}")] = "https://c.example.com"
    code, out = go([*argv, "--yes"], deps=d)
    assert code == 2 and "tico-runner-b1 is already the runner of https://a.example (us-west-2)" in out, out
    assert len(log) == before


def test_a_runner_saved_by_name_alone_is_moved_to_its_server_scoped_record(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    old = {"server_url": "https://other.example", "label": "b1", "target": "aws", "aws_region": "us-west-2"}
    state.save_runner("runner-b1", {**old, "aws_profile": "", "instance_id": "i-1", "name": "runner-b1"})
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://other.example"]
    assert go([*argv, "--label", "b1", "--aws-region", "us-west-2"], deps=d)[0] == 0
    assert [r["server_url"] for r in state.load_runners()] == ["https://other.example"]
    assert not (state.home() / "runners" / "runner-b1.json").exists()
    assert ssm.called("put_parameter")[0]["Name"] == "/tico/runner-b1/env"  # saved before the flag: tico


def test_a_name_other_than_the_domains_saved_one_is_refused_before_any_aws_call(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    clients = d.aws_clients
    opened = []
    d.aws_clients = lambda region, profile: opened.append(region) or clients(region, profile)
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    before = (len(log), len(opened))
    msg = f"{DOMAIN} is set up with --name hotmic"
    for argv in (
        hub_args(tmp_path, "--name", "production"),
        ["destroy", "--domain", DOMAIN, "--name", "production", "--yes"],
    ):
        code, out = go(argv, deps=d)
        assert code == 2 and msg in out and "Leave --name out or pass --name hotmic" in out, out
    assert (len(log), len(opened)) == before
    # Saved before --name existed: an AWS install was named from its domain.
    state.save("old.example.com", {"domain": "old.example.com", "target": "aws", "aws_instance_id": "i-1"})
    code, out = go(["destroy", "--domain", "old.example.com", "--name", "hotmic", "--yes"], deps=d)
    assert code == 2 and "old.example.com is set up with --name old-example-com" in out, out
    code, out = go(["destroy", "--domain", "old.example.com", "--name", "old-example-com", "--yes"], deps=d)
    assert code == 0 and "Nothing tagged" in out, out


READS = ("describe_", "list_", "get_", "head_")


def test_an_ssm_parameter_setup_did_not_make_is_refused_before_any_write(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    ssm.params["/hub/hotmic/env"] = []  # someone else's, untagged
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)
    assert code == 2 and "SSM parameter /hub/hotmic/env already exists and was not made by this setup" in out, out
    assert "Pick another --name or --asset-prefix." in out
    assert [n for n, _ in log if not n.startswith(READS)] == []
    # The deployer on its own never overwrites or tags it either.
    p = aws.plan_aws(domain=DOMAIN, front_door="caddy", region="us-west-2", name="hotmic", prefix="hub")
    dep = aws.Deployer(p, ec2, iam, ssm, ACCOUNT, say=lambda m: None, sleep=lambda s: None)
    with pytest.raises(MissingInput, match="SSM parameter /hub/hotmic/env"):
        dep.put_env("X=1\n")
    assert not ssm.called("put_parameter") and not ssm.called("add_tags_to_resource")
    # A missing one is created without overwrite, tagged in the same call.
    del ssm.params["/hub/hotmic/env"]
    dep.put_env("X=1\n")
    (put,) = ssm.called("put_parameter")
    assert put["Overwrite"] is False and put["Tags"] == HUB_TAGS


def test_an_iam_role_setup_did_not_make_is_refused_before_any_write(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    iam.roles["hub-hotmic"] = [{"Key": "team", "Value": "production"}]
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)
    assert code == 2 and "IAM role hub-hotmic already exists and was not made by this setup" in out, out
    assert [n for n, _ in log if not n.startswith(READS)] == []


PRODUCTION = [{"Key": "team", "Value": "production"}]


def test_an_instance_profile_setup_did_not_make_is_refused_and_never_deleted(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    iam.profiles["hub-hotmic"] = {"Tags": [], "Roles": ["production-app"]}
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)
    assert code == 2 and "IAM instance profile hub-hotmic already exists and was not made by this setup" in out, out
    assert [n for n, _ in log if not n.startswith(READS)] == []
    # ensure_role on its own refuses it too, rather than handing it to EC2.
    p = aws.plan_aws(domain=DOMAIN, front_door="caddy", region="us-west-2", name="hotmic", prefix="hub")
    dep = aws.Deployer(p, ec2, iam, ssm, ACCOUNT, say=lambda m: None, sleep=lambda s: None)
    with pytest.raises(MissingInput, match="IAM instance profile hub-hotmic"):
        dep.ensure_role()
    assert not iam.called("add_role_to_instance_profile")
    # A tagged one holding another role is not ours either.
    iam.profiles["hub-hotmic"] = {"Tags": HUB_TAGS, "Roles": ["production-app"]}
    with pytest.raises(MissingInput, match="IAM instance profile hub-hotmic"):
        dep.ensure_role()
    # Destroy deletes the role it made but leaves a profile it did not make.
    for tags in ([], PRODUCTION, HUB_TAGS):
        iam.calls.clear()
        iam.profiles["hub-hotmic"] = {"Tags": tags, "Roles": ["production-app"] if tags == HUB_TAGS else []}
        out = []
        aws.destroy(ec2, iam, ssm, {"iam-role": ["hub-hotmic"]}, say=out.append, sleep=lambda s: None, names=HUB)
        assert not iam.called("delete_instance_profile") and not iam.called("remove_role_from_instance_profile")
        assert "left alone: IAM instance profile hub-hotmic was not made by this setup" in "\n".join(out)


def test_a_profile_setup_made_without_its_role_gets_it_on_a_rerun():
    p = aws.plan_aws(domain=DOMAIN, front_door="caddy", region="us-west-2", name="hotmic", prefix="hub")
    iam = IAM()
    iam.profiles["hub-hotmic"] = {"Tags": HUB_TAGS, "Roles": []}  # an earlier run stopped between the two calls
    dep = aws.Deployer(p, fakes.FakeEC2(), iam, fakes.FakeSSM(), ACCOUNT, say=lambda m: None, sleep=lambda s: None)
    assert dep.ensure_role() == "hub-hotmic"
    assert iam.called("add_role_to_instance_profile") == [
        {"InstanceProfileName": "hub-hotmic", "RoleName": "hub-hotmic"}
    ]


def test_a_route53_record_setup_did_not_make_is_refused_unless_replace_dns(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    production = {"Name": f"{DOMAIN}.", "Type": "A", "TTL": 60, "ResourceRecords": [{"Value": "198.51.100.50"}]}
    r53.records = [production]
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    code, out = go(argv, deps=d)
    assert code == 2 and f"Route 53 already has A {DOMAIN} -> 198.51.100.50" in out, out
    assert "--replace-dns" in out and [n for n, _ in log if not n.startswith(READS)] == []
    assert r53.records == [production]
    code, out = go([*argv, "--replace-dns"], deps=d)
    assert code == 0, out
    assert [r["ResourceRecords"] for r in r53.records] == [[{"Value": "203.0.113.9"}]]
    assert state.load(DOMAIN)[0]["dns_owned"] == DOMAIN
    # Re-runs of this install keep working without the flag, also once the record points elsewhere: setup made it.
    assert go(argv, deps=d)[0] == 0
    r53.records[0]["ResourceRecords"] = [{"Value": "198.51.100.77"}]
    assert go(argv, deps=d)[0] == 0 and r53.records[0]["ResourceRecords"] == [{"Value": "203.0.113.9"}]


def test_an_alias_or_cname_at_the_hostname_is_refused_too(tmp_path):
    for existing in (
        {"Name": f"{DOMAIN}.", "Type": "A", "AliasTarget": {"DNSName": "lb.example.", "HostedZoneId": "Z2"}},
        {"Name": f"{DOMAIN}.", "Type": "CNAME", "TTL": 60, "ResourceRecords": [{"Value": "app.example."}]},
        {"Name": f"{DOMAIN}.", "Type": "AAAA", "TTL": 60, "ResourceRecords": [{"Value": "2001:db8::1"}]},
    ):
        d, ec2, iam, ssm, s3, log = hub_world()
        d.route53_client("")._inner.records = [existing]
        code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)
        assert code == 2 and f"Route 53 already has {existing['Type']} {DOMAIN}" in out, out
        assert [n for n, _ in log if not n.startswith(READS)] == []


def test_a_hub_runner_that_fails_at_launch_keeps_its_prefix_for_the_retry(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://other.example"]
    argv += ["--label", "b1", "--aws-region", "us-west-2"]

    def refused(**kw):
        raise RuntimeError("InsufficientInstanceCapacity")

    ec2.run_instances = refused
    with pytest.raises(RuntimeError):
        go([*argv, "--asset-prefix", "hub"], deps=d)
    assert ssm.called("put_parameter")[0]["Name"] == "/hub/runner-b1/env"
    (saved,) = state.load_runners()
    assert saved["asset_prefix"] == "hub" and saved["instance_id"] == ""
    io = fakes.CaptureIO()
    assert not runner_setup.doctor_runners(io, d, "https://other.example") and "not launched yet" in io.text
    del ec2.run_instances  # the capacity is back
    assert go(argv, deps=d)[0] == 0
    assert {c["Name"] for c in ssm.called("put_parameter")} == {"/hub/runner-b1/env"}
    assert state.load_runners()[0]["instance_id"] == "i-1"


def test_backup_storage_saves_its_prefix_before_provisioning_and_keeps_it():
    iam, made = IAM(), []

    def failing_s3(region, profile):
        s3 = S3()
        s3.put_bucket_policy = lambda **kw: (_ for _ in ()).throw(ClientError("AccessDenied"))
        made.append(s3)
        return s3

    d = fakes.deps()
    d.aws_clients = lambda region, profile: (None, iam, None, ACCOUNT)
    d.aws_s3 = failing_s3
    argv = ["backup-storage", "--domain", "laptop.example.com", "--aws-region", "us-west-2"]
    with pytest.raises(ClientError):
        go([*argv, "--asset-prefix", "hub"], deps=d)
    assert made[0].called("create_bucket")[0]["Bucket"].startswith("hub-backup-laptop-example-com-")
    d.aws_s3 = lambda region, profile: made[0]
    del made[0].put_bucket_policy
    code, out = go(argv, deps=d)  # the retry, without the flag
    assert code == 0 and "s3://hub-backup-laptop-example-com-" in out, out
    assert [c["UserName"] for c in iam.called("create_user")] == ["hub-backup-laptop-example-com"]
    code, out = go([*argv, "--asset-prefix", "tico"], deps=d)
    assert code == 2 and "named with --asset-prefix hub" in out
    assert state.load("laptop.example.com") == ({}, "")  # no server state is made or changed for it
    assert state.known_domains() == []


class StopAtPlan(fakes.CaptureIO):
    def confirm(self, question, default=True):
        return False if "Go ahead" in question else default


def test_a_prompted_domain_restores_its_whole_saved_install(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    io = StopAtPlan(interactive=True, answers=["", DOMAIN])  # the target's default, then the domain
    code, out = go([], deps=d, io=io)
    assert code == 1 and "Stopped; nothing was changed." in out, out
    assert f"Resuming from the last run for {DOMAIN}" in out
    assert "Security group hub-hotmic:" in out and "SSM SecureString /hub/hotmic/env" in out, out
    assert "hub-hub-hotmicapp-com" not in out


# --- Codex's second review -----------------------------------------------------------------------------------------


def test_an_install_keeps_its_aws_account_and_region_and_destroy_never_guesses_them(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    saved = state.load(DOMAIN)[0]
    assert (saved["aws_account_id"], saved["aws_region"]) == (HOTMIC_ACCOUNT, "us-west-2")
    # Another account (the same tags could be another install there) or another region: refused before any call.
    d.aws_clients = lambda region, profile: (Spy(ec2, log), Spy(iam, log), Spy(ssm, log), "999999999999")
    log.clear()
    code, out = go(hub_args(tmp_path), deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    code, out = go(["destroy", "--domain", DOMAIN, "--yes"], deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    assert not [c for c, _ in log if not c.startswith(READS)]
    d.aws_clients = lambda region, profile: (Spy(ec2, log), Spy(iam, log), Spy(ssm, log), HOTMIC_ACCOUNT)
    code, out = go([*hub_args(tmp_path), "--aws-region", "us-east-1"], deps=d)
    assert code == 2 and "in us-west-2" in out and "cannot change" in out, out
    code, out = go(["destroy", "--domain", DOMAIN, "--aws-region", "us-east-1", "--yes"], deps=d)
    assert code == 2 and "in us-west-2" in out, out
    assert not [c for c, _ in log if not c.startswith(READS)]


def test_destroy_without_a_saved_install_deletes_only_what_the_flags_name_in_full():
    d, ec2, iam, ssm = two_installs()
    code, out = go(["destroy", "--name", "hotmic", "--yes"], deps=d)
    assert code == 2 and "--asset-prefix, --name and --aws-region" in out, out
    state.save("old.example.com", {"domain": "old.example.com", "aws_name": "hotmic", "target": "aws"})  # no account
    code, out = go(["destroy", "--domain", "old.example.com", "--yes"], deps=d)
    assert code == 2 and "--asset-prefix, --name and --aws-region" in out, out
    assert deleted(ec2, iam, ssm)["instances"] == []
    argv = ["destroy", "--name", "hotmic", "--asset-prefix", "tico", "--aws-region", "us-east-1", "--yes"]
    code, out = go(argv, deps=d)
    assert code == 0 and deleted(ec2, iam, ssm)["instances"] == ["i-tico"], out


def test_an_install_saved_before_the_account_was_adopts_it_once_after_the_tag_proof(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    saved = state.load(DOMAIN)[0]
    del saved["aws_account_id"]
    state.save(DOMAIN, saved)
    assert go(hub_args(tmp_path), deps=d)[0] == 0
    assert state.load(DOMAIN)[0]["aws_account_id"] == HOTMIC_ACCOUNT


def test_a_runner_and_backup_storage_keep_their_aws_account(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://other.example"]
    argv += ["--label", "b1", "--aws-region", "us-west-2"]
    assert go(argv, deps=d)[0] == 0
    assert state.load_runners()[0]["aws_account_id"] == HOTMIC_ACCOUNT
    bs = ["backup-storage", "--domain", "laptop.example.com", "--aws-region", "us-west-2"]
    assert go(bs, deps=d)[0] == 0 and state.load_backup("laptop.example.com")["aws_account_id"] == HOTMIC_ACCOUNT
    d.aws_clients = lambda region, profile: (Spy(ec2, log), Spy(iam, log), Spy(ssm, log), "999999999999")
    log.clear()
    code, out = go(argv, deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    code, out = go(bs, deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    code, out = go(["backup-storage", "--domain", "laptop.example.com", "--aws-region", "us-east-1"], deps=d)
    assert code == 2 and "set up in us-west-2" in out, out
    assert not [c for c, _ in log if not c.startswith(READS)]


class RacingS3(S3):
    """A bucket that someone makes between setup's read (absent) and its create, which us-east-1 answers with success."""

    def __init__(self, tags):
        super().__init__()
        self.appears = tags

    def create_bucket(self, **kw):
        self._rec("create_bucket", kw)
        self.tagsets[kw["Bucket"]] = self.appears


def test_a_bucket_that_appears_between_the_read_and_the_create_is_never_adopted():
    # (An untagged one made in that instant cannot be told from a new one in us-east-1: the residual window.)
    for tags in (PRODUCTION, TICO_TAGS):
        s3, iam = RacingS3(tags), IAM()
        with pytest.raises(MissingInput, match="was not made by this setup"):
            backup.create_s3(
                s3, iam, domain="tico.example.com", region="us-east-1", account_id=ACCOUNT, name="acme", say=print
            )
        assert not s3.called("put_bucket_tagging") and not iam.called("create_user")
        assert [n for n, _ in s3.calls] == ["head_bucket", "create_bucket", "get_bucket_tagging"]


def test_a_bucket_setup_made_but_could_not_tag_is_finished_by_the_retry_and_only_with_its_claim():
    iam, s3 = IAM(), S3()
    s3.put_bucket_tagging = lambda **kw: (_ for _ in ()).throw(ClientError("SlowDown"))
    d = fakes.deps()
    d.aws_clients = lambda region, profile: (None, iam, None, ACCOUNT)
    d.aws_s3 = lambda region, profile: s3
    argv = ["backup-storage", "--domain", "laptop.example.com", "--aws-region", "us-west-2"]
    with pytest.raises(ClientError):
        go(argv, deps=d)
    bucket = aws.DEFAULT_NAMES.backup_bucket("laptop.example.com", ACCOUNT)
    assert s3.tagsets[bucket] == [] and state.load_backup("laptop.example.com")["bucket_claims"].startswith(
        bucket + "@"
    )
    del s3.put_bucket_tagging
    assert go(argv, deps=d)[0] == 0  # the claim saved before the create: setup's own bucket, tagged now
    assert aws.DEFAULT_NAMES.is_ours(s3.tagsets[bucket], "laptop-example-com")
    # Without the claim, the same untagged bucket is someone else's.
    s3.tagsets[bucket] = []
    claimless = state.load_backup("laptop.example.com")
    del claimless["bucket_claims"]
    state.save_backup("laptop.example.com", claimless)
    code, out = go(argv, deps=d)
    assert code == 2 and "was not made by this setup" in out, out


def test_the_plan_bucket_setup_made_but_could_not_tag_is_finished_with_its_claim():
    p = aws.plan_aws(
        domain="a.example", front_door="caddy", region="us-east-1", name="acme", plan_bucket="tico-setup-plans-x"
    )
    d = aws.Deployer(p, None, None, None, ACCOUNT)
    s3, claims = S3(), []
    s3.put_bucket_tagging = lambda **kw: (_ for _ in ()).throw(ClientError("SlowDown"))
    with pytest.raises(ClientError):
        d.ensure_plan_bucket(s3, "us-east-1", claim=lambda b, on: claims.append((b, on)))
    assert claims == [("tico-setup-plans-x", True)] and s3.tagsets["tico-setup-plans-x"] == []
    del s3.put_bucket_tagging
    with pytest.raises(SystemExit, match="refusing to reuse"):
        d.ensure_plan_bucket(s3, "us-east-1")  # no claim: never adopted
    d.ensure_plan_bucket(s3, "us-east-1", claims="tico-setup-plans-x@2026-11-10T00:00:00Z")
    assert aws.DEFAULT_NAMES.is_ours(s3.tagsets["tico-setup-plans-x"], "acme")


def test_destroy_never_terminates_an_instance_that_would_take_a_volume_setup_did_not_make():
    d, ec2, iam, ssm = two_installs()
    attached = lambda dot: [{"InstanceId": "i-tico", "DeleteOnTermination": dot}]  # noqa: E731
    ec2.volumes = [
        {"VolumeId": "vol-root", "Tags": TICO_TAGS, "Attachments": attached(True)},
        {"VolumeId": "vol-prod", "Tags": PRODUCTION, "Attachments": attached(True)},
        {"VolumeId": "vol-kept", "Tags": PRODUCTION, "Attachments": attached(False)},  # survives termination
    ]
    argv = ["destroy", "--name", "hotmic", "--asset-prefix", "tico", "--aws-region", "us-east-1", "--yes"]
    code, out = go(argv, deps=d)
    assert code == 2 and "vol-prod" in out and "vol-kept" not in out and "vol-root" not in out, out
    assert deleted(ec2, iam, ssm)["instances"] == []
    with pytest.raises(MissingInput, match="vol-prod"):  # and again right before the terminate call itself
        aws.destroy(ec2, iam, ssm, {"instance": ["i-tico"]}, say=print, name="hotmic")
    assert deleted(ec2, iam, ssm)["instances"] == []
    ec2.volumes[1]["Attachments"] = attached(False)
    assert go(argv, deps=d)[0] == 0 and deleted(ec2, iam, ssm)["instances"] == ["i-tico"]


def test_a_role_or_backup_user_replaced_between_the_proof_and_the_write_is_never_written():
    p = aws.plan_aws(domain=DOMAIN, front_door="caddy", region="us-west-2", name="hotmic", prefix="hub")
    for replacement in ({"RoleId": "AROAOTHER", "Tags": HUB_TAGS}, {"RoleId": "AROAOTHER", "Tags": PRODUCTION}):
        iam = IAM()
        iam.roles["hub-hotmic"] = {"RoleId": "AROAFIRST", "Tags": HUB_TAGS}
        dep = aws.Deployer(p, fakes.FakeEC2(), iam, fakes.FakeSSM(), ACCOUNT, say=print, sleep=lambda s: None)
        dep.preflight()  # proven: setup's role
        iam.roles["hub-hotmic"] = replacement  # deleted and made again by someone else
        with pytest.raises((MissingInput, SystemExit), match="hub-hotmic"):
            dep.ensure_role()
        assert not iam.called("attach_role_policy") and not iam.called("put_role_policy")
    ssm = fakes.FakeSSM(param_tags=HUB_TAGS)  # an existing parameter is overwritten, never re-tagged
    aws.Deployer(p, fakes.FakeEC2(), IAM(), ssm, ACCOUNT, say=print, sleep=lambda s: None).put_env("A=1")
    assert ssm.called("put_parameter")[0]["Overwrite"] and not ssm.called("add_tags_to_resource")
    s3, iam = S3(), IAM()
    real_get = iam.get_user

    def swapped(UserName):  # the user is replaced right after setup's first read of it
        got = real_get(UserName)
        iam.users[UserName] = {"UserId": "AIDAOTHER", "Tags": iam.users[UserName]["Tags"]}
        return got

    iam.users["hub-backup-acme"] = {"UserId": "AIDAFIRST", "Tags": HUB.tags("acme")}
    iam.get_user = swapped
    with pytest.raises(MissingInput, match="IAM user hub-backup-acme"):
        backup.create_s3(
            s3,
            iam,
            domain="tico.example.com",
            region="us-west-2",
            account_id=ACCOUNT,
            name="acme",
            names=HUB,
            say=print,
        )
    assert not iam.called("put_user_policy") and not iam.called("create_access_key")


def test_a_record_made_between_the_read_and_the_write_is_never_overwritten():
    r53 = FakeRoute53(ZONE)
    want = [dns.Record("A", DOMAIN, "203.0.113.9")]
    changes, created, found = route53.plan(r53, "Z1", want, set())
    assert found == [] and created == {DOMAIN} and [c["Action"] for c in changes] == ["CREATE"]
    production = {"Name": f"{DOMAIN}.", "Type": "A", "TTL": 60, "ResourceRecords": [{"Value": "198.51.100.50"}]}
    r53.records = [production]  # someone else's, made in that instant
    with pytest.raises(fakes.InvalidChangeBatch):
        route53.apply(r53, "Z1", changes, HUB)
    assert r53.records == [production]


def test_replace_dns_turns_a_cname_into_the_servers_address_in_one_batch(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    r53.records = [{"Name": f"{DOMAIN}.", "Type": "CNAME", "TTL": 60, "ResourceRecords": [{"Value": "app.example."}]}]
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic", "--replace-dns"), deps=d)
    assert code == 0, out
    assert [(r["Type"], r["ResourceRecords"]) for r in r53.records] == [("A", [{"Value": "203.0.113.9"}])]
    (batch,) = r53.called("change_resource_record_sets")
    assert [c["Action"] for c in batch["ChangeBatch"]["Changes"]] == ["DELETE", "CREATE"]


def test_a_matching_record_setup_did_not_make_is_left_alone_and_never_deleted(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    r53.fail = True  # the first run cannot reach Route 53, so the person adds the record by hand
    assert go(argv, deps=d)[0] == 0 and state.load(DOMAIN)[0]["dns_owned"] == ""
    manual = {"Name": f"{DOMAIN}.", "Type": "A", "TTL": 3600, "ResourceRecords": [{"Value": "203.0.113.9"}]}
    r53.fail, r53.records = False, [dict(manual)]
    code, out = go(argv, deps=d)
    assert code == 0, out
    assert r53.records == [manual] and not r53.called("change_resource_record_sets")  # TTL kept, nothing written
    assert state.load(DOMAIN)[0]["dns_owned"] == ""
    code, out = go(["destroy", "--domain", DOMAIN, "--dns", "--yes"], deps=d)
    assert code == 0 and f"Left alone: A {DOMAIN} -> 203.0.113.9 was not made by this setup" in out, out
    assert r53.records == [manual] and not r53.called("change_resource_record_sets")


def test_replace_dns_covers_only_the_requested_hostname_not_the_runner_one():
    r53 = FakeRoute53(ZONE)
    runner = f"runner.{DOMAIN}"
    theirs = {"Name": f"{runner}.", "Type": "CNAME", "TTL": 60, "ResourceRecords": [{"Value": "other.example."}]}
    r53.records = [
        {"Name": f"{DOMAIN}.", "Type": "CNAME", "TTL": 60, "ResourceRecords": [{"Value": "old.example."}]},
        theirs,
    ]
    s = Settings(domain=DOMAIN, replace_dns=True)
    records = dns.plan_records(DOMAIN, "cloudflared", tunnel_id="t1", runner_host=runner)
    changes, created, found = route53.plan(r53, "Z1", records, wizard.replaceable(s))
    assert found == [theirs] and wizard.replaceable(s) == {DOMAIN}
    with pytest.raises(MissingInput, match="--replace-dns covers only the server's own hostname"):
        wizard.refuse_dns(s, found)


def test_another_servers_runner_role_in_another_region_is_never_reused(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    # Made from another computer for https://a.example in us-east-2: IAM roles and profiles are global.
    theirs = aws.Names().tags("runner-b1") + [
        {"Key": "tico-setup-server", "Value": aws.server_identity("tico", "https://a.example", "us-east-2")}
    ]
    iam.roles["tico-runner-b1"] = {"RoleId": "AROATHEIRS", "Tags": theirs}
    iam.profiles["tico-runner-b1"] = {"Tags": theirs, "Roles": ["tico-runner-b1"]}
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://c.example.com"]
    code, out = go([*argv, "--label", "b1", "--aws-region", "us-west-2"], deps=d)
    assert code == 2 and "IAM role tico-runner-b1" in out, out
    assert not iam.called("put_role_policy") and not ssm.called("put_parameter") and not ec2.called("run_instances")
    # From this computer too, the saved record of a.example's runner in another region refuses it before any AWS call.
    rec = {"server_url": "https://a.example", "label": "b1", "target": "aws", "aws_region": "us-east-2"}
    state.save_runner("a", {**rec, "name": "runner-b1", "instance_id": "i-a"})
    before = len(log)
    code, out = go([*argv, "--label", "b1", "--aws-region", "us-west-2"], deps=d)
    assert code == 2 and "already the runner of https://a.example (us-east-2)" in out and len(log) == before, out
    # Its own runner, made here, carries the identity on the role, profile and parameter.
    code, out = go([*argv, "--label", "b2", "--aws-region", "us-west-2"], deps=d)
    assert code == 0, out
    mine = {"Key": "tico-setup-server", "Value": aws.server_identity("tico", "https://c.example.com", "us-west-2")}
    assert mine in iam.called("create_role")[0]["Tags"] and mine in iam.profiles["tico-runner-b2"]["Tags"]
    assert mine in ssm.called("put_parameter")[0]["Tags"]

