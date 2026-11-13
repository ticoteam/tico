"""The default prefix (no --asset-prefix, or tico) makes exactly the AWS calls it made before --asset-prefix existed.

One stateful fake AWS account records every call (reads too, in order, with their arguments) through a default install,
its re-run, a runner, `setup backup-storage` and `setup destroy --dns`. golden/default_prefix_calls.json is that
sequence, first recorded on main at eb08ac89b, before --asset-prefix. A change to setup/ re-records it in the same
change, so the call diff is reviewed with the code that made it:

    TICO_GOLDEN_WRITE=setup/tests/golden/default_prefix_calls.json pytest setup/tests/test_default_prefix_golden.py

Its "_recorded_on" holds a digest of the setup/ code it was recorded from (setup_digest: every file under setup/ but
its tests). test_the_recording_is_of_this_setup fails while the checkout's own setup/ differs from that digest. It
reads only the files here, never another ref, so a change that passes it passes it once merged too."""

from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import os
import secrets
import uuid
from pathlib import Path

import pytest

from backend import company_file as C
from setup import cli, dns, verify
from setup.tests import fakes
from setup.tests.fakes import CaptureIO

GOLDEN = Path(__file__).parent / "golden" / "default_prefix_calls.json"
DOMAIN, ZONE, ACCOUNT, REGION = "tico.example.com", "example.com", "123456789012", "us-east-1"
CODE = "join-code-0123456789"


class AwsError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class Exc:
    class EntityAlreadyExistsException(Exception):
        pass

    class NoSuchEntityException(Exception):
        pass

    class InvalidResourceId(Exception):
        pass

    class InvocationDoesNotExist(Exception):
        pass


def scrub(v):
    """Arguments as recorded: user data unzipped, secrets in the env reduced to its keys."""
    if isinstance(v, bytes):
        try:
            v = gzip.decompress(v)
        except OSError:
            pass
        return v.decode(errors="replace")
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in sorted(v.items())}
    if isinstance(v, (list, tuple)):
        return [scrub(x) for x in v]
    return v


class Recorder:
    """Logs every call made on `inner` (name and arguments), then makes it."""

    def __init__(self, service, inner, log):
        self._service, self._inner, self._log = service, inner, log

    @property
    def exceptions(self):
        return Exc

    def __getattr__(self, name):
        target = getattr(self._inner, name)

        def call(*a, **kw):
            kw2 = dict(kw)
            if name == "put_parameter":
                kw2["Value"] = sorted(line.partition("=")[0] for line in kw["Value"].splitlines() if "=" in line)
            if name == "put_object":
                kw2["Body"] = len(kw["Body"])
            if name == "send_command":
                kw2 = {"InstanceIds": kw.get("InstanceIds"), "DocumentName": kw.get("DocumentName")}
            self._log.append([self._service, name, scrub(kw2)])
            return target(*a, **kw)

        return call


class EC2:
    def __init__(self):
        self.instances, self.sgs, self.eips, self.n = [], [], [], itertools.count(1)

    def _ours(self, rows, Filters):
        want = {f["Name"][4:]: f["Values"] for f in Filters if f["Name"].startswith("tag:")}
        states = next((f["Values"] for f in Filters if f["Name"] == "instance-state-name"), None)
        out = []
        for r in rows:
            tags = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
            if all(tags.get(k) in v for k, v in want.items()) and (not states or r["State"]["Name"] in states):
                out.append(r)
        return out

    def describe_instances(self, Filters=(), InstanceIds=()):
        rows = [i for i in self.instances if not InstanceIds or i["InstanceId"] in InstanceIds]
        return {"Reservations": [{"Instances": self._ours(rows, Filters)}]}

    def describe_security_groups(self, Filters):
        return {"SecurityGroups": self._ours(self.sgs, Filters)}

    def describe_addresses(self, Filters):
        return {"Addresses": self._ours(self.eips, Filters)}

    def describe_vpcs(self, Filters):
        return {"Vpcs": [{"VpcId": "vpc-1"}]}

    def describe_subnets(self, Filters):
        return {"Subnets": [{"SubnetId": "subnet-1", "DefaultForAz": True}]}

    def describe_route_tables(self, Filters):
        return {
            "RouteTables": [
                {
                    "Associations": [{"Main": True}],
                    "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-1"}],
                }
            ]
        }

    def describe_volumes(self, Filters):
        return {"Volumes": []}

    def describe_network_interfaces(self, Filters):
        return {"NetworkInterfaces": []}

    def create_security_group(self, **kw):
        gid = f"sg-{next(self.n)}"
        self.sgs.append({"GroupId": gid, "Tags": kw["TagSpecifications"][0]["Tags"], "IpPermissions": []})
        return {"GroupId": gid}

    def authorize_security_group_ingress(self, **kw):
        pass

    def allocate_address(self, **kw):
        a = {
            "AllocationId": f"eipalloc-{next(self.n)}",
            "PublicIp": "203.0.113.9",
            "Tags": kw["TagSpecifications"][0]["Tags"],
        }
        self.eips.append(a)
        return dict(a)

    def associate_address(self, AllocationId, InstanceId, **kw):
        next(a for a in self.eips if a["AllocationId"] == AllocationId)["InstanceId"] = InstanceId

    def run_instances(self, **kw):
        iid = f"i-{next(self.n)}"
        tags = kw["TagSpecifications"][0]["Tags"]
        self.instances.append({"InstanceId": iid, "Tags": tags, "State": {"Name": "running"}})
        return {"Instances": [{"InstanceId": iid}]}

    def get_waiter(self, name):
        class W:
            def wait(self, **kw):
                pass

        return W()

    def terminate_instances(self, InstanceIds):
        for i in self.instances:
            if i["InstanceId"] in InstanceIds:
                i["State"] = {"Name": "terminated"}

    def release_address(self, AllocationId):
        self.eips = [a for a in self.eips if a["AllocationId"] != AllocationId]

    def delete_security_group(self, GroupId):
        self.sgs = [g for g in self.sgs if g["GroupId"] != GroupId]


class IAM:
    exceptions = Exc

    def __init__(self):
        self.roles, self.profiles, self.users, self.keys = {}, {}, {}, {}

    def create_role(self, RoleName, Tags=(), **kw):
        if RoleName in self.roles:
            raise Exc.EntityAlreadyExistsException()
        self.roles[RoleName] = {"RoleName": RoleName, "RoleId": "AROA" + RoleName, "Tags": list(Tags)}
        return {"Role": dict(self.roles[RoleName])}

    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise Exc.NoSuchEntityException()
        return {"Role": dict(self.roles[RoleName])}

    def list_role_tags(self, RoleName):
        return {"Tags": self.get_role(RoleName)["Role"]["Tags"]}

    def attach_role_policy(self, **kw):
        self.get_role(kw["RoleName"])

    def detach_role_policy(self, **kw):
        self.get_role(kw["RoleName"])

    def put_role_policy(self, **kw):
        self.get_role(kw["RoleName"])

    def delete_role_policy(self, **kw):
        self.get_role(kw["RoleName"])

    def delete_role(self, RoleName):
        self.get_role(RoleName)
        del self.roles[RoleName]

    def create_instance_profile(self, InstanceProfileName, Tags=()):
        if InstanceProfileName in self.profiles:
            raise Exc.EntityAlreadyExistsException()
        self.profiles[InstanceProfileName] = {
            "InstanceProfileName": InstanceProfileName,
            "InstanceProfileId": "AIPA" + InstanceProfileName,
            "Tags": list(Tags),
            "Roles": [],
        }
        return {"InstanceProfile": dict(self.profiles[InstanceProfileName])}

    def get_instance_profile(self, InstanceProfileName):
        if InstanceProfileName not in self.profiles:
            raise Exc.NoSuchEntityException()
        return {"InstanceProfile": dict(self.profiles[InstanceProfileName])}

    def add_role_to_instance_profile(self, InstanceProfileName, RoleName):
        self.profiles[InstanceProfileName]["Roles"].append({"RoleName": RoleName})

    def remove_role_from_instance_profile(self, InstanceProfileName, RoleName):
        p = self.profiles[InstanceProfileName]
        p["Roles"] = [r for r in p["Roles"] if r["RoleName"] != RoleName]

    def delete_instance_profile(self, InstanceProfileName):
        self.profiles.pop(InstanceProfileName)

    def get_user(self, UserName):
        if UserName not in self.users:
            raise Exc.NoSuchEntityException()
        return {"User": dict(self.users[UserName])}

    def create_user(self, UserName, Tags=()):
        self.users[UserName] = {"UserName": UserName, "UserId": "AIDA" + UserName, "Tags": list(Tags)}
        return {"User": dict(self.users[UserName])}

    def list_user_tags(self, UserName):
        return {"Tags": self.get_user(UserName)["User"]["Tags"]}

    def tag_user(self, UserName, Tags):
        self.users[UserName]["Tags"] = list(Tags)

    def put_user_policy(self, **kw):
        pass

    def list_access_keys(self, UserName):
        return {"AccessKeyMetadata": self.keys.get(UserName, [])}

    def create_access_key(self, UserName):
        keys = self.keys.setdefault(UserName, [])
        k = {"AccessKeyId": f"AKIA{len(keys)}", "CreateDate": len(keys)}
        keys.append(k)
        return {"AccessKey": {**k, "SecretAccessKey": "secret"}}

    def delete_access_key(self, UserName, AccessKeyId):
        self.keys[UserName] = [k for k in self.keys[UserName] if k["AccessKeyId"] != AccessKeyId]


class SSM:
    exceptions = Exc

    def __init__(self):
        self.params = {}

    def get_parameter(self, Name, **kw):
        return {"Parameter": {"Value": "ami-123"}}

    def put_parameter(self, Name, Overwrite=False, Tags=(), **kw):
        if Name in self.params and not Overwrite:
            raise AwsError("ParameterAlreadyExists")
        self.params.setdefault(Name, {"Tags": list(Tags)})

    def add_tags_to_resource(self, ResourceId, Tags, **kw):
        have = {t["Key"]: t for t in self.params[ResourceId]["Tags"]}
        have.update({t["Key"]: t for t in Tags})
        self.params[ResourceId]["Tags"] = list(have.values())

    def list_tags_for_resource(self, ResourceId, **kw):
        if ResourceId not in self.params:
            raise Exc.InvalidResourceId()
        return {"TagList": self.params[ResourceId]["Tags"]}

    def delete_parameter(self, Name):
        self.params.pop(Name)

    def describe_instance_information(self, Filters):
        return {"InstanceInformationList": [{"PingStatus": "Online"}]}

    def send_command(self, **kw):
        return {"Command": {"CommandId": "c1"}}

    def get_command_invocation(self, **kw):
        return {
            "Status": "Success",
            "ResponseCode": 0,
            "StandardOutputContent": "Up 3 seconds\n",
            "StandardErrorContent": "",
        }


class S3:
    def __init__(self):
        self.buckets, self.objects = {}, {}

    def head_bucket(self, Bucket, **kw):
        if Bucket not in self.buckets:
            raise AwsError("404")

    def create_bucket(self, Bucket, **kw):
        if Bucket in self.buckets:
            raise AwsError("BucketAlreadyOwnedByYou")
        self.buckets[Bucket] = {"Tags": list(kw.get("CreateBucketConfiguration", {}).get("Tags", []))}

    def put_bucket_tagging(self, Bucket, Tagging, **kw):
        self.buckets[Bucket]["Tags"] = Tagging["TagSet"]

    def get_bucket_tagging(self, Bucket, **kw):
        if not self.buckets[Bucket]["Tags"]:
            raise AwsError("NoSuchTagSet")
        return {"TagSet": self.buckets[Bucket]["Tags"]}

    def put_object(self, Bucket, Key, **kw):
        self.objects[(Bucket, Key)] = kw["Body"]
        return {"ETag": '"e"'}

    def delete_object(self, Bucket, Key, **kw):
        self.objects.pop((Bucket, Key), None)

    def __getattr__(self, name):  # the put_bucket_* settings
        if not name.startswith("put_"):
            raise AttributeError(name)
        return lambda **kw: None


class Route53:
    def __init__(self):
        self.records = []

    def list_hosted_zones_by_name(self, DNSName, MaxItems):
        return {"HostedZones": [{"Id": "/hostedzone/Z1", "Name": ZONE + "."}]}

    def get_hosted_zone(self, Id):
        return {"DelegationSet": {"NameServers": fakes.R53_NS}}

    def list_resource_record_sets(self, HostedZoneId, StartRecordName, StartRecordType, MaxItems):
        rows = sorted(self.records, key=lambda r: (r["Name"].rstrip("."), r["Type"]))
        return {
            "ResourceRecordSets": [
                r for r in rows if (r["Name"].rstrip("."), r["Type"]) >= (StartRecordName, StartRecordType)
            ]
        }

    def change_resource_record_sets(self, HostedZoneId, ChangeBatch):
        for c in ChangeBatch["Changes"]:
            rs = c["ResourceRecordSet"]
            self.records = [r for r in self.records if (r["Name"], r["Type"]) != (rs["Name"], rs["Type"])]
            if c["Action"] in ("UPSERT", "CREATE"):
                self.records.append(rs)
        return {"ChangeInfo": {"Id": "/change/C1", "Status": "PENDING"}}


@pytest.fixture
def chart(tmp_path):
    from backend.tests.helpers.company_file import chart

    draft = chart(3)
    draft["company"].update(name="Acme", about="We make things.")
    path = tmp_path / "tico.company.yaml"
    path.write_text(C.to_yaml(draft))
    return path


@pytest.fixture(autouse=True)
def steady(monkeypatch):
    """The same secrets and ids every run, so the recorded arguments compare."""
    n = itertools.count()
    monkeypatch.setattr(secrets, "token_hex", lambda k=32: f"{next(n):0{2 * k}x}"[-2 * k :])
    monkeypatch.setattr(secrets, "token_urlsafe", lambda k=32: f"tok{next(n)}")
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(n)))
    # gzip's header is the interpreter's, not setup's: Python 3.15 made level 6 the default (XFL byte 0, was 2 at 9) and
    # the OS byte is whatever its zlib build writes (19 for the macOS Python this was recorded with, 255 for python.org
    # builds). The starter plan's S3 key is the sha256 of its gzip and user data inlines gzipped scripts, so pin both
    # to the recording's: level 9 when the caller names none, OS byte 19. The compressed data itself is unchanged.
    real_compress = gzip.compress

    def compress(data, compresslevel=9, *, mtime=None):
        out = bytearray(real_compress(data, compresslevel, mtime=mtime))
        out[9] = 19
        return bytes(out)

    monkeypatch.setattr(gzip, "compress", compress)
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "client-SECRET-0123456789abcdef")
    monkeypatch.setenv("TICO_ENROLL_CODE", CODE)


def world(log):
    ec2, iam, ssm, s3, r53 = EC2(), IAM(), SSM(), S3(), Route53()
    r = fakes.FakeResolver({(DOMAIN, dns.A): ["203.0.113.9"]}, {ZONE: fakes.R53_NS})
    d = fakes.deps()
    d.resolver, d.public_resolvers = r, (lambda: [("Google", r)])
    d.aws_clients = lambda region, profile: (
        Recorder("ec2", ec2, log),
        Recorder("iam", iam, log),
        Recorder("ssm", ssm, log),
        ACCOUNT,
    )
    d.aws_s3 = lambda region, profile: Recorder("s3", s3, log)
    d.route53_client = lambda profile: Recorder("route53", r53, log)
    return d


INSTALL = [
    "--target",
    "aws",
    "--domain",
    DOMAIN,
    "--aws-region",
    REGION,
    "--front-door",
    "caddy",
    "--backup",
    "aws",
    "--auth",
    "google",
    "--client-id",
    "cid.apps.googleusercontent.com",
    "--company",
    "Acme",
    "--owner-email",
    "me@example.com",
    "--skip-dns-wait",
    "--non-interactive",
    "--yes",
]
RUNNER = [
    "runner",
    "--cloud",
    "aws",
    "--aws-region",
    REGION,
    "--server-url",
    f"https://{DOMAIN}",
    "--label",
    "build",
    "--runner-tag",
    "v0.3.71",
    "--non-interactive",
    "--yes",
]


def calls(chart, flag=()) -> dict[str, list]:
    log: list = []
    d = world(log)
    out: dict[str, list] = {}
    steps = {
        "install": [*INSTALL, "--company-file", str(chart)],
        "rerun": [*INSTALL, "--company-file", str(chart)],
        "runner": RUNNER,
        "backup-storage": ["backup-storage", "--domain", DOMAIN, "--aws-region", REGION],
        "destroy": ["destroy", "--domain", DOMAIN, "--dns", "--yes"],
    }
    for step, argv in steps.items():
        del log[:]
        io = CaptureIO()
        code = cli.main([*argv, *flag], deps=d, io=io)
        assert code == 0, f"{step}: {io.text}"
        out[step] = json.loads(json.dumps(log))
    return out


@pytest.mark.parametrize("flag", [(), ("--asset-prefix", "tico")], ids=["no flag", "--asset-prefix tico"])
def test_the_default_prefix_makes_exactly_the_calls_it_made_before_asset_prefix(chart, flag):
    if flag and os.environ.get("TICO_GOLDEN_WRITE"):
        pytest.skip("the no-flag run records it")
    got = calls(chart, flag)
    if os.environ.get("TICO_GOLDEN_WRITE"):
        here = {"setup_digest": setup_digest(SETUP)}
        Path(os.environ["TICO_GOLDEN_WRITE"]).write_text(json.dumps({"_recorded_on": here, **got}, indent=1) + "\n")
        pytest.skip("written")
    want = {k: v for k, v in json.loads(GOLDEN.read_text()).items() if not k.startswith("_")}
    for step in want:
        assert got[step] == want[step], step
    assert list(got) == list(want)


SETUP = Path(__file__).resolve().parents[1]


def setup_digest(root: Path) -> str:
    """SHA-256 of the setup/ code at `root`: each file's path and content, its tests and caches left out."""
    total = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("tests/") or "__pycache__" in relative.split("/") or path.suffix == ".pyc":
            continue
        total.update(relative.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return total.hexdigest()
