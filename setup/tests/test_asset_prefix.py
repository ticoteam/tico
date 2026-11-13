"""The words in every AWS name, tag and path setup makes. Without --asset-prefix (or with tico) they are today's `tico`
names and setup runs exactly as before (test_default_prefix_golden.py pins every call). With --asset-prefix hub no AWS
name, tag, path, policy or bucket says tico, names on the server itself (/opt/tico, TICO_* settings) stay, and setup runs
in strict mode (setup/owned.py): it acts only on what it proves is this install's."""

import gzip
import hashlib
import json
import re

import pytest

from backend import company_file as C
from backend.tests.helpers.company_file import chart
from setup import aws, backup, dns, owned, remote, route53, runner_setup, signin, state, wizard
from setup.settings import Settings
from setup.ui import MissingInput
from setup.tests import fakes
from setup.tests.fakes import FakeRoute53, aws_clients
from setup.tests.helpers.plan_bucket import ClientError
from setup.tests.helpers.wizard import BASE, go, secret_env  # noqa: F401 (secret_env: autouse)

ACCOUNT = "123456789012"


def etag(body: bytes) -> str:
    """As S3 gives a single-part upload: the MD5 of its bytes, whatever its metadata or when it was written."""
    return f'"{hashlib.md5(body).hexdigest()}"'


class S3:
    """Every S3 call, recorded; a bucket exists once it is created. Objects too, as S3 keeps them: a put with
    IfNoneMatch="*" fails where one exists (S3's conditional write); an ETag is a function of the bytes alone, so the same
    bytes written again keep it; in a bucket with versioning on, each put is a new VersionId and a delete naming one
    removes only that version; a delete with IfMatch fails unless the current ETag is that one."""

    def __init__(self):
        self.calls, self.tagsets, self.objects, self.puts = [], {}, {}, 0
        self.versioned, self.versions = set(), {}  # versions: (bucket, key) -> older versions still kept

    def _rec(self, name, kw):
        self.calls.append((name, kw))

    def head_bucket(self, **kw):
        self._rec("head_bucket", kw)
        if kw["Bucket"] not in self.tagsets:
            raise ClientError("404")

    def create_bucket(self, **kw):
        self._rec("create_bucket", kw)
        self.tagsets[kw["Bucket"]] = kw.get("CreateBucketConfiguration", {}).get("Tags", [])

    def put_bucket_tagging(self, **kw):
        self._rec("put_bucket_tagging", kw)
        self.tagsets[kw["Bucket"]] = kw["Tagging"]["TagSet"]

    def get_bucket_tagging(self, **kw):
        self._rec("get_bucket_tagging", kw)
        return {"TagSet": self.tagsets[kw["Bucket"]]}

    def put_object(self, **kw):
        self._rec("put_object", kw)
        at = (kw["Bucket"], kw["Key"])
        if kw.get("IfNoneMatch") == "*" and at in self.objects:
            raise ClientError("PreconditionFailed")
        self.puts += 1
        obj = {"Body": kw["Body"], "Metadata": kw.get("Metadata", {}), "ETag": etag(kw["Body"])}
        if kw["Bucket"] in self.versioned:
            obj["VersionId"] = f"v{self.puts}"
            if at in self.objects:
                self.versions.setdefault(at, []).append(self.objects[at])
        self.objects[at] = obj
        return {k: obj[k] for k in ("ETag", "VersionId") if k in obj}

    def put_bucket_versioning(self, **kw):
        self._rec("put_bucket_versioning", kw)
        if kw["VersioningConfiguration"]["Status"] == "Enabled":
            self.versioned.add(kw["Bucket"])

    def head_object(self, **kw):
        self._rec("head_object", kw)
        at = (kw["Bucket"], kw["Key"])
        rows = [self.objects[at]] if at in self.objects else []
        rows += self.versions.get(at, [])
        o = next((r for r in rows if not kw.get("VersionId") or r.get("VersionId") == kw["VersionId"]), None)
        if o is None:
            raise ClientError("404")
        return {k: o[k] for k in ("ETag", "Metadata", "VersionId") if k in o}

    def delete_object(self, **kw):
        self._rec("delete_object", kw)
        at = (kw["Bucket"], kw["Key"])
        if kw.get("IfMatch") and (at not in self.objects or self.objects[at]["ETag"] != kw["IfMatch"]):
            raise ClientError("PreconditionFailed")
        if kw.get("VersionId"):
            if (self.objects.get(at) or {}).get("VersionId") == kw["VersionId"]:
                self.objects.pop(at)
                if self.versions.get(at):
                    self.objects[at] = self.versions[at].pop()
            else:
                self.versions[at] = [v for v in self.versions.get(at, []) if v.get("VersionId") != kw["VersionId"]]
            return
        self.objects.pop(at, None)

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


def strict_dep(p, ec2, iam, ssm, ids=None, save=lambda: None):
    """Strict mode's deployer (a plan with an install id)."""
    return owned.StrictDeployer(p, ec2, iam, ssm, ACCOUNT, say=print, sleep=lambda s: None, ids=ids, save=save)


def hub_plan(**kw):
    """A strict-mode plan: --asset-prefix hub, a new install's id."""
    base = dict(domain="hub.hotmicapp.com", front_door="caddy", region="us-west-2", name="hotmic", prefix="hub")
    return aws.plan_aws(**{**base, "install_id": INSTALL, **kw})


def deployed(p):
    ec2, iam, ssm, _ = aws_clients()
    if p.install_id:
        d = strict_dep(p, ec2, iam, ssm)
    else:
        d = aws.Deployer(p, ec2, iam, ssm, ACCOUNT, say=lambda m: None, sleep=lambda s: None)
    d.put_env("TICO_DOMAIN=x\n")
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\n")
    return d, ec2, iam, ssm


def created_backup(prefix_kw=None):
    s3, iam = S3(), IAM()
    values = (owned.create_s3 if prefix_kw else backup.create_s3)(
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
    assert "Tags" not in ssm.called("put_parameter")[0] and ssm.called("add_tags_to_resource")[0]["Tags"] == tags
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
    assert s3.called("create_bucket")[0]["CreateBucketConfiguration"] == {"LocationConstraint": "us-west-2"}
    assert s3.called("put_bucket_tagging")[0]["Tagging"]["TagSet"] == [
        {"Key": "ManagedBy", "Value": "tico-setup"},
        {"Key": "tico-setup-name", "Value": "acme"},
    ]


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


# --- the backup user's name, in strict mode (the default prefix keeps main's, from the domain) ---------------------


def test_a_hub_plan_names_the_backup_user_from_name_as_setup_creates_it():
    code, out = go(
        ["--dry-run", "--target", "aws", "--front-door", "caddy", "--name", "acme", "--asset-prefix", "hub", *BASE]
    )
    assert code == 0, out
    assert "IAM user hub-backup-acme allowed" in out and "hub-backup-tico-example-com allowed" not in out


def test_hub_backup_storage_takes_name_and_otherwise_the_saved_one(monkeypatch):
    s3, iam = S3(), IAM()
    d = fakes.deps()
    d.aws_clients = lambda region, profile: (None, iam, None, ACCOUNT)
    d.aws_s3 = lambda region, profile: s3
    argv = ["backup-storage", "--domain", "tico.example.com", "--aws-region", "us-west-2"]
    assert go([*argv, "--name", "acme", "--asset-prefix", "hub"], deps=d)[0] == 0
    code, out = go(argv, deps=d)  # a hub install named again without its prefix: refused before any AWS call
    assert code == 2 and "Run the command again with --asset-prefix hub" in out, out
    assert go([*argv, "--asset-prefix", "hub"], deps=d)[0] == 0  # its own saved name
    assert state.load_backup("tico.example.com")["name"] == "acme"
    other = {"domain": "other.example.com", "aws_name": "saved-name", "asset_prefix": "hub", "install_id": INSTALL}
    state.save("other.example.com", {**other, "target": "aws", "aws_region": "us-west-2"})
    other_argv = [
        "backup-storage",
        "--domain",
        "other.example.com",
        "--aws-region",
        "us-west-2",
        "--asset-prefix",
        "hub",
    ]
    assert go(other_argv, deps=d)[0] == 0
    assert [c["UserName"] for c in iam.called("create_user")] == ["hub-backup-acme", "hub-backup-saved-name"]
    code, out = go([*argv, "--asset-prefix", "hub", "--name", "other"], deps=d)
    assert code == 2 and "tico.example.com is set up with --name acme" in out
    code, out = go([*argv, "--asset-prefix", "hub", "--name", "Not_A_Name"], deps=d)
    assert code == 2 and "--name is 1 to 40" in out


# --- --asset-prefix hub -------------------------------------------------------------------------------------------

HUB = aws.Names("hub")
TICO_TAGS = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": "hotmic"}]
HUB_TAGS = [{"Key": "ManagedBy", "Value": "hub-setup"}, {"Key": "hub-setup-name", "Value": "hotmic"}]
INSTALL = "0123456789abcdef"
HUB_ID, TICO_ID = aws.Names("hub", INSTALL), aws.Names("tico", INSTALL)  # a new install's: tags carry its id
HUB_OURS = HUB_ID.tags("hotmic")
# What a strict install recorded making (its journal, saved as aws_ids), for the hub resources of two_installs.
HUB_IDS = {
    "instance": "i-hub",
    "eip": "eip-hub",
    "sg": "sg-hub",
    "role": "AROAhub-hotmic",
    "profile": "AIPAhub-hotmic",
    "param": "/hub/hotmic/env",
    "volumes": ["vol-root"],  # the launch's own root volume and primary network interface (as test_destroy_never_* has)
    "enis": ["eni-root"],
}


def test_the_prefix_is_checked_and_a_saved_one_wins():
    assert aws.asset_prefix(None, {}) == "tico" and aws.asset_prefix("hub", {}) == "hub"
    assert aws.asset_prefix(None, {"domain": "x"}) == "tico"  # saved before the flag existed: tico names
    assert aws.asset_prefix("hub", {"asset_prefix": "hub"}) == "hub"
    # A strict install's prefix must be named again by every command that changes it; only doctor reads it saved.
    assert aws.asset_prefix(None, {"asset_prefix": "hub"}, read_only=True) == "hub"
    for given in (None, "tico", "other"):
        with pytest.raises(
            MissingInput, match="set up with --asset-prefix hub, and every command .* --asset-prefix hub"
        ):
            aws.asset_prefix(given, {"asset_prefix": "hub"})
    with pytest.raises(MissingInput, match="set up with --asset-prefix hub"):
        aws.asset_prefix("tico", {"asset_prefix": "hub"}, read_only=True)  # a different one: refused even there
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


@pytest.mark.parametrize("prefix", ["abcdefghij"])
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
    p = hub_plan(plan_bucket="hub-setup-plans-abc")
    d, ec2, iam, ssm = deployed(p)
    named = HUB_OURS + [{"Key": "Name", "Value": "hub-hotmic"}]
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
    assert iam.called("create_instance_profile") == [{"InstanceProfileName": "hub-hotmic", "Tags": HUB_OURS}]
    assert ssm.called("put_parameter")[0]["Name"] == d.param_name == "/hub/hotmic/env"
    assert ssm.called("put_parameter")[0]["Tags"] == HUB_OURS and not ssm.called("add_tags_to_resource")
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
        self.filters, self.address_reads = [], []

    def _match(self, rows, Filters):
        self.filters.append(Filters)
        want = {f["Name"][4:]: f["Values"] for f in Filters if f["Name"].startswith("tag:")}
        return [
            r
            for r in rows
            if all({t["Key"]: t["Value"] for t in r.get("Tags", [])}.get(k) in v for k, v in want.items())
        ]

    def describe_instances(self, Filters=(), InstanceIds=()):
        rows = [i for i in self.instances if not InstanceIds or i["InstanceId"] in InstanceIds]
        states = [f["Values"] for f in Filters if f["Name"] == "instance-state-name"]
        if states:  # an instance without a State is a running one
            rows = [i for i in rows if (i.get("State") or {}).get("Name", "running") in states[0]]
        rows = self._match(rows, list(Filters))
        return {"Reservations": [{"Instances": rows if InstanceIds else self.listed(rows, "InstanceId")}]}

    def describe_security_groups(self, Filters=(), GroupIds=()):
        if GroupIds:
            return {"SecurityGroups": [g for g in self.sgs if g["GroupId"] in GroupIds]}
        return {"SecurityGroups": self.listed(self._match(self.sgs, Filters), "GroupId")}

    def describe_addresses(self, Filters=(), AllocationIds=()):
        self.address_reads.append({"Filters": Filters, **({"AllocationIds": AllocationIds} if AllocationIds else {})})
        rows = [a for a in self.eips if not AllocationIds or a["AllocationId"] in AllocationIds]
        rows = self._match(rows, list(Filters))
        return {"Addresses": rows if AllocationIds else self.listed(rows, "AllocationId")}


class LaggingEC2(FilteredEC2):
    """EC2 is eventually consistent: an address it allocated is missing from describe_addresses for `lag` reads. The
    first AllocateAddress answer is lost."""

    def __init__(self, lag):
        super().__init__()
        self.lag, self.reads, self.lost = lag, 0, False

    def allocate_address(self, **kw):
        a = super().allocate_address(**kw)
        a["AllocationId"] = f"eipalloc-{len(self.eips)}"
        self.reads = 0
        if not self.lost:
            self.lost = True
            raise Lost("allocate_address")
        return a

    def describe_addresses(self, Filters=(), AllocationIds=()):
        self.reads += 1
        return super().describe_addresses(Filters, AllocationIds) if self.reads > self.lag else {"Addresses": []}


def test_a_lost_allocate_address_is_waited_for_and_never_allocated_twice():
    # Codex review 5, case 11: AllocateAddress has no ClientToken. Its answer lost and the address not yet listed, the
    # retry allocated a second one and recorded it over the first. An open intent is now waited for, then refused.
    for lag, found in ((3, True), (50, False)):
        ids, sleeps = {}, []
        ec2 = LaggingEC2(lag)
        d = owned.StrictDeployer(hub_plan(), ec2, None, None, ACCOUNT, sleep=sleeps.append, ids=ids)
        with pytest.raises(Lost):
            d.ensure_eip()
        assert ids["intents"]["eip"] and "eip" not in ids
        ec2.reads = 0
        if found:
            assert d.ensure_eip() == ("eipalloc-1", "203.0.113.9") and ids["eip"] == "eipalloc-1"
            assert not ids.get("intents", {}).get("eip")
        else:
            with pytest.raises(MissingInput, match="Elastic IP") as e:
                d.ensure_eip()
            assert "describe-addresses" in str(e.value) and ids["intents"]["eip"] and "eip" not in ids
            assert sum(sleeps) <= 60
        assert len(ec2.called("allocate_address")) == 1, lag


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
    iam.profiles = {
        n: {"Tags": tags, "Roles": [n]} for n, tags in (("tico-hotmic", TICO_TAGS), ("hub-hotmic", HUB_TAGS))
    }

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
        (
            ["--domain", "hub.hotmicapp.com", "--asset-prefix", "hub"],
            "hub",
            "tico",
        ),  # the saved install's account, region
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
    if prefix == "hub":  # strict mode: never by the prefix and name tags alone, without a saved install id
        assert code == 2 and "nothing was deleted" in out and "Check by hand" in out, out
        assert not any(deleted(ec2, iam, ssm).values())
        return
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
    s3, iam, values = created_backup({"names": HUB_ID})
    bucket = f"hub-backup-tico-example-com-{INSTALL}"  # the domain's slug, then the install id
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
        == s3.called("create_bucket")[0]["CreateBucketConfiguration"]["Tags"]
        == HUB_ID.tags("acme")
    )
    lines = "\n".join(backup.aws_plan_lines("hub.hotmicapp.com", "us-west-2", ACCOUNT, HUB, "hotmic"))
    assert f"S3 bucket hub-backup-hub-hotmicapp-com-{ACCOUNT} in us-west-2" in lines
    assert (
        "IAM user hub-backup-hotmic allowed" in lines and f"to s3://hub-backup-hub-hotmicapp-com-{ACCOUNT}/hub" in lines
    )
    assert "tico" not in lines.lower()


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
    code, out = go(argv, deps=d)  # the re-run, naming its prefix again
    assert code == 0 and "already exists; reusing it" in out, out
    code, out = go(["destroy", "--domain", DOMAIN, "--dns", "--yes", "--asset-prefix", "hub"], deps=d)
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
    install = state.load(DOMAIN)[0]["install_id"]  # buckets under another prefix end in the install id
    assert f"TICO_BACKUP_URL=s3://hub-backup-hub-hotmicapp-com-{install}/hub" in user_data
    assert re.search(rf"TICO_COMPANY_FILE_S3=s3://hub-setup-plans-{install}/plans/hotmic/[0-9a-f]{{64}}", user_data)


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
        assert code == 2 and "set up with --asset-prefix hub" in out, (argv, out)
        assert "Run the command again with --asset-prefix hub" in out, (argv, out)
    assert len(log) == before  # refused before any AWS call
    # backup-storage, from the same computer: the saved name.
    assert (
        go(["backup-storage", "--domain", DOMAIN, "--aws-region", "us-west-2", "--asset-prefix", "hub"], deps=d)[0] == 0
    )
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
            "--asset-prefix",
            "hub",
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
    for other in (["--asset-prefix", "tico"], []):
        code, out = go([*argv, *other], deps=d)
        assert code == 2 and "set up with --asset-prefix hub" in out, out
    assert go([*argv, "--asset-prefix", "hub"], deps=d)[0] == 0
    assert ssm.called("put_parameter")[-1]["Name"] == "/hub/runner-b1/env"


# --- never touch what this setup did not make ---------------------------------------------------------------------


def test_a_backup_bucket_another_prefix_made_is_refused_before_any_write():
    """hub + backup-foo.example.com and hub-backup + foo.example.com spell the same bucket."""
    s3, iam = S3(), IAM()
    kw = dict(region="us-west-2", account_id=ACCOUNT, say=lambda _: None)
    owned.create_s3(s3, iam, domain="backup-foo.example.com", name="a", names=HUB, **kw)
    bucket = HUB.backup_bucket("backup-foo.example.com", ACCOUNT)
    assert aws.Names("hub-backup").backup_bucket("foo.example.com", ACCOUNT) == bucket
    before, users = len(s3.calls), dict(iam.users)
    with pytest.raises(MissingInput, match=f"S3 bucket {bucket} .*another --asset-prefix"):
        owned.create_s3(s3, iam, domain="foo.example.com", name="b", names=aws.Names("hub-backup"), **kw)
    assert [n for n, _ in s3.calls[before:]] == ["head_bucket", "get_bucket_tagging"]
    assert iam.users == users and len(iam.called("put_user_policy")) == 1
    assert all(c[1].get("ExpectedBucketOwner") == ACCOUNT for c in s3.calls[before:])


def test_an_untagged_backup_bucket_is_refused_and_a_new_one_is_tagged_in_its_create_call():
    s3, iam = S3(), IAM()
    bucket = HUB_ID.backup_bucket("tico.example.com", ACCOUNT)
    s3.tagsets[bucket] = []  # exists, made by someone else
    with pytest.raises(MissingInput, match=f"S3 bucket {bucket} "):
        owned.create_s3(
            s3, iam, domain="tico.example.com", region="us-west-2", account_id=ACCOUNT, name="acme", names=HUB_ID
        )
    assert [n for n, _ in s3.calls] == ["head_bucket", "get_bucket_tagging"]
    assert not [n for n, _ in iam.calls if not n.startswith(READS)]
    s3, _, _ = created_backup({"names": HUB_ID})
    writes = [n for n, _ in s3.calls if n not in ("head_bucket", "get_bucket_tagging")]
    assert writes[0] == "create_bucket" and "put_bucket_tagging" not in writes
    assert s3.called("create_bucket")[0]["CreateBucketConfiguration"]["Tags"] == HUB_ID.tags("acme")


def test_a_runner_takes_its_own_servers_prefix_not_another_servers_runner_of_the_same_name(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    old = {"server_url": "https://a.example", "label": "b1", "target": "aws", "aws_region": "us-west-2"}
    old |= {"aws_profile": "", "instance_id": "i-a", "name": "runner-b1"}  # from before the flag: tico
    state.save_runner("runner-b1", old)
    state.save(DOMAIN, {"domain": DOMAIN, "aws_name": "hotmic", "asset_prefix": "hub"})
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--cloud", "aws", "--server-url", f"https://{DOMAIN}", "--label", "b1"]
    argv += ["--aws-region", "us-west-2", "--asset-prefix", "hub"]
    code, out = go([*argv, "--dry-run"], deps=d)
    assert code == 0 and "Security group hub-runner-b1:" in out, out
    assert go([*argv, "--yes"], deps=d)[0] == 0
    assert ssm.called("put_parameter")[0]["Name"] == "/hub/runner-b1/env"
    runners = {r["server_url"]: r for r in state.load_runners()}
    assert runners["https://a.example"] == old and runners[f"https://{DOMAIN}"]["asset_prefix"] == "hub"
    # Another hub server's runner b1 in that region would be hub.hotmicapp.com's runner: refused before any AWS call.
    state.save("c.example.com", {"domain": "c.example.com", "aws_name": "c", "asset_prefix": "hub"})
    before = len(log)
    argv[argv.index(f"https://{DOMAIN}")] = "https://c.example.com"
    code, out = go([*argv, "--yes"], deps=d)
    assert code == 2 and f"hub-runner-b1 is already the runner of https://{DOMAIN} (us-west-2)" in out, out
    assert len(log) == before


def test_a_name_other_than_the_domains_saved_one_is_refused_before_any_aws_call(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    clients = d.aws_clients
    opened = []
    d.aws_clients = lambda region, profile: opened.append(region) or clients(region, profile)
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    before = (len(log), len(opened))
    msg = f"{DOMAIN} is set up with --name hotmic"
    for argv in (
        hub_args(tmp_path, "--asset-prefix", "hub", "--name", "production"),
        ["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--name", "production", "--yes"],
    ):
        code, out = go(argv, deps=d)
        assert code == 2 and msg in out and "Leave --name out or pass --name hotmic" in out, out
    assert (len(log), len(opened)) == before
    # Saved before install ids: destroy takes --name as it always has.
    state.save("old.example.com", {"domain": "old.example.com", "target": "aws", "aws_instance_id": "i-1"})
    code, out = go(["destroy", "--domain", "old.example.com", "--name", "legacy", "--yes"], deps=d)
    assert code == 0 and "Nothing tagged ManagedBy=tico-setup, tico-setup-name=legacy" in out, out
    code, out = go(["destroy", "--domain", "old.example.com", "--name", "hotmic", "--yes"], deps=d)
    assert code == 2 and "made with --asset-prefix hub" in out, out  # review 5, case 1: the name is the hub install's


READS = ("describe_", "list_", "get_", "head_")


def test_an_ssm_parameter_setup_did_not_make_is_refused_before_any_write(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    ssm.params["/hub/hotmic/env"] = []  # someone else's, untagged
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)
    assert code == 2 and "SSM parameter /hub/hotmic/env already exists and was not made by this setup" in out, out
    assert "Pick another --name or --asset-prefix." in out
    assert [n for n, _ in log if not n.startswith(READS)] == []
    # The deployer on its own never overwrites or tags it either.
    dep = strict_dep(hub_plan(domain=DOMAIN), ec2, iam, ssm)
    with pytest.raises(MissingInput, match="SSM parameter /hub/hotmic/env"):
        dep.put_env("X=1\n")
    assert not ssm.called("put_parameter") and not ssm.called("add_tags_to_resource")
    # A missing one is created without overwrite, tagged in the same call.
    del ssm.params["/hub/hotmic/env"]
    dep.put_env("X=1\n")
    (put,) = ssm.called("put_parameter")
    assert put["Overwrite"] is False and put["Tags"] == HUB_OURS


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
    dep = strict_dep(hub_plan(domain=DOMAIN), ec2, iam, ssm)
    with pytest.raises(MissingInput, match="IAM instance profile hub-hotmic"):
        dep.ensure_role()
    assert not iam.called("add_role_to_instance_profile")
    # A tagged one holding another role is not ours either.
    iam.profiles["hub-hotmic"] = {"Tags": HUB_TAGS, "Roles": ["production-app"]}
    with pytest.raises(MissingInput, match="IAM instance profile hub-hotmic"):
        dep.ensure_role()
    # Destroy leaves a profile its install did not make, and the role with it.
    iam.roles["hub-hotmic"] = HUB_ID.tags("hotmic")
    for tags in ([], PRODUCTION, HUB_TAGS, HUB_ID.tags("hotmic")):
        iam.calls.clear()
        held = ["production-app"] if tags == HUB_ID.tags("hotmic") else []
        iam.profiles["hub-hotmic"] = {"Tags": tags, "Roles": held}
        out = []
        owned.destroy(
            ec2,
            iam,
            ssm,
            {"iam-role": ["hub-hotmic"]},
            say=out.append,
            sleep=lambda s: None,
            names=HUB_ID,
            name="hotmic",
            ids={"role": "AROAhub-hotmic"},
        )
        assert not iam.called("delete_instance_profile") and not iam.called("remove_role_from_instance_profile")
        assert "left alone: IAM instance profile hub-hotmic was not made by this install" in "\n".join(out)


def test_a_profile_setup_made_without_its_role_gets_it_on_a_rerun():
    iam = IAM()
    iam.profiles["hub-hotmic"] = {"Tags": HUB_OURS, "Roles": []}  # an earlier run stopped between the two calls
    dep = strict_dep(hub_plan(domain=DOMAIN), fakes.FakeEC2(), iam, fakes.FakeSSM(), ids={"profile": "AIPAhub-hotmic"})
    assert dep.ensure_role() == "hub-hotmic"
    assert iam.called("add_role_to_instance_profile") == [
        {"InstanceProfileName": "hub-hotmic", "RoleName": "hub-hotmic"}
    ]


def test_a_route53_record_setup_did_not_make_is_refused_and_changed_by_hand(tmp_path):
    # Codex review 5, case 10: strict mode has no exceptions. --replace-dns (which deleted a foreign record) is gone; setup
    # prints the exact change for a person to make, and acts again only once the name is free or this install's.
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    production = {"Name": f"{DOMAIN}.", "Type": "A", "TTL": 60, "ResourceRecords": [{"Value": "198.51.100.50"}]}
    r53.records = [dict(production)]
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    code, out = go(argv, deps=d)
    assert code == 2 and f"Route 53 already has A {DOMAIN} -> 198.51.100.50" in out, out
    assert "by hand" in out and "--replace-dns" not in out and [n for n, _ in log if not n.startswith(READS)] == []
    assert r53.records == [production]
    with pytest.raises(SystemExit):  # no such flag any more
        go([*argv, "--replace-dns"], deps=d)
    assert r53.records == [production]
    r53.records = []  # the person removed it by hand
    code, out = go(argv, deps=d)
    assert code == 0, out
    assert [r["ResourceRecords"] for r in r53.records] == [[{"Value": "203.0.113.9"}]]
    saved = state.load(DOMAIN)[0]
    assert saved["dns_records"] == r53.records and saved["dns_zone_id"] == "Z1" and saved.get("dns_pending", []) == []
    assert go(argv, deps=d)[0] == 0  # re-runs keep working while the record is exactly the one it made
    # Codex review 3, case 2: a record repointed (at production, say) is no longer the one it made: refused, untouched,
    # with the exact change to make by hand.
    r53.records[0]["ResourceRecords"] = [{"Value": "198.51.100.77"}]
    before = len(r53.called("change_resource_record_sets"))
    code, out = go(argv, deps=d)
    assert code == 2 and f"Route 53 already has A {DOMAIN} -> 198.51.100.77" in out, out
    assert f"A {DOMAIN} -> 203.0.113.9" in out and "by hand" in out, out
    assert r53.records[0]["ResourceRecords"] == [{"Value": "198.51.100.77"}]
    assert len(r53.called("change_resource_record_sets")) == before


def test_strict_mode_uploads_the_plan_only_to_a_bucket_it_recorded_making(tmp_path):
    # Codex review 5, case 10: an install tag on a --plan-bucket is not proof that setup made it. Strict mode refuses a
    # --plan-bucket before any AWS write; leave it out and setup makes (and records) its own.
    d, ec2, iam, ssm, s3, log = hub_world()
    s3.tagsets["prod-assets"] = PRODUCTION
    code, out = go(
        hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic", "--plan-bucket", "prod-assets"), deps=d
    )
    assert code == 2 and "--plan-bucket" in out and "prod-assets" in out, out
    assert [n for n, _ in log if not n.startswith(READS)] == []


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
    code, out = go(argv, deps=d)  # the retry without the flag: refused, nothing sent
    assert code == 2 and "Run the command again with --asset-prefix hub" in out, out
    assert go([*argv, "--asset-prefix", "hub"], deps=d)[0] == 0
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
    for other in ([], ["--asset-prefix", "tico"]):  # the retry must name its prefix again
        code, out = go([*argv, *other], deps=d)
        assert code == 2 and "Run the command again with --asset-prefix hub" in out, out
    code, out = go([*argv, "--asset-prefix", "hub"], deps=d)
    assert code == 0 and "s3://hub-backup-laptop-example-com-" in out, out
    assert [c["UserName"] for c in iam.called("create_user")] == ["hub-backup-laptop-example-com"]
    assert state.load("laptop.example.com") == ({}, "")  # no server state is made or changed for it
    assert state.known_domains() == []


class StopAtPlan(fakes.CaptureIO):
    def confirm(self, question, default=True):
        return False if "Go ahead" in question else default


def test_a_prompted_domain_restores_its_whole_saved_install(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    io = StopAtPlan(interactive=True, answers=["", DOMAIN])  # the target's default, then the domain
    code, out = go(["--asset-prefix", "hub"], deps=d, io=io)
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
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub"), deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--yes"], deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    assert not [c for c, _ in log if not c.startswith(READS)]
    d.aws_clients = lambda region, profile: (Spy(ec2, log), Spy(iam, log), Spy(ssm, log), HOTMIC_ACCOUNT)
    code, out = go([*hub_args(tmp_path, "--asset-prefix", "hub"), "--aws-region", "us-east-1"], deps=d)
    assert code == 2 and "in us-west-2" in out and "cannot change" in out, out
    code, out = go(
        ["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--aws-region", "us-east-1", "--yes"], deps=d
    )
    assert code == 2 and "in us-west-2" in out, out
    assert not [c for c, _ in log if not c.startswith(READS)]


def test_destroy_without_a_saved_install_id_works_as_before():
    # Codex review 3, case 11: an older install's destroy, and HQ's state-free day-7 command, refused (dry run too).
    # Without a saved install id, destroy fills in the name, prefix and region as it always has, and the dry run lists.
    d, ec2, iam, ssm = two_installs()
    state.save("old.example.com", {"domain": "old.example.com", "aws_name": "hotmic", "target": "aws"})  # no account
    code, out = go(["destroy", "--domain", "old.example.com", "--dry-run"], deps=d)
    assert code == 0 and "instance: i-tico" in out and "Dry run: nothing was deleted." in out, out
    assert deleted(ec2, iam, ssm)["instances"] == []
    code, out = go(["destroy", "--name", "hotmic", "--yes"], deps=d)
    assert code == 0 and deleted(ec2, iam, ssm)["instances"] == ["i-tico"], out  # the default prefix's, never i-hub


def test_an_install_saved_before_the_account_was_adopts_it_once_after_the_tag_proof(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    saved = state.load(DOMAIN)[0]
    del saved["aws_account_id"]
    state.save(DOMAIN, saved)
    assert go(hub_args(tmp_path, "--asset-prefix", "hub"), deps=d)[0] == 0
    assert state.load(DOMAIN)[0]["aws_account_id"] == HOTMIC_ACCOUNT


def test_a_runner_and_backup_storage_keep_their_aws_account(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://other.example"]
    argv += ["--label", "b1", "--aws-region", "us-west-2", "--asset-prefix", "hub"]
    assert go(argv, deps=d)[0] == 0
    assert state.load_runners()[0]["aws_account_id"] == HOTMIC_ACCOUNT
    bs = ["backup-storage", "--domain", "laptop.example.com", "--aws-region", "us-west-2", "--asset-prefix", "hub"]
    assert go(bs, deps=d)[0] == 0 and state.load_backup("laptop.example.com")["aws_account_id"] == HOTMIC_ACCOUNT
    d.aws_clients = lambda region, profile: (Spy(ec2, log), Spy(iam, log), Spy(ssm, log), "999999999999")
    log.clear()
    code, out = go(argv, deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    code, out = go(bs, deps=d)
    assert code == 2 and f"set up in AWS account {HOTMIC_ACCOUNT}" in out, out
    code, out = go([*bs[:4], "us-east-1", "--asset-prefix", "hub"], deps=d)
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
    for tags in (PRODUCTION, HUB_TAGS):
        s3, iam = RacingS3(tags), IAM()
        with pytest.raises(MissingInput, match="was not made by this setup"):
            owned.create_s3(
                s3, iam, domain="tico.example.com", region="us-east-1", account_id=ACCOUNT, name="acme", names=HUB
            )
        assert not s3.called("put_bucket_tagging") and not iam.called("create_user")
        assert [n for n, _ in s3.calls] == ["head_bucket", "create_bucket", "get_bucket_tagging"]


def test_an_untagged_bucket_is_never_adopted():
    # Codex review 3, case 4: someone else's untagged bucket of the name was adopted and retagged. Strict mode's buckets
    # are tagged in the create call, so none of setup's is untagged.
    s3 = S3()
    s3.tagsets["hub-setup-plans-x"] = []
    with pytest.raises((MissingInput, SystemExit), match="refusing to reuse"):
        strict_dep(hub_plan(region="us-east-1", plan_bucket="hub-setup-plans-x"), None, None, None).ensure_plan_bucket(
            s3, "us-east-1"
        )
    assert not s3.called("put_bucket_tagging") and not s3.called("create_bucket")


def test_a_boto3_too_old_to_tag_a_bucket_at_creation_stops_before_making_one():
    class ParamValidationError(Exception):
        pass

    s3 = S3()
    s3.create_bucket = lambda **kw: (_ for _ in ()).throw(ParamValidationError("Unknown parameter Tags"))
    p = hub_plan(plan_bucket="hub-setup-plans-x")
    with pytest.raises(SystemExit, match="pip install -U boto3"):
        strict_dep(p, None, None, None).ensure_plan_bucket(s3, "us-west-2")
    assert "hub-setup-plans-x" not in s3.tagsets


def saved_install(domain="x.example.com", prefix="hub", **extra):
    """A whole install saved with an install id, as a new install saves it."""
    rec = {"domain": domain, "target": "aws", "aws_name": "hotmic", "aws_region": "us-east-1", "asset_prefix": prefix}
    state.save(domain, {**rec, "aws_account_id": ACCOUNT, "install_id": INSTALL, "aws_ids": HUB_IDS, **extra})
    return ["destroy", "--domain", domain, "--yes", *(["--asset-prefix", prefix] if prefix != "tico" else [])]


@pytest.mark.parametrize("kind", ["volume", "network interface"])
def test_destroy_never_terminates_an_instance_that_would_take_something_setup_did_not_make(kind):
    # Codex review 3, case 9: foreign network interfaces attached with DeleteOnTermination go with the instance too.
    d, ec2, iam, ssm = two_installs()
    ec2.instances[1]["Tags"] = HUB_OURS
    attach = lambda dot: {"InstanceId": "i-hub", "DeleteOnTermination": dot}  # noqa: E731
    ec2.volumes = [{"VolumeId": "vol-root", "Tags": HUB_OURS, "Attachments": [attach(True)]}]
    ec2.enis = [{"NetworkInterfaceId": "eni-root", "TagSet": HUB_OURS, "Attachment": attach(True)}]
    for dot, tags in ((True, PRODUCTION), (False, PRODUCTION), (True, HUB_TAGS)):  # HUB_TAGS: no install id
        if kind == "volume":
            ec2.volumes.append({"VolumeId": "vol-prod", "Tags": tags, "Attachments": [attach(dot)]})
        else:
            ec2.enis.append({"NetworkInterfaceId": "eni-prod", "TagSet": tags, "Attachment": attach(dot)})
        argv = saved_install()
        code, out = go(argv, deps=d)
        prod = "vol-prod" if kind == "volume" else "eni-prod"
        if dot:
            assert code == 2 and prod in out and "root" not in out.split("would also delete")[-1], out
            assert deleted(ec2, iam, ssm)["instances"] == []
            with pytest.raises(MissingInput, match=prod):  # and again right before the terminate call itself
                owned.destroy(
                    ec2, iam, ssm, {"instance": ["i-hub"]}, print, None, names=HUB_ID, name="hotmic", ids=HUB_IDS
                )
        else:
            assert code == 0 and deleted(ec2, iam, ssm)["instances"] == ["i-hub"], out
            ec2.calls.clear()
        ec2.volumes, ec2.enis = ec2.volumes[:1], ec2.enis[:1]


def test_a_role_or_backup_user_replaced_between_the_proof_and_the_write_is_never_written():
    p = hub_plan(domain=DOMAIN)
    for replacement in ({"RoleId": "AROAOTHER", "Tags": HUB_OURS}, {"RoleId": "AROAOTHER", "Tags": PRODUCTION}):
        iam = IAM()
        iam.roles["hub-hotmic"] = {"RoleId": "AROAFIRST", "Tags": HUB_OURS}
        dep = strict_dep(p, fakes.FakeEC2(), iam, fakes.FakeSSM(), ids={"role": "AROAFIRST"})
        dep.preflight()  # proven: setup's role
        iam.roles["hub-hotmic"] = replacement  # deleted and made again by someone else
        with pytest.raises((MissingInput, SystemExit), match="hub-hotmic"):
            dep.ensure_role()
        assert not iam.called("attach_role_policy") and not iam.called("put_role_policy")
    ssm = fakes.FakeSSM(param_tags=HUB_OURS)  # an existing parameter is overwritten, never re-tagged
    strict_dep(p, fakes.FakeEC2(), IAM(), ssm, ids={"param": "/hub/hotmic/env"}).put_env("A=1")
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
        owned.create_s3(
            s3,
            iam,
            domain="tico.example.com",
            region="us-west-2",
            account_id=ACCOUNT,
            name="acme",
            names=HUB,
            ids={"backup_user": "AIDAFIRST"},
        )
    assert not iam.called("put_user_policy") and not iam.called("create_access_key")


def test_a_record_made_between_the_read_and_the_write_is_never_overwritten():
    r53 = FakeRoute53(ZONE)
    want = [dns.Record("A", DOMAIN, "203.0.113.9")]
    changes, created, found, kept = route53.plan(r53, "Z1", want, [])
    assert found == kept == [] and [c["Action"] for c in changes] == ["CREATE"]
    assert created == [{"Name": DOMAIN, "Type": "A", "TTL": want[0].ttl, "ResourceRecords": [{"Value": "203.0.113.9"}]}]
    production = {"Name": f"{DOMAIN}.", "Type": "A", "TTL": 60, "ResourceRecords": [{"Value": "198.51.100.50"}]}
    r53.records = [production]  # someone else's, made in that instant
    with pytest.raises(fakes.InvalidChangeBatch):
        route53.apply(r53, "Z1", changes, HUB)
    assert r53.records == [production]


def test_a_matching_record_setup_did_not_make_is_left_alone_and_never_deleted(tmp_path):
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    r53.fail = True  # the first run cannot reach Route 53, so the person adds the record by hand
    assert go(argv, deps=d)[0] == 0 and state.load(DOMAIN)[0].get("dns_records", []) == []
    manual = {"Name": f"{DOMAIN}.", "Type": "A", "TTL": 3600, "ResourceRecords": [{"Value": "203.0.113.9"}]}
    r53.fail, r53.records = False, [dict(manual)]
    code, out = go(argv, deps=d)
    assert code == 0, out
    assert r53.records == [manual] and not r53.called("change_resource_record_sets")  # TTL kept, nothing written
    assert state.load(DOMAIN)[0].get("dns_records", []) == []
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--dns", "--yes"], deps=d)
    assert code == 0 and f"Left alone: A {DOMAIN} -> 203.0.113.9 is not the record this install made" in out, out
    assert r53.records == [manual] and not r53.called("change_resource_record_sets")


def test_another_servers_runner_role_in_another_region_is_never_reused(monkeypatch):
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    # Made from another computer for https://a.example in us-east-2: IAM roles and profiles are global.
    theirs = HUB.tags("runner-b1") + [
        {"Key": "hub-setup-server", "Value": owned.server_identity("hub", "https://a.example", "us-east-2")}
    ]
    iam.roles["hub-runner-b1"] = {"RoleId": "AROATHEIRS", "Tags": theirs}
    iam.profiles["hub-runner-b1"] = {"Tags": theirs, "Roles": ["hub-runner-b1"]}
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://c.example.com"]
    argv += ["--asset-prefix", "hub"]
    code, out = go([*argv, "--label", "b1", "--aws-region", "us-west-2"], deps=d)
    assert code == 2 and "IAM role hub-runner-b1" in out, out
    assert not iam.called("put_role_policy") and not ssm.called("put_parameter") and not ec2.called("run_instances")
    # From this computer too, the saved record of a.example's runner in another region refuses it before any AWS call.
    rec = {"server_url": "https://a.example", "label": "b1", "target": "aws", "aws_region": "us-east-2"}
    state.save_runner("a", {**rec, "name": "runner-b1", "instance_id": "i-a", "asset_prefix": "hub"})
    before = len(log)
    code, out = go([*argv, "--label", "b1", "--aws-region", "us-west-2"], deps=d)
    assert code == 2 and "already the runner of https://a.example (us-east-2)" in out and len(log) == before, out
    # Its own runner, made here, carries its own install id on the role, profile and parameter.
    code, out = go([*argv, "--label", "b2", "--aws-region", "us-west-2"], deps=d)
    assert code == 0, out
    (rec,) = [r for r in state.load_runners() if r.get("name") == "runner-b2"]
    mine = {"Key": "hub-setup-install", "Value": rec["install_id"]}
    assert mine in iam.called("create_role")[0]["Tags"] and mine in iam.profiles["hub-runner-b2"]["Tags"]
    assert mine in ssm.called("put_parameter")[0]["Tags"]


def test_runner_records_of_servers_that_slug_alike_never_share_a_file():
    keys = set()
    for url in ("https://a-b.example.com", "https://a.b.example.com", "https://a.b.example.com:8443"):
        for prefix in ("tico", "hub"):
            s = Settings(server_url=url, aws_region="us-west-2", asset_prefix=prefix)
            keys.add(runner_setup.runner_key(s, "runner-b1"))
    long = "https://" + "x" * 45 + ".example.com"
    for suffix in ("", "/a"):
        keys.add(runner_setup.runner_key(Settings(server_url=long + suffix, aws_region="us-west-2"), "runner-b1"))
    assert len(keys) == 8


def test_a_role_without_a_profile_or_beside_someone_elses_is_modelled_and_handled_apart():
    ec2, ssm = fakes.FakeEC2(), fakes.FakeSSM()
    ours = HUB_ID.tags("hotmic")
    iam = fakes.FakeIAM(role_tags=ours, profile_tags=None)  # setup's role; its profile was never made
    assert owned.instance_profile(iam, "hub-hotmic") is None
    kw = dict(say=print, sleep=lambda s: None, names=HUB_ID, name="hotmic", ids={"role": "AROAhub-hotmic"})
    owned.destroy(ec2, iam, ssm, {"iam-role": ["hub-hotmic"]}, **kw)
    assert iam.called("delete_role") == [{"RoleName": "hub-hotmic"}] and not iam.called("delete_instance_profile")
    with pytest.raises(fakes._Exc.NoSuchEntityException):
        iam.get_instance_profile(InstanceProfileName="hub-hotmic")  # still none: never made up from the role's tags
    iam = fakes.FakeIAM(role_tags=ours, profile_tags=PRODUCTION)  # someone else's profile of that name
    owned.destroy(ec2, iam, ssm, {"iam-role": ["hub-hotmic"]}, **kw)
    assert not iam.called("delete_role") and not iam.called("delete_instance_profile")
    dep = strict_dep(hub_plan(domain=DOMAIN), ec2, iam, ssm, ids={"role": "AROAhub-hotmic"})
    with pytest.raises(MissingInput, match="IAM instance profile hub-hotmic"):
        dep.preflight()


@pytest.mark.parametrize("region", ["us-west-2", "us-east-2"])
def test_two_installs_sharing_a_prefix_and_name_never_touch_each_others_resources(tmp_path, region):
    # Codex review 3, case 1: the prefix and name tags alone let a second domain reuse the first's server and env, and
    # another region rewrite its (global) IAM role. The install id, saved before the first write, tells them apart.
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    first = state.load(DOMAIN)[0]
    other = "hub2.hotmicapp.com"
    r = fakes.FakeResolver({(DOMAIN, dns.A): ["203.0.113.9"], (other, dns.A): ["203.0.113.9"]}, {ZONE: fakes.R53_NS})
    d.resolver, d.public_resolvers = r, (lambda: [("Google", r)])
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    argv[argv.index(DOMAIN)] = other
    argv[argv.index("us-west-2")] = region
    if region != "us-west-2":  # EC2 and SSM are regional; IAM is global, so only the role and profile are shared
        west, east = d.aws_clients, (FilteredEC2(), fakes.FakeSSM())
        d.aws_clients = lambda r, p: (
            west(r, p) if r == "us-west-2" else (*map(Spy, (east[0], iam, east[1]), [log] * 3), HOTMIC_ACCOUNT)
        )
    before = len(log)
    code, out = go(argv, deps=d)
    what = "SSM parameter /hub/hotmic/env" if region == "us-west-2" else "IAM role hub-hotmic"
    assert code == 2 and f"{what} already exists and was not made by this setup" in out, out
    writes = [n for n, _ in log[before:] if not n.startswith(("describe_", "get_", "list_", "head_"))]
    assert not [n for n in writes if n in ("put_parameter", "put_role_policy", "attach_role_policy", "run_instances")]
    assert first["install_id"] and state.load(other)[0]["install_id"] != first["install_id"]
    # The first install's re-run still owns everything it made.
    code, out = go(hub_args(tmp_path, "--asset-prefix", "hub"), deps=d)
    assert code == 0 and "already exists; reusing it" in out, out


def test_a_new_hub_installs_buckets_end_in_its_install_id_and_an_existing_name_is_never_created_again():
    # Codex review 3, case 3: us-east-1's CreateBucket answers success for a bucket this account holds, and resets its
    # ACLs. Names ending in the install id are held by no one else, and a name head_bucket finds is never created.
    names = aws.Names("hub", "0123456789abcdef")
    assert names.backup_bucket("hub.hotmicapp.com", ACCOUNT) == "hub-backup-hub-hotmicapp-com-0123456789abcdef"
    long = names.backup_bucket("x" * 60 + ".example.com", ACCOUNT)
    assert len(long) <= 63 and long.endswith("-0123456789abcdef")
    assert (
        aws.Names("tico", "0123456789abcdef").backup_bucket("a.example", ACCOUNT) == f"tico-backup-a-example-{ACCOUNT}"
    )
    for tags in ([], PRODUCTION, aws.Names("hub", "fedcba9876543210").tags("acme")):
        s3, iam = S3(), IAM()
        s3.tagsets[names.backup_bucket("a.example", ACCOUNT)] = tags
        with pytest.raises(MissingInput, match="was not made by this setup"):
            owned.create_s3(
                s3, iam, domain="a.example", region="us-east-1", account_id=ACCOUNT, name="acme", names=names
            )
        assert not s3.called("create_bucket") and not [n for n, _ in iam.calls if not n.startswith(READS)]


def test_the_plan_goes_only_to_a_bucket_setup_recorded_making_and_its_objects_are_never_overwritten():
    # Codex review 3, case 8 and review 5, case 10: owned by the account, or tagged for this install, is not enough when
    # the account holds production's buckets; an object at the plan's key was overwritten, then deleted.
    install, saved = "0123456789abcdef", []
    p = aws.plan_aws(
        domain="a.example",
        front_door="caddy",
        region="us-east-1",
        name="acme",
        plan_bucket="hub-setup-plans-x",
        prefix="hub",
        install_id=install,
    )
    d = strict_dep(p, None, None, None, ids={}, save=lambda: saved.append(dict(d.ids)))
    s3 = S3()
    s3.tagsets["hub-setup-plans-x"] = d.n.tags("acme")  # tagged for this install, but setup never recorded making it
    with pytest.raises(MissingInput, match="--plan-bucket hub-setup-plans-x"):
        d.verify_plan_bucket(s3)
    key = "plans/acme/" + "a" * 64
    with pytest.raises(MissingInput):
        d.put_plan(s3, key, "ours")
    assert not s3.called("put_object") and not s3.called("delete_object")
    d.ids["plan_bucket"] = "hub-setup-plans-x"  # the one it recorded making
    theirs = {"Body": b"production's", "Metadata": {}, "ETag": '"theirs"'}
    s3.objects[("hub-setup-plans-x", key)] = theirs
    with pytest.raises(MissingInput, match="did not record writing it"):
        d.put_plan(s3, key, "ours")
    assert s3.objects[("hub-setup-plans-x", key)] == theirs and d.delete_plan(s3, key)
    assert s3.objects[("hub-setup-plans-x", key)] == theirs and not s3.called("delete_object")
    # Its own: written only where nothing is, recorded, and only then deleted; a retry of a lost response is its own.
    other = "plans/acme/" + "b" * 64
    d.put_plan(s3, other, "ours")
    assert {k: saved[-1]["plan_object"][k] for k in ("key", "etag")} == {"key": other, "etag": etag(b"ours")}
    d.put_plan(s3, other, "ours")
    assert d.delete_plan(s3, other) == "" and ("hub-setup-plans-x", other) not in s3.objects


def test_a_create_whose_answer_was_lost_is_known_for_its_own_on_the_retry_and_destroyed(tmp_path):
    # Codex review 3, case 12: Route 53 applied the CREATE but its answer never came back, and the retry left the record
    # unowned, so destroy --dns left it behind. The CREATE is journalled before it is sent.
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    real, lost = r53.change_resource_record_sets, []

    def answer_lost(**kw):
        real(**kw)
        if not lost:
            lost.append(1)
            raise ConnectionError("read timeout")

    r53.change_resource_record_sets = answer_lost
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    with pytest.raises(ConnectionError):
        go(argv, deps=d)
    (made,) = r53.records
    saved = state.load(DOMAIN)[0]
    assert saved["dns_pending"] == [made] and saved.get("dns_records", []) == []
    code, out = go(argv, deps=d)  # review 5, case 7: found in the zone, but who made it? Asked, never assumed.
    assert code == 2 and f"--confirm-dns {DOMAIN}" in out, out
    code, out = go([*argv, "--confirm-dns", DOMAIN], deps=d)
    assert code == 0, out
    saved = state.load(DOMAIN)[0]
    assert saved["dns_records"] == [made] and saved.get("dns_pending", []) == []
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--dns", "--yes"], deps=d)
    assert code == 0 and "DNS records deleted." in out and r53.records == [], out


def id_install():
    """two_installs, with the hub one made by a new install (strict mode): every resource carries its install id."""
    d, ec2, iam, ssm = two_installs()
    for rows in (ec2.instances, ec2.sgs, ec2.eips):
        rows[1]["Tags"] = HUB_OURS
    iam.roles["hub-hotmic"], iam.profiles["hub-hotmic"]["Tags"] = HUB_OURS, HUB_OURS
    ssm.params["/hub/hotmic/env"] = HUB_OURS
    return d, ec2, iam, ssm


def test_destroy_reads_each_resource_again_right_before_deleting_it():
    # Codex review 3, case 5: a parameter replaced after it was listed was deleted. Each resource is read again right
    # before its delete, in order, and deleted only while it carries the install's tags and recorded id.
    d, ec2, iam, ssm = id_install()
    found = aws.discover(ec2, iam, ssm, "hotmic", names=HUB_ID)
    assert found == {
        "instance": ["i-hub"],
        "elastic-ip": ["eip-hub"],
        "security-group": ["sg-hub"],
        "iam-role": ["hub-hotmic"],
        "ssm-parameter": ["/hub/hotmic/env"],
    }
    # The install recorded another allocation: it is not this install's, and nothing is deleted.
    with pytest.raises(MissingInput, match="Elastic IP eip-hub carries this install's tags.*nothing was deleted"):
        owned.destroy(ec2, iam, ssm, found, print, None, names=HUB_ID, name="hotmic", ids={**HUB_IDS, "eip": "eip-o"})
    assert not any(deleted(ec2, iam, ssm).values())
    ssm.params["/hub/hotmic/env"] = PRODUCTION  # replaced since it was listed
    ec2.sgs[1]["Tags"] = HUB_TAGS  # retagged without the install id
    out = []
    owned.destroy(ec2, iam, ssm, found, say=out.append, sleep=lambda s: None, names=HUB_ID, name="hotmic", ids=HUB_IDS)
    gone = deleted(ec2, iam, ssm)
    assert gone["params"] == gone["sgs"] == [] and gone["instances"] == ["i-hub"] and gone["eips"] == ["eip-hub"]
    assert gone["roles"] == ["hub-hotmic"]
    said = "\n".join(out)
    for what in ("SSM parameter /hub/hotmic/env", "security group sg-hub"):
        assert f"left alone: {what}" in said, said
    # A role replaced (same tags, another RoleId) after the first delete step keeps its policies.
    d, ec2, iam, ssm = id_install()
    real = iam.detach_role_policy

    def replaced(**kw):
        real(**kw)
        iam.roles["hub-hotmic"] = {"RoleId": "AROAPRODUCTION", "Tags": HUB_ID.tags("hotmic")}

    iam.detach_role_policy = replaced
    out = []
    owned.destroy(
        ec2, iam, ssm, {"iam-role": ["hub-hotmic"]}, out.append, None, names=HUB_ID, name="hotmic", ids=HUB_IDS
    )
    assert not iam.called("delete_role_policy") and not iam.called("delete_role")
    assert "left alone: IAM role hub-hotmic" in "\n".join(out)


def test_destroy_finishes_an_install_interrupted_before_its_inline_policy():
    # Codex review 3, case 14: deleting an inline policy the role never got raises NoSuchEntity, as IAM does.
    d, ec2, iam, ssm = id_install()
    iam.no_policy.add("hub-hotmic")
    with pytest.raises(fakes._Exc.NoSuchEntityException):
        iam.delete_role_policy(RoleName="hub-hotmic", PolicyName="hub-env")
    code, out = go(saved_install(), deps=d)
    assert code == 0 and deleted(ec2, iam, ssm)["roles"] == ["hub-hotmic"], out
    assert deleted(ec2, iam, ssm)["params"] == ["/hub/hotmic/env"]


def test_an_elastic_ip_attached_to_another_server_is_never_moved_or_released():
    # Codex review 3, case 10: a setup-tagged address since attached to a production server was taken back with
    # AllowReassociation=True. Its attachment is checked; the call never forces it.
    p = hub_plan(domain="a.example", region="us-east-1")
    ec2 = fakes.FakeEC2()
    ec2.eips = [{"AllocationId": "eip-hub", "PublicIp": "203.0.113.9", "Tags": HUB_OURS}]
    ec2.eips[0]["InstanceId"] = "i-production"
    dep = strict_dep(p, ec2, fakes.FakeIAM(), fakes.FakeSSM(), ids={"eip": "eip-hub"})
    with pytest.raises(MissingInput, match="attached to i-production"):
        dep.associate("eip-hub", "i-hub")
    assert not ec2.called("associate_address")
    del ec2.eips[0]["InstanceId"]
    dep.associate("eip-hub", "i-hub")
    assert ec2.called("associate_address") == [
        {"AllocationId": "eip-hub", "InstanceId": "i-hub", "AllowReassociation": False}
    ]
    # Destroy leaves it too.
    d, ec2, iam, ssm = id_install()
    ec2.eips[1]["InstanceId"] = "i-production"
    code, out = go(saved_install(), deps=d)
    assert code == 0 and "left alone: Elastic IP eip-hub is attached to i-production" in out, out
    assert deleted(ec2, iam, ssm)["eips"] == []


def test_a_role_replaced_with_the_same_tags_gets_no_write_on_a_re_run(tmp_path):
    # Codex review 3, case 7: the RoleId captured in preflight was dropped with its Deployer. It is saved with the
    # install now, and every run and destroy compares it.
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    assert go(argv, deps=d)[0] == 0
    saved = state.load(DOMAIN)[0]
    assert saved["aws_ids"]["role"] == "AROANEWhub-hotmic" and saved["aws_ids"]["instance"] == "i-1"
    iam.roles["hub-hotmic"] = {"RoleId": "AROAREPLACED", "Tags": iam.roles["hub-hotmic"]["Tags"]}
    before = len(iam.called("put_role_policy"))
    code, out = go(argv, deps=d)
    assert code == 2 and "IAM role hub-hotmic" in out, out
    assert len(iam.called("put_role_policy")) == before and not iam.called("attach_role_policy")[1:]


def test_backup_storage_checks_the_server_installs_account_and_region_too():
    # Codex review 3, case 6: with no backup.json, credentials for another account and region made storage anyway.
    saved_install(DOMAIN, "hub")  # us-east-1, ACCOUNT
    iam, s3 = IAM(), S3()
    d = fakes.deps()
    d.aws_clients = lambda region, profile: (None, iam, None, "999999999999")
    d.aws_s3 = lambda region, profile: s3
    code, out = go(["backup-storage", "--domain", DOMAIN, "--asset-prefix", "hub", "--aws-region", "us-east-1"], deps=d)
    assert code == 2 and f"set up in AWS account {ACCOUNT}" in out, out
    code, out = go(["backup-storage", "--domain", DOMAIN, "--asset-prefix", "hub", "--aws-region", "us-west-2"], deps=d)
    assert code == 2 and "set up in us-east-1" in out, out
    assert not iam.calls and not s3.calls and state.load_backup(DOMAIN) == {}
    d.aws_clients = lambda region, profile: (None, iam, None, ACCOUNT)
    assert (
        go(["backup-storage", "--domain", DOMAIN, "--asset-prefix", "hub", "--aws-region", "us-east-1"], deps=d)[0] == 0
    )
    assert state.load_backup(DOMAIN)["install_id"] == INSTALL


def test_a_security_group_left_without_its_ingress_gets_it_on_the_retry():
    # Codex review 3, case 13: a run stopped between creating the group and opening its ports left a closed group.
    p = hub_plan(domain=DOMAIN, region="us-east-1")
    ec2 = fakes.FakeEC2()
    ec2.sgs = [{"GroupId": "sg-hub", "Tags": HUB_OURS}]
    dep = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids={"sg": "sg-hub"})
    assert dep.ensure_sg() == "sg-hub"
    (call,) = ec2.called("authorize_security_group_ingress")
    assert call["GroupId"] == "sg-hub" and {(r["FromPort"], r["IpProtocol"]) for r in call["IpPermissions"]} == {
        (80, "tcp"),
        (443, "tcp"),
        (443, "udp"),
    }
    ec2.calls.clear()
    assert dep.ensure_sg() == "sg-hub" and not ec2.called("authorize_security_group_ingress")  # nothing missing now
    ec2.sgs[0]["IpPermissions"] = [p for p in ec2.sgs[0]["IpPermissions"] if p["FromPort"] != 80]
    dep.ensure_sg()
    (call,) = ec2.called("authorize_security_group_ingress")
    assert [(r["FromPort"], r["IpProtocol"]) for r in call["IpPermissions"]] == [(80, "tcp")]
    # The default prefix keeps main's behaviour: an existing group is used as it is.
    old = aws.plan_aws(domain=DOMAIN, front_door="caddy", region="us-east-1", name="hotmic")
    ec2 = fakes.FakeEC2()
    ec2.sgs = [{"GroupId": "sg-tico", "Tags": TICO_TAGS}]
    aws.Deployer(old, ec2, IAM(), fakes.FakeSSM(), ACCOUNT, say=print, sleep=lambda s: None).ensure_sg()
    assert not ec2.called("authorize_security_group_ingress")


class Lost(Exception):
    """The connection dropped after AWS did the work: the call happened, its answer never came back."""


# Every write the install sends, in about the order it sends them (test_every_write_of_the_traced_install_is_a_stop).
STOPS = [
    "create_bucket",
    "put_public_access_block",
    "put_bucket_ownership_controls",
    "put_bucket_encryption",
    "put_bucket_lifecycle_configuration",
    "put_bucket_versioning",
    "put_bucket_policy",
    "create_user",
    "put_user_policy",
    "create_access_key",
    "put_object",
    "create_security_group",
    "authorize_security_group_ingress",
    "allocate_address",
    "create_role",
    "attach_role_policy",
    "put_role_policy",
    "create_instance_profile",
    "add_role_to_instance_profile",
    "put_parameter",
    "run_instances",
    "associate_address",
    "change_resource_record_sets",
    "send_command",
    "delete_object",
]


@pytest.mark.parametrize("stop", STOPS)
def test_a_hub_install_in_us_east_1_stopped_anywhere_finishes_on_a_plain_retry_and_destroys(tmp_path, stop):
    # The HotMic install as it will run: a clean account but for production, --asset-prefix hub --name hotmic in
    # us-east-1. Each write in turn is done by AWS but its answer is lost; the same command again finishes the install
    # with no manual step, and destroy --dns removes the server, its IAM, SSM and DNS, keeping the backup bucket.
    d, ec2, iam, ssm, s3, log = hub_world()
    clients, region_s3 = d.aws_clients, d.aws_s3
    fired = []

    def lossy(client):
        def wrap(name, real):
            def call(*a, **kw):
                out = real(*a, **kw)
                if name == stop and not fired:
                    fired.append(name)
                    raise Lost(name)
                return out

            return call

        return type(
            "Lossy",
            (),
            {
                "__getattr__": lambda self, n: (
                    wrap(n, getattr(client, n))
                    if n != "exceptions" and callable(getattr(client, n))
                    else getattr(client, n)
                )
            },
        )()

    d.aws_clients = lambda region, profile: (*(lossy(c) for c in clients(region, profile)[:3]), HOTMIC_ACCOUNT)
    d.aws_s3 = lambda region, profile: lossy(region_s3(region, profile))
    r53 = d.route53_client(None)
    d.route53_client = lambda profile: lossy(r53)
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic", "--backup", "aws")
    argv[argv.index("us-west-2")] = "us-east-1"
    try:
        code, out = go(argv, deps=d)
    except Lost:  # some steps let the error end the run as it is (a traceback, as on main)
        code, out = None, ""
    # The plan's delete comes last, after the server works: a lost answer there is reported, not a failed install.
    assert fired == [stop] and (code != 0 or stop == "delete_object" and "Could not delete the starter plan" in out), (
        out
    )
    code, out = go(argv, deps=d)
    if stop == "change_resource_record_sets":  # review 5, case 7: the one stop a person settles, by --confirm-dns
        assert code == 2 and f"--confirm-dns {DOMAIN}" in out, out
        code, out = go([*argv, "--confirm-dns", DOMAIN], deps=d)
    assert code == 0, out
    saved = state.load(DOMAIN)[0]
    assert saved["aws_region"] == "us-east-1" and saved["aws_account_id"] == HOTMIC_ACCOUNT and saved["dns_records"]
    assert len([i for i in ec2.instances if i.get("State", {}).get("Name") != "terminated"]) == 1
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--dns", "--yes"], deps=d)
    assert code == 0 and "  left alone: " not in out and "Left alone" not in out, out
    gone = deleted(ec2, iam, ssm)
    assert len(gone["instances"]) == 1 and gone["roles"] == ["hub-hotmic"] and gone["params"] == ["/hub/hotmic/env"]
    assert gone["eips"] and gone["sgs"] and gone["profiles"] == ["hub-hotmic"]
    assert not r53.records  # the A record setup made is gone
    assert not s3.called("delete_bucket")  # the backup copy stays


# --- Codex's fourth review -----------------------------------------------------------------------------------------


def test_lost_cut_short_or_edited_state_never_adopts_or_deletes_what_carries_the_install_tags(tmp_path, monkeypatch):
    # Review 4, findings 1 and 3: missing recorded ids passed, and discovery recorded the live resource's id; destroy
    # without saved proof deleted by the prefix and name tags. Only what the install recorded making is its own.
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    assert go(argv, deps=d)[0] == 0
    good = state.load(DOMAIN)[0]
    kinds = ("instance", "eip", "sg", "role", "profile", "param", "plan_bucket", "backup_bucket", "backup_user")
    assert set(kinds) <= set(good["aws_ids"]) and not good["aws_ids"].get("intents")

    def writes():
        return [n for n, _ in log if not n.startswith(READS)]

    destroy = ["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--dns", "--yes"]
    for kind in kinds:  # cut short: each recorded id lost in turn
        state.save(DOMAIN, {**good, "aws_ids": {k: v for k, v in good["aws_ids"].items() if k != kind}})
        (state.home() / DOMAIN / "backup.json").unlink(missing_ok=True)  # the other record of the storage
        before = len(writes())
        # A re-run leaves the backup storage as it is; backup-storage is what writes to it.
        rerun = (
            argv
            if kind not in ("backup_bucket", "backup_user")
            else ["backup-storage", "--domain", DOMAIN, "--asset-prefix", "hub", "--aws-region", "us-west-2"]
        )
        code, out = go(rerun, deps=d)
        assert code == 2 and "never recorded making it" in out and "Check it by hand" in out, (kind, out)
        if kind not in ("plan_bucket", "backup_bucket", "backup_user"):  # destroy leaves the buckets and user alone
            code, out = go(destroy, deps=d)
            assert code == 2 and "nothing was deleted" in out, (kind, out)
        assert len(writes()) == before, (kind, writes()[before:])
    # Edited: another domain's state given this install's id, without its ids.
    other = {k: v for k, v in good.items() if k not in ("aws_ids", "dns_records", "dns_zone_id")}
    state.save("hub2.hotmicapp.com", {**other, "domain": "hub2.hotmicapp.com"})
    before = len(writes())
    code, out = go(["destroy", "--domain", "hub2.hotmicapp.com", "--asset-prefix", "hub", "--yes"], deps=d)
    assert code == 2 and "nothing was deleted" in out, out
    # Lost or corrupt: no install id to prove anything with.
    (state.home() / DOMAIN / "state.json").write_text("{not json")
    # (Review 5, case 1: this computer's mode marker names the install, so both are refused before anything else.)
    code, out = go(argv, deps=d)
    assert code == 2 and "state.json is missing or unreadable" in out, out
    assert (state.home() / DOMAIN / "state.json").read_text() == "{not json"
    (state.home() / DOMAIN / "state.json").unlink()  # lost: a new install's id proves nothing of the old one's
    code, out = go(argv, deps=d)
    assert code == 2 and "state.json is missing or unreadable" in out, out
    marker = state.home() / "installs" / f"domain-{DOMAIN}.json"
    marker.rename(marker.with_suffix(".moved"))  # the marker lost too: the AWS tags still refuse
    code, out = go(argv, deps=d)
    assert code == 2 and "was not made by this setup" in out, out
    marker.with_suffix(".moved").rename(marker)
    (state.home() / DOMAIN / "state.json").write_text("{not json")
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--name", "hotmic", "--yes"], deps=d)
    assert code == 2 and "Check by hand" in out, out
    assert len(writes()) == before, writes()[before:]
    # A runner whose saved record lost its ids, and backup storage whose record lost the user's.
    rargv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://other.example"]
    rargv += ["--label", "b1", "--aws-region", "us-west-2", "--asset-prefix", "hub"]
    assert go(rargv, deps=d)[0] == 0
    (rec,) = state.load_runners()
    state.save_runner(
        runner_setup.runner_key(
            Settings(**{k: rec[k] for k in ("server_url", "aws_region")}, asset_prefix="hub"), "runner-b1"
        ),
        {**rec, "aws_ids": {}},
    )
    bs = ["backup-storage", "--domain", "laptop.example.com", "--aws-region", "us-west-2", "--asset-prefix", "hub"]
    assert go(bs, deps=d)[0] == 0
    state.save_backup("laptop.example.com", {**state.load_backup("laptop.example.com"), "aws_ids": {}})
    before = len(writes())
    for again in (rargv, bs):
        code, out = go(again, deps=d)
        assert code == 2 and "never recorded making it" in out, (again, out)
    assert len(writes()) == before, writes()[before:]


def test_an_elastic_ip_is_attached_only_after_a_fresh_read_and_never_forced():
    # Review 4, finding 2: leaving AllowReassociation out lets EC2 move an address attached elsewhere (the fake treated
    # it as False), and an address missing from the lookup was attached without proof it is still this install's.
    p = hub_plan(domain="a.example", region="us-east-1")
    ec2 = FilteredEC2()
    ec2.eips = [{"AllocationId": "eip-hub", "PublicIp": "203.0.113.9", "Tags": HUB_OURS}]
    dep = strict_dep(p, ec2, fakes.FakeIAM(), fakes.FakeSSM(), ids={"eip": "eip-hub"})
    read = ec2.describe_addresses

    def attached_meanwhile(**kw):  # attached to production right after setup's read
        out = json.loads(json.dumps(read(**kw)))  # what setup read, apart from what happens next
        ec2.eips[0]["InstanceId"] = "i-production"
        return out

    ec2.describe_addresses = attached_meanwhile
    with pytest.raises(RuntimeError, match="AlreadyAssociated"):
        dep.associate("eip-hub", "i-hub")
    assert ec2.eips[0]["InstanceId"] == "i-production"
    del ec2.describe_addresses
    # No longer this install's by a fresh read: released, retagged or not the allocation it recorded.
    for eips, ids in (
        ([], {"eip": "eip-hub"}),
        ([{"AllocationId": "eip-hub", "PublicIp": "203.0.113.9", "Tags": HUB_TAGS}], {"eip": "eip-hub"}),
        ([{"AllocationId": "eip-hub", "PublicIp": "203.0.113.9", "Tags": HUB_OURS}], {"eip": "eip-other"}),
    ):
        ec2.eips, ec2.calls = eips, []
        with pytest.raises(MissingInput, match="Elastic IP eip-hub"):
            strict_dep(p, ec2, fakes.FakeIAM(), fakes.FakeSSM(), ids=ids).associate("eip-hub", "i-hub")
        assert not ec2.called("associate_address")


def test_backup_storage_holds_to_the_server_installs_record_of_its_user(tmp_path):
    # Review 4, finding 4: backup-storage checked only backup.json. Without it, a user deleted and made again with the
    # same tags got the policy and a key; and the two records may disagree.
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    made = state.load(DOMAIN)[0]["aws_ids"]["backup_user"]
    user = iam.users["hub-backup-hotmic"]
    iam.users["hub-backup-hotmic"] = {**user, "UserId": "AIDAREMADE"}  # deleted and made again, same tags
    bs = ["backup-storage", "--domain", DOMAIN, "--asset-prefix", "hub", "--aws-region", "us-west-2"]
    keys = len(iam.called("create_access_key"))
    code, out = go(bs, deps=d)
    assert code == 2 and "IAM user hub-backup-hotmic" in out, out
    assert len(iam.called("create_access_key")) == keys and len(iam.called("put_user_policy")) == 1
    iam.users["hub-backup-hotmic"] = user
    state.save_backup(DOMAIN, {**state.load_backup(DOMAIN), "aws_ids": {"backup_user": "AIDAOTHER"}})
    before = len(log)
    code, out = go(bs, deps=d)
    assert code == 2 and f"backup.json has backup_user AIDAOTHER, the server install's state.json {made}" in out, out
    assert len(log) == before
    # Agreeing records: storage made here is recorded in both.
    state.save_backup(DOMAIN, {**state.load_backup(DOMAIN), "aws_ids": {}})
    assert go(bs, deps=d)[0] == 0
    assert (
        state.load_backup(DOMAIN)["aws_ids"]["backup_user"] == made == state.load(DOMAIN)[0]["aws_ids"]["backup_user"]
    )


def test_a_record_set_matches_this_installs_only_when_every_routing_field_is_equal():
    # Codex review 4: name, type, TTL and values alone are not the record set. One someone has since made weighted,
    # latency, failover, geo, multivalue or health-checked (with the same values) is theirs: never kept, never deleted.
    made = {"Name": f"{DOMAIN}.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "203.0.113.9"}]}
    want = [dns.Record("A", DOMAIN, "203.0.113.9", 300)]
    for extra in (
        {"SetIdentifier": "blue", "Weight": 10},
        {"SetIdentifier": "east", "Region": "us-east-1"},
        {"SetIdentifier": "p", "Failover": "PRIMARY"},
        {"SetIdentifier": "g", "GeoLocation": {"CountryCode": "US"}},
        {"SetIdentifier": "m", "MultiValueAnswer": True},
        {"HealthCheckId": "hc-1"},
    ):
        theirs = {**made, **extra}
        assert route53.key(theirs) != route53.key(made), extra
        r53 = FakeRoute53(ZONE, [dict(theirs)])
        changes, created, found, kept = route53.plan(r53, "Z1", want, [made])
        assert found == [theirs] and kept == [] and not [c for c in changes if c["Action"] == "DELETE"], extra
        assert route53.recorded(r53, "Z1", DOMAIN, [made]) == ([], [theirs]), extra
    r53 = FakeRoute53(ZONE, [dict(made)])
    assert route53.recorded(r53, "Z1", DOMAIN, [made]) == ([made], [])


class Refused(Exception):
    """As botocore's ClientError: Route 53 answered, and refused the whole batch."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


def test_a_refused_route53_batch_leaves_no_pending_record_and_a_lost_answer_is_adopted_only_exactly(tmp_path):
    # Codex review 4: a CREATE Route 53 refused made nothing, so its journalled record set must not stay "pending": a
    # record set someone else makes later with the same values would be taken for this install's. A lost answer (no
    # reply, or a 5xx) may have made it, so it stays pending; the re-run adopts it only when it is exactly what was
    # journalled. There is no change id to ask GetChange about: it came back in the answer that was lost.
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    real = r53.change_resource_record_sets
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    for err in (Refused("InvalidChangeBatch"), Refused("Throttling")):

        def refuse(**kw):
            r53.calls.append(("change_resource_record_sets", kw))
            raise err

        r53.change_resource_record_sets = refuse
        try:
            code = go(argv, deps=d)[0]
        except Refused:
            code = None
        assert code != 0 and r53.records == []
        assert state.load(DOMAIN)[0].get("dns_pending", []) == [], err
    for err in (Lost("change_resource_record_sets"), Refused("InternalError", 500)):

        def lose(**kw):
            real(**kw)
            raise err

        r53.records, r53.change_resource_record_sets = [], lose
        try:
            code = go(argv, deps=d)[0]
        except (Lost, Refused):
            code = None
        assert code != 0 and len(r53.records) == 1
        (pending,) = state.load(DOMAIN)[0]["dns_pending"]
        assert route53.key(pending) == route53.key(r53.records[0]), err
        r53.change_resource_record_sets = real
        code, out = go(argv, deps=d)
        assert code == 2 and f"--confirm-dns {DOMAIN}" in out, out  # review 5, case 7
        code, out = go([*argv, "--confirm-dns", DOMAIN], deps=d)
        assert code == 0, out
        saved = state.load(DOMAIN)[0]
        assert [route53.key(rs) for rs in saved["dns_records"]] == [route53.key(pending)]
        assert saved.get("dns_pending", []) == []
        s = state.load(DOMAIN)[0]
        state.save(DOMAIN, {**s, "dns_records": []})


def test_an_instance_profile_role_or_backup_user_is_read_again_right_before_each_iam_write():
    # Codex review 4: the profile and role were proven, then written later without a fresh read. Each is read again
    # (tags and recorded id) right before add_role_to_instance_profile, remove_role_from_instance_profile,
    # delete_instance_profile, delete_access_key and create_access_key; a replacement in between is never written.
    p = hub_plan(domain=DOMAIN)

    def deploy(iam, ids, hook, swap):
        real = getattr(iam, hook)

        def then(**kw):
            out = real(**kw)
            swap(iam)
            return out

        setattr(iam, hook, then)
        with pytest.raises((MissingInput, SystemExit), match="hub-hotmic"):
            strict_dep(p, fakes.FakeEC2(), iam, fakes.FakeSSM(), ids=ids).ensure_role()
        assert not iam.called("add_role_to_instance_profile")

    def other_profile(iam):
        iam.profiles["hub-hotmic"]["InstanceProfileId"] = "AIPAOTHER"

    def other_role(iam):
        iam.roles["hub-hotmic"] = {"RoleId": "AROAOTHER", "Tags": HUB_OURS}

    deploy(IAM(), {}, "create_instance_profile", other_profile)  # made, then deleted and made again
    deploy(IAM(), {}, "put_role_policy", other_role)
    iam = IAM()  # the re-run branch: an earlier run made the profile but stopped before adding the role
    iam.roles["hub-hotmic"] = {"RoleId": "AROAFIRST", "Tags": HUB_OURS}
    iam.profiles["hub-hotmic"] = {"Tags": HUB_OURS, "Roles": [], "InstanceProfileId": "AIPAFIRST"}
    deploy(iam, {"role": "AROAFIRST", "profile": "AIPAFIRST"}, "put_role_policy", other_role)

    d, ec2, iam, ssm = id_install()
    real = iam.remove_role_from_instance_profile

    def removed(**kw):
        real(**kw)
        iam.profiles["hub-hotmic"]["InstanceProfileId"] = "AIPAPRODUCTION"  # replaced right after

    iam.remove_role_from_instance_profile = removed
    out = []
    owned.destroy(
        ec2, iam, ssm, {"iam-role": ["hub-hotmic"]}, out.append, None, names=HUB_ID, name="hotmic", ids=HUB_IDS
    )
    assert iam.called("remove_role_from_instance_profile") and not iam.called("delete_instance_profile")
    assert "left alone: IAM instance profile hub-hotmic" in "\n".join(out)

    for keys in ([], [{"AccessKeyId": "AKIA1", "CreateDate": 1}, {"AccessKeyId": "AKIA2", "CreateDate": 2}]):
        s3, iam = S3(), IAM()

        def listed(UserName, keys=keys, iam=iam):
            iam.users[UserName] = {"UserId": "AIDAOTHER", "Tags": iam.users[UserName]["Tags"]}  # replaced meanwhile
            return {"AccessKeyMetadata": keys}

        iam.list_access_keys = listed
        with pytest.raises(MissingInput, match="IAM user hub-backup-acme"):
            owned.create_s3(
                s3, iam, domain=DOMAIN, region="us-west-2", account_id=ACCOUNT, name="acme", names=HUB, ids={}
            )
        assert not iam.called("delete_access_key") and not iam.called("create_access_key")


def test_the_plan_bucket_and_object_are_proved_before_each_write_and_a_lost_put_is_known_by_its_hash():
    # Codex review 4: the plan bucket was proved once, then configured, written to and deleted from without a fresh
    # read; a lost put_object answer left the re-run refusing its own object; delete_plan deleted whatever was at the
    # key. Now: the bucket (tags and recorded name, or the explicit install tag) is read before each write; the put is
    # journalled (key and sha256) first and carries the hash in its metadata, so a 412 on the re-run is accepted only for
    # this install's own bytes; and the delete happens only while the object's ETag is the one recorded.
    install = "0123456789abcdef"
    names = aws.Names("hub", install)
    p = aws.plan_aws(
        domain="a.example",
        front_door="caddy",
        region="us-east-1",
        name="acme",
        prefix="hub",
        install_id=install,
        plan_bucket="hub-plans-x",
    )
    ours = names.tags("acme")
    for write in ("put_public_access_block", "put_bucket_ownership_controls", "put_bucket_encryption"):
        s3 = S3()
        real_hook = getattr(s3, write)

        def retag(*, s3=s3, real_hook=real_hook, **kw):
            real_hook(**kw)
            s3.tagsets["hub-plans-x"] = PRODUCTION  # deleted and made again by someone else, in this account

        setattr(s3, write, retag)
        d = strict_dep(p, None, None, None, ids={})
        with pytest.raises(MissingInput, match="hub-plans-x"):
            d.ensure_plan_bucket(s3, "us-east-1")
        assert not s3.called("put_bucket_lifecycle_configuration"), write
    s3, saved = S3(), []
    d = strict_dep(p, None, None, None, ids={}, save=lambda: saved.append(json.loads(json.dumps(d.ids))))
    d.ensure_plan_bucket(s3, "us-east-1")
    key = "plans/acme/" + "c" * 64
    real_put = s3.put_object

    def lost(**kw):
        real_put(**kw)
        raise ConnectionError("read timeout")

    s3.put_object = lost
    with pytest.raises(ConnectionError):
        d.put_plan(s3, key, "ours")
    assert saved[-1]["intents"]["plan_object"]["key"] == key  # journalled before the put
    s3.put_object = real_put
    again = strict_dep(p, None, None, None, ids=json.loads(json.dumps(saved[-1])))
    again.ensure_plan_bucket(s3, "us-east-1")
    again.put_plan(s3, key, "ours")  # 412: its own bytes, known by the hash in the object's metadata
    assert {k: again.ids["plan_object"][k] for k in ("key", "etag")} == {"key": key, "etag": etag(b"ours")}
    s3.objects[("hub-plans-x", key)]["ETag"] = '"replaced"'  # written over since (by someone else)
    assert again.delete_plan(s3, key) and ("hub-plans-x", key) in s3.objects
    s3.objects[("hub-plans-x", key)]["ETag"] = etag(b"ours")
    s3.tagsets["hub-plans-x"] = PRODUCTION
    assert again.delete_plan(s3, key) and ("hub-plans-x", key) in s3.objects
    with pytest.raises(MissingInput, match="hub-plans-x"):
        again.put_plan(s3, key, "ours")
    s3.tagsets["hub-plans-x"] = ours
    assert again.delete_plan(s3, key) == "" and ("hub-plans-x", key) not in s3.objects
    other = "plans/acme/" + "d" * 64  # someone else's bytes at a key this install journalled
    third = strict_dep(
        p, None, None, None, ids={**again.ids, "intents": {"plan_object": {"key": other, "sha256": "e" * 64}}}
    )
    s3.objects[("hub-plans-x", other)] = {"Body": b"x", "Metadata": {"sha256": "f" * 64}, "ETag": '"x"'}
    with pytest.raises(MissingInput, match="did not record writing it"):
        third.put_plan(s3, other, "ours")


def test_iam_reads_right_after_a_create_wait_out_iam_lag_and_a_lost_launch_answer_makes_no_second_server():
    # Codex review 4: IAM is eventually consistent, so the read that proves a role, profile or user just made can answer
    # NoSuchEntity for a few seconds; it is retried with backoff, a bounded number of times. And a RunInstances whose
    # answer was lost, with DescribeInstances lagging behind on the re-run, sends the journalled ClientToken again: EC2
    # answers with the instance it launched, and no second one is made.
    p, slept = hub_plan(domain=DOMAIN), []

    class Lagging(IAM):
        def __init__(self, lag):
            super().__init__()
            self.lag = dict(lag)

        def _lagging(self, kind):
            if self.lag.get(kind):
                self.lag[kind] -= 1
                raise self.exceptions.NoSuchEntityException()

        def get_role(self, RoleName):
            if any(n == "create_role" for n, _ in self.calls):
                self._lagging("role")
            return super().get_role(RoleName)

        def get_instance_profile(self, InstanceProfileName):
            if InstanceProfileName in self.profiles:  # made
                self._lagging("profile")
            return super().get_instance_profile(InstanceProfileName)

        def get_user(self, UserName):
            if UserName in self.users:
                self._lagging("user")
            return super().get_user(UserName)

    iam = Lagging({"role": 2, "profile": 2})
    dep = owned.StrictDeployer(p, fakes.FakeEC2(), iam, fakes.FakeSSM(), ACCOUNT, say=print, sleep=slept.append)
    dep.ensure_role()
    assert iam.called("add_role_to_instance_profile") and slept == [1, 2, 1, 2]
    iam = Lagging({"user": 3})
    owned.create_s3(
        S3(), iam, domain=DOMAIN, region="us-west-2", account_id=ACCOUNT, name="acme", names=HUB, sleep=slept.append
    )
    assert iam.called("create_access_key")
    iam, slept[:] = Lagging({"role": 99}), []
    dep = owned.StrictDeployer(p, fakes.FakeEC2(), iam, fakes.FakeSSM(), ACCOUNT, say=print, sleep=slept.append)
    with pytest.raises(fakes._Exc.NoSuchEntityException):
        dep.ensure_role()
    assert slept == [1, 2, 4, 8, 16] and not iam.called("attach_role_policy")

    ec2, saved = fakes.FakeEC2(), []
    real = ec2.run_instances

    def lost(**kw):
        real(**kw)
        raise ConnectionError("read timeout")

    ec2.run_instances = lost
    first = strict_dep(
        p, ec2, IAM(), fakes.FakeSSM(), ids={}, save=lambda: saved.append(json.loads(json.dumps(first.ids)))
    )
    with pytest.raises(ConnectionError):
        first.launch("#!/bin/bash\n", "hub-hotmic", "sg-1")
    ec2.run_instances = real
    ec2.describe_instances = lambda **kw: {"Reservations": []}  # lagging: the instance is not listed yet
    again = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=saved[-1])
    assert again.launch_or_reuse({"sg": "sg-1", "alloc": ""}, "#!/bin/bash\n") == ("i-1", True)
    tokens = [kw["ClientToken"] for kw in ec2.called("run_instances")]
    assert len(ec2.instances) == 1 and len(tokens) == 2 and tokens[0] == tokens[1]
    assert again.ids["instance"] == "i-1"


def test_destroy_dns_deletes_the_recorded_records_after_the_server_is_already_gone(tmp_path):
    # Codex review 4: destroy returned early when no AWS resource was left, so `destroy --dns` after a plain destroy
    # (or after the server was removed by hand) never deleted the DNS records this install recorded making.
    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    assert len(r53.records) == 1 and state.load(DOMAIN)[0]["dns_records"]
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--yes"], deps=d)
    assert code == 0 and len(r53.records) == 1, out
    ec2.instances, ec2.sgs, ec2.eips, ssm.params = [], [], [], {}  # all of it gone now
    iam.roles, iam.profiles = {}, {}
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--dns", "--yes"], deps=d)
    assert code == 0 and "DNS records deleted." in out and r53.records == [], out


# --- Codex's fifth review ------------------------------------------------------------------------------------------


def test_a_strict_install_stays_strict_when_its_state_is_lost_or_corrupt_or_the_flag_is_left_out(tmp_path):
    # Codex review 5, case 1: with state.json corrupt, a retry without --asset-prefix took the default path and overwrote
    # and re-tagged a foreign /tico/hotmic/env. A mode marker, saved before the first AWS write and keyed by domain and
    # by name, makes every later command for either refuse instead of falling back to the default path.
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    kept = state.home() / DOMAIN / "state.json"
    good = kept.read_text()
    writes = lambda: [n for n, _ in log if not n.startswith(("describe_", "get_", "head_", "list_"))]  # noqa: E731
    for broken in ("{not json", None):
        if broken is None:
            kept.unlink()
        else:
            kept.write_text(broken)
        before = len(writes())
        for argv in (
            hub_args(tmp_path, "--name", "hotmic"),  # the flag left out
            hub_args(tmp_path),
            hub_args(tmp_path, "--asset-prefix", "tico", "--name", "hotmic"),
            ["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--yes"],
            ["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--name", "hotmic", "--yes"],
            ["backup-storage", "--domain", DOMAIN, "--asset-prefix", "hub", "--aws-region", "us-west-2"],
        ):
            code, out = go(argv, deps=d)
            assert code != 0 and "made with --asset-prefix hub" in out, (argv, out)
        code, out = go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)
        assert code != 0 and "refusing" in out, out  # never a second install over the lost one
        assert (
            len(writes()) == before
            and not ssm.called("put_parameter")
            or all(kw["Name"].startswith("/hub/") for kw in ssm.called("put_parameter"))
        )
        kept.write_text(good)
    other = hub_args(tmp_path, "--name", "hotmic")  # another domain, the same name, no flag
    other[other.index(DOMAIN)] = "other.hotmicapp.com"
    code, out = go(other, deps=d)
    assert code != 0 and "made with --asset-prefix hub" in out, out
    assert all(kw["Name"].startswith("/hub/") for kw in ssm.called("put_parameter"))
    code, out = go(hub_args(tmp_path, "--name", "hotmic"), deps=d)  # its state intact: the flag must be named again
    assert code == 2 and "Run the command again with --asset-prefix hub" in out, out
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0


def test_state_is_bound_to_its_install_and_a_remote_shell_proves_its_server_first(tmp_path):
    # Codex review 5, case 2: a whole state.json copied beneath another domain repointed the original server, and
    # owner-link trusted an edited instance id. State is bound to its domain and install; before any remote shell, the
    # instance is read and must carry the install's id tag, be the recorded instance, in the recorded account.
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    good = state.load(DOMAIN)[0]
    copy = "copy.hotmicapp.com"
    state.save(copy, dict(good))
    writes = lambda: [n for n, _ in log if not n.startswith(READS)]  # noqa: E731
    before = len(writes())
    again = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    again[again.index(DOMAIN)] = copy
    destroy = ["destroy", "--domain", copy, "--asset-prefix", "hub", "--yes"]
    for argv in (again, destroy, ["doctor", "--domain", copy, "--non-interactive"]):
        code, out = go(argv, deps=d)
        assert code != 0 and f"saved for {DOMAIN}" in out, (argv, out)
    assert len(writes()) == before
    ec2.instances.append({"InstanceId": "i-prod", "Tags": PRODUCTION, "State": {"Name": "running"}})
    for edit in ({"aws_instance_id": "i-prod"}, {"aws_account_id": "999999999999"}):
        state.save(DOMAIN, {**good, "auth": "owner-link", **edit})
        for argv in (
            ["owner-link", "--domain", DOMAIN, "--asset-prefix", "hub"],
            ["doctor", "--domain", DOMAIN, "--non-interactive"],
        ):
            code, out = go(argv, deps=d)
            assert code != 0, out
            assert "refusing" in out, (edit, argv, out)
        assert len(writes()) == before, writes()[before:]


def test_a_route53_create_that_may_have_landed_stays_pending_and_is_never_adopted_by_equality(tmp_path, monkeypatch):
    # Codex review 5, cases 3 and 7: the CREATE landed, its answer timed out, botocore retried and got 400 "already
    # exists", and setup took that as a refusal and dropped the pending proof; and a pending entry was resolved by an
    # identical record existing, which cannot tell who made it. Strict mode's Route 53 client makes one attempt per call,
    # a 4xx after any retry is ambiguous, and a pending record set found in the zone is adopted only with --confirm-dns.
    import sys
    import types

    built = []

    class Session:
        def __init__(self, profile_name=None):
            pass

        def client(self, name, config=None):
            built.append(config)
            return types.SimpleNamespace(list_hosted_zones=lambda **kw: {})

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(Session=Session))
    wizard._r53_client("", single_attempt=True)
    assert built[-1].retries["max_attempts"] == 1
    wizard._r53_client("")
    assert built[-1] is None  # the default path's client is main's

    d, ec2, iam, ssm, s3, log = hub_world()
    r53 = d.route53_client("")._inner
    real = r53.change_resource_record_sets
    retried = Refused("InvalidChangeBatch")
    retried.response["ResponseMetadata"]["RetryAttempts"] = 1

    def landed_then_retried(**kw):
        real(**kw)
        raise retried

    r53.change_resource_record_sets = landed_then_retried
    argv = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    try:
        code = go(argv, deps=d)[0]
    except Refused:
        code = None
    assert code != 0 and len(r53.records) == 1
    (pending,) = state.load(DOMAIN)[0]["dns_pending"]
    r53.change_resource_record_sets = real
    code, out = go(argv, deps=d)
    assert code == 2 and f"A {DOMAIN} -> 203.0.113.9" in out and f"--confirm-dns {DOMAIN}" in out, out
    assert not r53.called("change_resource_record_sets")[1:] and state.load(DOMAIN)[0]["dns_pending"] == [pending]
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--dns", "--yes", "--dry-run"], deps=d)
    assert "not confirmed" in out, out  # destroy never deletes a pending record set either
    code, out = go([*argv, "--confirm-dns", DOMAIN], deps=d)
    assert code == 0, out
    saved = state.load(DOMAIN)[0]
    assert [route53.key(r) for r in saved["dns_records"]] == [route53.key(pending)]
    assert saved.get("dns_pending", []) == []
    # Pending and gone (deleted by hand): the entry is dropped and the record made again.
    state.save(DOMAIN, {**saved, "dns_records": [], "dns_pending": [pending]})
    r53.records = []
    code, out = go(argv, deps=d)
    assert code == 0 and len(r53.records) == 1, out
    assert state.load(DOMAIN)[0].get("dns_pending", []) == []


BACKUP_WRITES = (
    "put_public_access_block",
    "put_bucket_versioning",
    "put_bucket_encryption",
    "put_bucket_lifecycle_configuration",
    "put_bucket_policy",
)


@pytest.mark.parametrize("write", BACKUP_WRITES[:-1])
def test_the_backup_bucket_is_proved_again_right_before_each_of_its_writes(write):
    # Codex review 5, case 6: the backup bucket was proved once, then all five settings were written. A bucket replaced
    # (deleted and made again by someone else, in this account) after any one of them gets none of the later ones.
    s3, iam, ids = S3(), IAM(), {}
    real = getattr(s3, write)

    def replaced(**kw):
        real(**kw)
        s3.tagsets[kw["Bucket"]] = PRODUCTION

    setattr(s3, write, replaced)
    with pytest.raises(MissingInput, match="S3 bucket hub-backup-"):
        owned.create_s3(
            s3, iam, domain=DOMAIN, region="us-west-2", account_id=ACCOUNT, name="hotmic", names=HUB_ID, ids=ids
        )
    later = BACKUP_WRITES[BACKUP_WRITES.index(write) + 1 :]
    assert not any(s3.called(w) for w in later), [w for w in later if s3.called(w)]
    assert not iam.called("create_user") and not iam.called("create_access_key")
    # Each tag read before a write names this account as the bucket's owner.
    reads = s3.called("get_bucket_tagging")
    assert reads and all(r["ExpectedBucketOwner"] == ACCOUNT for r in reads)
    # A bucket with this install's tags that it never recorded making gets no write either.
    s3, iam = S3(), IAM()
    s3.tagsets[HUB_ID.backup_bucket(DOMAIN, ACCOUNT)] = HUB_ID.tags("hotmic")
    with pytest.raises(MissingInput, match="never recorded making it"):
        owned.create_s3(
            s3, iam, domain=DOMAIN, region="us-west-2", account_id=ACCOUNT, name="hotmic", names=HUB_ID, ids={}
        )
    assert not any(s3.called(w) for w in BACKUP_WRITES)


def test_a_create_intent_without_a_token_matches_only_its_own_name_and_type():
    # Codex review 5, case 8: an intent journalled before a create (its answer lost) proved any resource with the
    # install's tags, whatever its name: ingress was authorised on a tagged foreign security group of another name.
    p = hub_plan(domain=DOMAIN, region="us-east-1")
    ec2 = fakes.FakeEC2()
    ec2.sgs = [{"GroupId": "sg-prod", "GroupName": "production-web", "Tags": HUB_OURS}]
    ids = {"intents": {"sg": {"name": "hub-hotmic"}}}
    with pytest.raises(MissingInput, match="sg-prod"):
        strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids).ensure_sg()
    assert not ec2.called("authorize_security_group_ingress") and "sg" not in ids
    # Its own name and type: the create that landed.
    ec2.sgs[0]["GroupName"] = "hub-hotmic"
    assert strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids).ensure_sg() == "sg-prod" and ids["sg"] == "sg-prod"
    # Another kind's intent of the same name proves nothing, and an intent never matches a resource with no name.
    assert not owned.recorded({"intents": {"eip": {"name": "hub-hotmic"}}}, "sg", "sg-prod", name="hub-hotmic")
    assert not owned.recorded({"intents": {"sg": {"name": "hub-hotmic"}}}, "sg", "sg-prod")
    eip = {"AllocationId": "eip-prod", "PublicIp": "198.51.100.1", "Tags": HUB_ID.tags("hotmic", "production-ip")}
    ec2.eips = [eip]
    with pytest.raises(MissingInput, match="eip-prod"):
        strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids={"intents": {"eip": {"name": "hub-hotmic"}}}).ensure_eip()
    # Destroy holds to it too: a tagged group of another name is not the one the intent made.
    ec2.sgs[0]["GroupName"] = "production-web"
    with pytest.raises(MissingInput, match="security group sg-prod"):
        owned.refuse_unrecorded(
            ec2, IAM(), {"security-group": ["sg-prod"]}, HUB_ID, "hotmic", {"intents": {"sg": {"name": "hub-hotmic"}}}
        )


def test_the_volume_and_network_interface_a_launch_made_are_journalled_and_nothing_else_goes_with_the_server():
    # Codex review 5, case 8: attached volumes and network interfaces were proved by tags alone. The launch's own root
    # volume and primary interface are journalled; one attached since with DeleteOnTermination, even with the install's
    # tags, stops the terminate.
    ids = {}
    d = strict_dep(hub_plan(), fakes.FakeEC2(), IAM(), fakes.FakeSSM(), ids=ids)
    d.put_env("TICO_DOMAIN=x\n")
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\n")
    assert ids["volumes"] == ["vol-1"] and ids["enis"] == ["eni-1"]
    ec2 = d.ec2
    assert owned.foreign_attached(ec2, ["i-1"], "hotmic", HUB_ID, ids) == []
    attach = [{"InstanceId": "i-1", "DeleteOnTermination": True}]
    ec2.volumes.append({"VolumeId": "vol-tagged", "Tags": HUB_OURS, "Attachments": attach})
    ec2.enis.append({"NetworkInterfaceId": "eni-tagged", "TagSet": HUB_OURS, "Attachment": attach[0]})
    with pytest.raises(MissingInput, match="vol-tagged, eni-tagged"):
        owned.refuse_foreign_attached(ec2, ["i-1"], "hotmic", HUB_ID, ids)
    # An instance found by its journalled ClientToken (its launch answer lost) gets its own journalled on the retry.
    again = {k: v for k, v in ids.items() if k not in ("instance", "volumes", "enis")}
    again["intents"] = {"instance": {"name": "hub-hotmic", "token": ec2.instances[0]["ClientToken"]}}
    d2 = strict_dep(hub_plan(), ec2, IAM(), fakes.FakeSSM(), ids=again)
    d2.launch_or_reuse(d2.prepare(), "#!/bin/bash\n")
    assert again["instance"] == "i-1" and again["volumes"] == ["vol-1"] and again["enis"] == ["eni-1"]


class KeyedIAM(IAM):
    """A user's access keys as IAM keeps them: at most two, listed with their creation dates."""

    def __init__(self, keys=()):
        super().__init__()
        self.keys = list(keys)

    def list_access_keys(self, UserName):
        return {"AccessKeyMetadata": list(self.keys)}

    def delete_access_key(self, **kw):
        self._rec("delete_access_key", **kw)
        self.keys = [k for k in self.keys if k["AccessKeyId"] != kw["AccessKeyId"]]

    def create_access_key(self, UserName):
        self._rec("create_access_key", UserName=UserName)
        kid = f"AKIANEW{len(self.calls)}"
        self.keys.append({"AccessKeyId": kid, "CreateDate": 99})
        return {"AccessKey": {"AccessKeyId": kid, "SecretAccessKey": "s3cr3t"}}


def test_only_an_access_key_this_install_recorded_making_is_ever_deleted():
    # Codex review 5, case 8: with two keys, the older was deleted whoever made it. Keys setup makes are journalled as
    # made; only one of those is deleted, and two keys it never recorded are refused for a by-hand check.
    user = "hub-backup-hotmic"
    base = {"backup_user": f"AIDA{user}"}
    for keys, recorded_keys, gone in (
        ([("AKIAFOREIGN", 1), ("AKIAOURS", 2)], ["AKIAOURS"], "AKIAOURS"),
        ([("AKIAOLD", 1), ("AKIANEWER", 2)], ["AKIAOLD", "AKIANEWER"], "AKIAOLD"),
        ([("AKIAA", 1), ("AKIAB", 2)], [], None),
    ):
        iam, s3 = KeyedIAM({"AccessKeyId": k, "CreateDate": t} for k, t in keys), S3()
        iam.users[user] = {"UserId": f"AIDA{user}", "Tags": HUB_OURS}
        ids = {**base, "backup_keys": list(recorded_keys)}
        bucket = HUB_ID.backup_bucket(DOMAIN, ACCOUNT)
        s3.tagsets[bucket], ids["backup_bucket"] = HUB_OURS, bucket
        run = lambda: owned.create_s3(  # noqa: E731
            s3, iam, domain=DOMAIN, region="us-west-2", account_id=ACCOUNT, name="hotmic", names=HUB_ID, ids=ids
        )
        if gone is None:
            with pytest.raises(MissingInput, match="AKIAA, AKIAB"):
                run()
            assert not iam.called("delete_access_key") and not iam.called("create_access_key")
            continue
        values = run()
        assert [c["AccessKeyId"] for c in iam.called("delete_access_key")] == [gone]
        new = values["LITESTREAM_ACCESS_KEY_ID"]
        assert ids["backup_keys"] == [k for k in recorded_keys if k != gone] + [new]


def test_the_plan_object_is_deleted_only_by_its_recorded_version_or_a_delete_conditional_on_its_etag():
    # Codex review 5, case 9: the plan object was read (HEAD), then deleted unconditionally, so one replaced in between
    # was deleted; and the fake's new ETag per put hid that the same bytes written again keep their ETag. In a bucket
    # with versioning on, exactly the recorded version is deleted; with it off, the delete carries IfMatch.
    p = hub_plan(domain=DOMAIN, plan_bucket="hub-plans-x")
    key = "plans/hotmic/" + "c" * 64
    for versioned, body in ((False, b"someone else's"), (True, b"ours")):
        s3 = S3()
        d = strict_dep(p, None, None, None, ids={})
        d.ensure_plan_bucket(s3, "us-west-2")
        if versioned:
            s3.put_bucket_versioning(Bucket="hub-plans-x", VersioningConfiguration={"Status": "Enabled"})
        d.put_plan(s3, key, "ours")
        assert d.ids["plan_object"]["etag"] == etag(b"ours")
        assert ("version_id" in d.ids["plan_object"]) == versioned
        real = s3.delete_object

        def replaced_first(s3=s3, real=real, body=body, **kw):
            # Written over in the instant before the delete arrives (versioned: the same bytes, so the same ETag).
            s3.delete_object = real
            s3.put_object(Bucket="hub-plans-x", Key=key, Body=body, Metadata={"owner": "production"})
            real(**kw)

        s3.delete_object = replaced_first
        result = d.delete_plan(s3, key)
        now = s3.objects.get(("hub-plans-x", key))
        assert now is not None and now["Metadata"] == {"owner": "production"}, (versioned, result)
        if versioned:
            assert result == "" and s3.called("delete_object")[-1]["VersionId"] == d.ids["plan_object"]["version_id"]
            assert not s3.versions.get(("hub-plans-x", key))  # this install's own version is gone
        else:
            assert result == "ChangedSinceWritten" and s3.called("delete_object")[-1]["IfMatch"] == etag(b"ours")


def test_an_elastic_ip_is_released_only_when_a_fresh_read_shows_it_associated_with_nothing():
    # Codex review 5, case 4: an address associated with a network interface (no InstanceId) passed the check, and
    # ReleaseAddress in a default VPC disassociates it on its own. Right before the release, a read by its allocation id
    # must show no association at all; otherwise it is left, with the step to take by hand.
    eni = {"AssociationId": "eipassoc-prod", "NetworkInterfaceId": "eni-prod", "PrivateIpAddress": "10.0.0.5"}
    for association, released in (
        (eni, False),
        ({"AssociationId": "eipassoc-x", "InstanceId": "i-hub"}, False),
        ({}, True),
    ):
        d, ec2, iam, ssm = id_install()
        ec2.eips[1].update(association)
        if association.get("InstanceId"):  # still shown on the terminated server: EC2 has not caught up
            ec2.terminate_instances = lambda **kw: ec2._rec("terminate_instances", **kw)
        out, slept = [], []
        owned.destroy(
            ec2,
            iam,
            ssm,
            {"instance": ["i-hub"], "elastic-ip": ["eip-hub"]},
            out.append,
            slept.append,
            names=HUB_ID,
            name="hotmic",
            ids=HUB_IDS,
        )
        assert (deleted(ec2, iam, ssm)["eips"] == ["eip-hub"]) == released, (association, out)
        said = "\n".join(out)
        if not released:
            assert "left alone: Elastic IP eip-hub" in said and "release it by hand" in said, said
            assert sum(slept) <= 60
        # A read by its allocation id, after the terminate, decided.
        assert ec2.called("terminate_instances") and any("AllocationIds" in f for f in ec2.address_reads), association
    # As EC2 does: terminating the server it is attached to frees the address, and the release goes ahead.
    d, ec2, iam, ssm = id_install()
    ec2.eips[1].update({"AssociationId": "eipassoc-x", "InstanceId": "i-hub", "NetworkInterfaceId": "eni-hub"})
    owned.destroy(
        ec2,
        iam,
        ssm,
        {"instance": ["i-hub"], "elastic-ip": ["eip-hub"]},
        print,
        lambda s: None,
        names=HUB_ID,
        name="hotmic",
        ids=HUB_IDS,
    )
    assert deleted(ec2, iam, ssm)["eips"] == ["eip-hub"]


def _malformed(path, text="[1, 2]"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize("command", ["backup-storage", "runner", "prompted domain"])
def test_default_commands_read_no_new_local_file_that_can_stop_them(command):
    # Codex review 5, part 2, low 2: backup-storage, runner and a prompted domain read local records main never read; a
    # record of the wrong shape (valid JSON, not an object) stopped them. Without --asset-prefix they run as on main.
    home = state.home()
    _malformed(home / "tico.example.com" / "backup.json")
    _malformed(home / "tico.example.com" / "state.json")
    _malformed(home / "runners" / "runner-build-1.json")
    if command == "backup-storage":
        s3, iam = S3(), IAM()
        d = fakes.deps()
        d.aws_clients = lambda region, profile: (None, iam, None, ACCOUNT)
        d.aws_s3 = lambda region, profile: s3
        code, out = go(["backup-storage", "--domain", "tico.example.com", "--aws-region", "us-east-1"], deps=d)
        assert code == 0 and s3.called("create_bucket")[0]["Bucket"] == "tico-backup-tico-example-com-123456789012", out
    elif command == "runner":
        runner = ["runner", "--dry-run", "--non-interactive", "--cloud", "aws", "--label", "build-1"]
        code, out = go([*runner, "--server-url", "https://tico.example.com"])
        assert code == 0 and "Security group tico-runner-build-1:" in out, out
    else:
        io = StopAtPlan(interactive=True, answers=["tico.example.com"])  # the domain, asked
        base = [a for a in BASE if a not in ("--non-interactive", "--domain", "tico.example.com")]
        code, out = go([*base, "--target", "aws", "--front-door", "caddy", "--name", "acme"], io=io)
        assert code == 1 and "Stopped; nothing was changed." in out and "Security group tico-acme:" in out, out


# --- Codex's sixth review ------------------------------------------------------------------------------------------


class NotFoundEC2(FilteredEC2):
    """As EC2 answers a describe naming an id it does not know: InvalidInstanceID.NotFound / InvalidAllocationID.NotFound
    / InvalidGroup.NotFound, never an empty list."""

    def describe_instances(self, Filters=(), InstanceIds=()):
        if InstanceIds and not any(i["InstanceId"] in InstanceIds for i in self.instances):
            raise ClientError("InvalidInstanceID.NotFound")
        return super().describe_instances(Filters, InstanceIds)

    def describe_addresses(self, Filters=(), AllocationIds=()):
        if AllocationIds and not any(a["AllocationId"] in AllocationIds for a in self.eips):
            raise ClientError("InvalidAllocationID.NotFound")
        return super().describe_addresses(Filters, AllocationIds)

    def describe_security_groups(self, Filters=(), GroupIds=()):
        if GroupIds:
            rows = [g for g in self.sgs if g["GroupId"] in GroupIds]
            if not rows:
                raise ClientError("InvalidGroup.NotFound")
            return {"SecurityGroups": rows}
        return super().describe_security_groups(Filters)


def test_a_recorded_instance_missing_from_a_listing_is_read_by_its_id_and_never_launched_again():
    # Codex review 6, part 1, case 13: the launch's answer came back and its id was saved, the run stopped, and on the
    # re-run EC2's tag listing (eventually consistent) did not show the instance yet: setup launched a second server with
    # a new ClientToken and recorded it over the first. A recorded instance is now read by its id before any launch.
    p, saved = hub_plan(domain=DOMAIN), []
    ec2 = NotFoundEC2()
    first = strict_dep(
        p, ec2, IAM(), fakes.FakeSSM(), ids={}, save=lambda: saved.append(json.loads(json.dumps(first.ids)))
    )
    iid = first.launch("#!/bin/bash\n", "hub-hotmic", "sg-1")
    token = ec2.called("run_instances")[0]["ClientToken"]
    assert saved[-1]["instance"] == iid and saved[-1]["instance_token"] == token  # kept for the install's life
    ec2.hide(iid, 99)  # every listing misses it; a describe by its id finds it
    again = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=json.loads(json.dumps(saved[-1])))
    again.preflight()
    assert again.launch_or_reuse({"sg": "sg-1", "alloc": ""}, "#!/bin/bash\n") == (iid, False)
    assert len(ec2.called("run_instances")) == 1 and again.ids["instance"] == iid
    assert again.ids["instance_token"] == token

    # Read by its id and found terminated, or not found at all: refused with the step to take by hand, never launched.
    for gone in ("terminated", "shutting-down", "unknown"):
        if gone == "unknown":
            ec2.instances = []
        else:
            ec2.instances[0]["State"] = {"Name": gone}
        slept = []
        d = owned.StrictDeployer(
            p, ec2, IAM(), fakes.FakeSSM(), ACCOUNT, sleep=slept.append, ids=json.loads(json.dumps(saved[-1]))
        )
        with pytest.raises(MissingInput) as e:
            d.launch_or_reuse({"sg": "sg-1", "alloc": ""}, "#!/bin/bash\n")
        said = str(e.value)
        assert iid in said and "refusing to launch another" in said and '"instance"' in said, said
        assert len(ec2.called("run_instances")) == 1 and d.ids["instance"] == iid, gone
        if gone == "unknown":  # EC2 may not know a just-launched id yet: waited for, a bounded while
            assert slept == [1, 2, 4, 8, 16]
        else:
            ec2.instances[0].pop("State")

    # Terminated by this install's own destroy (journalled before the call): a new server, under a new ClientToken.
    ec2, iam, ssm = NotFoundEC2(), IAM(), fakes.FakeSSM()
    ids = {}
    d = strict_dep(p, ec2, iam, ssm, ids=ids)
    d.launch_or_reuse({"sg": "sg-1", "alloc": ""}, "#!/bin/bash\n")
    ec2.terminate_instances = lambda **kw: [i.update(State={"Name": "terminated"}) for i in ec2.instances]
    owned.destroy(ec2, iam, ssm, {"instance": ["i-1"]}, print, lambda s: None, HUB_ID, "hotmic", ids)
    assert ids["deleted"] == {"instance": ["i-1"]}
    d = strict_dep(p, ec2, iam, ssm, ids=ids)
    assert d.launch_or_reuse({"sg": "sg-1", "alloc": ""}, "#!/bin/bash\n") == ("i-2", True)
    tokens = [kw["ClientToken"] for kw in ec2.called("run_instances")]
    assert ids["instance"] == "i-2" and len(set(tokens)) == 2 and ids["instance_token"] == tokens[-1]
    assert ids["deleted"] == {"instance": []}


class AllocatingEC2(NotFoundEC2):
    """Each AllocateAddress makes a new address (eipalloc-1, -2, ...), as EC2 does."""

    def allocate_address(self, **kw):
        a = super().allocate_address(**kw)
        a["AllocationId"] = f"eipalloc-{len(self.eips)}"
        return a

    def release_address(self, **kw):
        super().release_address(**kw)
        self.eips = [a for a in self.eips if a["AllocationId"] != kw["AllocationId"]]


def test_a_recorded_elastic_ip_missing_from_a_listing_is_read_by_its_id_and_never_allocated_again():
    # Codex review 6, part 1, case 11: with an allocation recorded (made, or adopted by preflight after a lost answer) but
    # missing from the next tag listing, setup allocated a second address and recorded it over the first. A recorded
    # AllocationId is now read by its id; missing, it is waited for and refused, never allocated again or overwritten.
    p = hub_plan(domain=DOMAIN)
    for how in ("made", "lost answer, adopted by preflight"):
        ec2, ids = AllocatingEC2(), {}
        d = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids)
        if how == "made":
            d.ensure_eip()
        else:
            ids["intents"] = {"eip": {"name": "hub-hotmic", "type": "eip"}}
            ec2.allocate_address(Domain="vpc", TagSpecifications=[{"Tags": HUB_ID.tags("hotmic", "hub-hotmic")}])
            d.preflight()
        assert ids["eip"] == "eipalloc-1"
        ec2.hide("eipalloc-1", 99)  # every listing misses it from now on
        again = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids)
        again.preflight()
        assert again.ensure_eip() == ("eipalloc-1", "203.0.113.9"), how
        assert ids["eip"] == "eipalloc-1" and len(ec2.called("allocate_address")) == 1, how

    # Gone (EC2 does not know it, after a wait), and not released by setup: refused with the step, never allocated.
    ec2.eips, slept = [], []
    d = owned.StrictDeployer(p, ec2, IAM(), fakes.FakeSSM(), ACCOUNT, sleep=slept.append, ids=ids, record="S.json")
    with pytest.raises(MissingInput) as e:
        d.ensure_eip()
    said = str(e.value)
    assert "eipalloc-1" in said and "refusing to allocate another" in said and '"eip" under "aws_ids" in S.json' in said
    assert slept == [1, 2, 4, 8, 16] and ids["eip"] == "eipalloc-1" and len(ec2.called("allocate_address")) == 1

    # Released by this install's own destroy (journalled first): the next run allocates a new one.
    ec2, ids = AllocatingEC2(), {}
    d = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids)
    d.ensure_eip()
    owned.destroy(
        ec2, IAM(), fakes.FakeSSM(), {"elastic-ip": ["eipalloc-1"]}, print, lambda s: None, HUB_ID, "hotmic", ids
    )
    assert ids["deleted"] == {"eip": ["eipalloc-1"]} and not ec2.eips
    assert strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids).ensure_eip()[0] == "eipalloc-1"
    assert len(ec2.called("allocate_address")) == 2 and ids["eip"] == "eipalloc-1" and ids["deleted"] == {"eip": []}


def test_an_elastic_ip_intent_whose_call_provably_made_nothing_is_closed_and_any_other_names_the_exact_check():
    # Codex review 6, "tried and held" 3-4: an interruption that leaves an eip intent with no allocation made a plain
    # re-run refuse until someone checked the console. Where setup can prove the call made nothing (the request never
    # left, or EC2 answered its single attempt with a 4xx), it closes the intent itself; else the refusal names the
    # exact check: the region, the tag filter, and the entry to remove from which file.
    p = hub_plan(domain=DOMAIN)
    unsent = type("ParamValidationError", (Exception,), {})("bad parameter")
    refused = ClientError("AddressLimitExceeded")
    refused.response["ResponseMetadata"] = {"HTTPStatusCode": 400, "RetryAttempts": 0}
    refused.operation_name = "AllocateAddress"  # as botocore's ClientError names the call it answers
    for err in (unsent, refused):
        ec2, ids = AllocatingEC2(), {}
        real = ec2.allocate_address
        ec2.allocate_address = lambda **kw: (_ for _ in ()).throw(err)
        d = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids)
        with pytest.raises(type(err)):
            d.ensure_eip()
        assert not (ids.get("intents") or {}).get("eip"), err  # nothing was made: no intent left open
        ec2.allocate_address = real
        assert strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids).ensure_eip()[0] == "eipalloc-1"
    # Ambiguous: the process stopped after the intent was saved (or the answer was lost). Refused, with the exact check.
    for err in (Lost("allocate_address"), ClientError("InternalError")):
        ec2, ids = AllocatingEC2(), {}
        ec2.allocate_address = lambda **kw: (_ for _ in ()).throw(err)
        d = strict_dep(p, ec2, IAM(), fakes.FakeSSM(), ids=ids)
        with pytest.raises(type(err)):
            d.ensure_eip()
        assert ids["intents"]["eip"]
        d = owned.StrictDeployer(
            p, ec2, IAM(), fakes.FakeSSM(), ACCOUNT, sleep=lambda s: None, ids=ids, record="S.json"
        )
        with pytest.raises(MissingInput) as e:
            d.ensure_eip()
        said = str(e.value)
        assert (
            "aws ec2 describe-addresses --region us-west-2 --filters Name=tag:hub-setup-install,Values=0123456789abcdef"
            in said
        ), said
        assert 'remove "eip" under "intents" under "aws_ids" in S.json' in said, said


def test_every_recorded_resource_is_read_by_its_id_and_a_gone_one_is_never_made_again_or_overwritten(tmp_path):
    # Codex review 6, the rule of cases 11 and 13 for every recorded resource: a security group missing from a listing
    # was created again and its id recorded over the first; a recorded IAM role, instance profile, SSM parameter, backup
    # user or bucket found gone was made again. Each recorded one is read by its id (or name, for IAM, SSM and S3) and,
    # gone, refused with the step, unless this install's own destroy journalled deleting it.
    p = hub_plan(domain=DOMAIN)
    ec2, iam, ssm, ids = NotFoundEC2(), IAM(), fakes.FakeSSM(), {}
    d = strict_dep(p, ec2, iam, ssm, ids=ids)
    d.put_env("A=1\n")
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\n")
    made = {k: ids[k] for k in ("sg", "role", "profile", "param", "instance")}
    ec2.hide(made["sg"], 99)  # every listing misses it
    again = strict_dep(p, ec2, iam, ssm, ids=ids)
    again.preflight()
    assert again.ensure_sg() == made["sg"] and len(ec2.called("create_security_group")) == 1

    def world():
        return (ec2.sgs, ec2.eips, ec2.instances, sorted(iam.roles), sorted(iam.profiles), sorted(ssm.params))

    def gone(remove, key, what):
        snap = json.loads(json.dumps(ids))
        undo = remove()
        before = json.dumps(world(), sort_keys=True)
        for step in ("preflight", "run"):
            d = owned.StrictDeployer(
                p, ec2, iam, ssm, ACCOUNT, sleep=lambda s: None, ids=json.loads(json.dumps(snap)), record="S.json"
            )
            with pytest.raises(MissingInput) as e:
                if step == "preflight":
                    d.preflight()
                else:  # the step that would make it again
                    {"sg": d.prepare, "param": lambda: d.put_env("A=1\n")}.get(key, d.ensure_role)()
            said = str(e.value)
            assert what in said and f'"{key}" under "aws_ids" in S.json' in said, (key, step, said)
            assert d.ids[key] == snap[key], key  # never overwritten
        assert json.dumps(world(), sort_keys=True) == before, key  # nothing made again
        undo()

    sgs, roles, profiles, params = list(ec2.sgs), dict(iam.roles), dict(iam.profiles), dict(ssm.params)
    gone(lambda: (ec2.sgs.clear(), lambda: ec2.sgs.extend(sgs))[1], "sg", "Security group")
    gone(lambda: (ssm.params.clear(), lambda: ssm.params.update(params))[1], "param", "SSM parameter")
    gone(lambda: (iam.profiles.clear(), lambda: iam.profiles.update(profiles))[1], "profile", "IAM instance profile")
    gone(lambda: (iam.roles.clear(), lambda: iam.roles.update(roles))[1], "role", "IAM role")

    # Deleted by this install's own destroy (journalled first): a re-run makes each anew, and records the new ids.
    ec2.hidden = {}
    found = aws.discover(ec2, iam, ssm, "hotmic", HUB_ID)
    stop = ec2.terminate_instances  # frees the address, as EC2 does
    ec2.terminate_instances = lambda **kw: (
        [stop(**kw)] + [i.update(State={"Name": "terminated"}) for i in ec2.instances]
    )
    ec2.release_address = lambda **kw: ec2.eips.clear()
    ec2.delete_security_group = lambda **kw: ec2.sgs.clear()
    owned.destroy(ec2, iam, ssm, found, print, lambda s: None, HUB_ID, "hotmic", ids)
    assert set(ids["deleted"]) >= {"instance", "eip", "sg", "role", "profile", "param"}, ids["deleted"]
    d = strict_dep(p, ec2, iam, ssm, ids=ids)
    d.preflight()
    d.put_env("A=1\n")
    d.launch_or_reuse(d.prepare(), "#!/bin/bash\n")
    assert ids["instance"] == "i-2" and all(ids.get(k) for k in made), ids

    # S3 and the backup user: a recorded bucket or user found gone is refused, never created again.
    s3, iam = S3(), IAM()
    kw = dict(domain=DOMAIN, region="us-west-2", account_id=ACCOUNT, name="hotmic", names=HUB_ID)
    ids = {}
    owned.create_s3(s3, iam, ids=ids, **kw)
    for key, remove in (("backup_bucket", lambda: s3.tagsets.clear()), ("backup_user", lambda: iam.users.clear())):
        snap = json.loads(json.dumps(ids))
        keep = (dict(s3.tagsets), dict(iam.users))
        remove()
        with pytest.raises(MissingInput) as e:
            owned.create_s3(s3, iam, ids=snap, record="S.json", sleep=lambda s: None, **kw)
        assert f'"{key}" under "aws_ids" in S.json' in str(e.value), (key, str(e.value))
        assert snap[key] == ids[key] and len(s3.called("create_bucket")) == 1 and len(iam.called("create_user")) == 1
        s3.tagsets.update(keep[0])
        iam.users.update(keep[1])


def test_a_plan_upload_retry_never_replaces_its_recorded_version_or_adopts_an_object_that_was_already_there():
    # Codex review 6, part 1, case 9: another writer put a version v2 with identical bytes and matching sha256 metadata;
    # the re-run's put got 412, HEAD showed v2, and setup recorded v2 over its own v1, so cleanup then deleted the other
    # writer's version. And an intent written by this very call made an object already at the key look like its own.
    p = hub_plan(domain=DOMAIN, plan_bucket="hub-plans-x")
    key = "plans/hotmic/" + hashlib.sha256(b"ours").hexdigest()
    s3, ids = S3(), {}
    d = strict_dep(p, None, None, None, ids=ids)
    d.ensure_plan_bucket(s3, "us-west-2")
    s3.put_bucket_versioning(Bucket="hub-plans-x", VersioningConfiguration={"Status": "Enabled"})
    d.put_plan(s3, key, "ours")
    v1 = ids["plan_object"]["version_id"]
    meta = dict(s3.objects[("hub-plans-x", key)]["Metadata"])
    s3.put_object(Bucket="hub-plans-x", Key=key, Body=b"ours", Metadata=meta)  # v2: the same bytes and metadata
    with pytest.raises(MissingInput, match="refusing"):
        strict_dep(p, None, None, None, ids=ids).put_plan(s3, key, "ours")
    assert ids["plan_object"]["version_id"] == v1  # the recorded evidence is kept
    assert d.delete_plan(s3, key) == "" and s3.called("delete_object")[-1]["VersionId"] == v1
    assert s3.objects[("hub-plans-x", key)]["Metadata"] == meta  # the other writer's version stays

    # An object already at the key, with this install's bytes and even its sha256 and install metadata: never adopted
    # by an intent this call wrote, and the intent is closed (S3's 412 proves this put wrote nothing).
    for versioned in (False, True):
        s3, ids = S3(), {}
        d = strict_dep(p, None, None, None, ids=ids)
        d.ensure_plan_bucket(s3, "us-west-2")
        if versioned:
            s3.put_bucket_versioning(Bucket="hub-plans-x", VersioningConfiguration={"Status": "Enabled"})
        there = {"install": INSTALL, "sha256": hashlib.sha256(b"ours").hexdigest()}
        s3.put_object(Bucket="hub-plans-x", Key=key, Body=b"ours", Metadata=there)
        with pytest.raises(MissingInput, match="did not record writing it"):
            d.put_plan(s3, key, "ours")
        assert "plan_object" not in ids and not (ids.get("intents") or {}).get("plan_object"), versioned
        with pytest.raises(MissingInput, match="did not record writing it"):  # nor on a re-run
            strict_dep(p, None, None, None, ids=ids).put_plan(s3, key, "ours")
        assert d.delete_plan(s3, key) == "NotWrittenByThisInstall" and ("hub-plans-x", key) in s3.objects


def test_every_command_that_changes_a_strict_install_must_name_its_prefix_again(tmp_path, monkeypatch):
    # Codex review 6, case 12: a saved hub install re-run without --asset-prefix went on under the saved prefix, so a
    # command typed for a default install could act on the hub one. Each command that can change its AWS resources now
    # refuses before opening any AWS client, without the flag or with another, and names the exact flag; doctor reads.
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    assert go(hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic"), deps=d)[0] == 0
    state.save(DOMAIN, {**state.load(DOMAIN)[0], "auth": "owner-link"})
    runner = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", f"https://{DOMAIN}"]
    runner += ["--label", "b1", "--aws-region", "us-west-2"]
    commands = [
        hub_args(tmp_path, "--name", "hotmic"),
        ["destroy", "--domain", DOMAIN, "--dns", "--yes"],
        ["backup-storage", "--domain", DOMAIN, "--aws-region", "us-west-2"],
        runner,
        ["owner-link", "--domain", DOMAIN],
    ]
    clients = d.aws_clients
    opened = []
    d.aws_clients = lambda region, profile: opened.append(region) or clients(region, profile)
    d.aws_s3 = lambda region, profile: opened.append(region) or S3()
    before = len(log)
    for argv in commands:
        for flag in ([], ["--asset-prefix", "tico"], ["--asset-prefix", "other"]):
            code, out = go([*argv, *flag], deps=d)
            assert code == 2 and f"{DOMAIN} was set up with --asset-prefix hub" in out, (argv, flag, out)
            assert "Run the command again with --asset-prefix hub." in out, (argv, flag, out)
    assert (len(log), opened) == (before, [])  # refused before any AWS client or call
    code, out = go(["doctor", "--domain", DOMAIN, "--non-interactive"], deps=d)  # reads only: the saved prefix
    assert "--asset-prefix" not in out, out


def test_runner_doctor_proves_the_runners_box_before_any_shell_on_it(monkeypatch):
    # Codex review 6, part 2, case 2: server doctor and owner-link proved their instance first, but runner doctor sent a
    # shell to whatever instance id the record named, in whatever account the credentials were for. A strict runner's
    # box is now read first: the install's tags, the instance it recorded making, the account it recorded.
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    argv = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", "https://other.example"]
    assert go([*argv, "--label", "b1", "--aws-region", "us-west-2", "--asset-prefix", "hub"], deps=d)[0] == 0
    (good,) = state.load_runners()
    base = len(ssm.commands)  # the runner's own setup
    key = runner_setup.runner_key(
        Settings(server_url=good["server_url"], aws_region="us-west-2", asset_prefix="hub"), good["name"]
    )
    ec2.instances.append({"InstanceId": "i-prod", "Tags": PRODUCTION, "State": {"Name": "running"}})
    sent = lambda: len(ssm.commands) - base  # noqa: E731
    io = fakes.CaptureIO()
    runner_setup.doctor_runners(io, d, "https://other.example")
    checked = sent()
    assert checked, io.text  # its own box: a shell runs the check
    clients = d.aws_clients
    for edit, account in (
        ({"instance_id": "i-prod"}, HOTMIC_ACCOUNT),  # another actor's box named in the record
        ({}, "999999999999"),  # the credentials are another account's
        ({"instance_id": "i-prod", "aws_account_id": "999999999999"}, "999999999999"),  # both edited to match
        ({"install_id": ""}, HOTMIC_ACCOUNT),  # nothing to prove it with
    ):
        state.save_runner(key, {**good, **edit})
        d.aws_clients = lambda r, p, account=account: (*clients(r, p)[:3], account)
        io = fakes.CaptureIO()
        assert not runner_setup.doctor_runners(io, d, "https://other.example"), (edit, io.text)
        assert "refusing to" in io.text and sent() == checked, (edit, io.text)
    state.save_runner(key, good)
    d.aws_clients = clients
    ec2.instances[0]["Tags"] = [t for t in ec2.instances[0]["Tags"] if "install" not in t["Key"]]  # re-tagged
    io = fakes.CaptureIO()
    assert not runner_setup.doctor_runners(io, d, "https://other.example") and sent() == checked, io.text
    assert "refusing to" in io.text, io.text


def test_a_default_install_keeps_working_beside_a_strict_install_of_the_same_name(tmp_path, monkeypatch):
    # Codex review 6, part 2, case 1: after a hub install named hotmic, the existing tico install named hotmic on the
    # same computer could no longer re-run or be destroyed: the name marker refused it, and named a prefix its own saved
    # state refuses. A default-path command whose domain has its own saved default state is that install's.
    monkeypatch.setenv("TICO_ENROLL_CODE", "one-time-code")
    monkeypatch.setattr(runner_setup, "_verify", lambda *a: 0)
    d, ec2, iam, ssm, s3, log = hub_world()
    tico = "tico.hotmicapp.com"
    r = fakes.FakeResolver({(DOMAIN, dns.A): ["203.0.113.9"], (tico, dns.A): ["203.0.113.9"]}, {ZONE: fakes.R53_NS})
    d.resolver, d.public_resolvers = r, (lambda: [("Google", r)])
    default = hub_args(tmp_path, "--name", "hotmic")
    default[default.index(DOMAIN)] = tico
    code, out = go(default, deps=d)
    assert code == 0 and state.load(tico)[0].get("asset_prefix", "tico") == "tico", out
    hub = hub_args(tmp_path, "--asset-prefix", "hub", "--name", "hotmic")
    code, out = go(hub, deps=d)
    assert code == 0, out
    # The default install, named by its domain: re-run, backup storage, a runner of it, destroy.
    code, out = go(default, deps=d)
    assert code == 0 and "already exists; reusing it" in out, out
    code, out = go(["backup-storage", "--domain", tico, "--aws-region", "us-west-2"], deps=d)
    assert code == 0, out
    runner = ["runner", "--non-interactive", "--yes", "--cloud", "aws", "--server-url", f"https://{tico}"]
    code, out = go([*runner, "--label", "b1", "--aws-region", "us-west-2"], deps=d)
    assert code == 0, out
    # And the strict install beside it, as before.
    code, out = go(hub, deps=d)
    assert code == 0 and "already exists; reusing it" in out, out
    # By name alone (no saved state names the install meant), the name marker still refuses.
    before = len(log)
    code, out = go(["destroy", "--name", "hotmic", "--yes"], deps=d)
    assert code == 2 and "Run the command again with --asset-prefix hub" in out, out
    assert len(log) == before
    code, out = go(["destroy", "--domain", tico, "--yes"], deps=d)
    assert code == 0 and "Tagged tico-setup-name=hotmic in us-west-2:" in out, out
    assert all(not kw["Name"].startswith("/hub/") for kw in ssm.called("delete_parameter"))
    assert all(kw["RoleName"].startswith("tico-") for kw in iam.called("delete_role"))
    code, out = go(["destroy", "--domain", DOMAIN, "--asset-prefix", "hub", "--dns", "--yes"], deps=d)
    assert code == 0 and "Tagged hub-setup-name=hotmic in us-west-2:" in out, out
