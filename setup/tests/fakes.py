"""Stand-ins for every outside dependency: no test here touches the network or a cloud."""
from __future__ import annotations

from setup import dns as dnsmod
from setup.remote import Result
from setup.ui import IO
from setup.wizard import Deps

AWS_NS = ["ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"]
NETLIFY_NS = ["dns1.p05.nsone.net", "dns2.p05.nsone.net"]


class FakeResolver:
    def __init__(self, records: dict | None = None, ns: dict | None = None):
        self.records, self.ns = records or {}, ns or {}

    def query(self, name, rtype):
        if rtype == dnsmod.NS:
            return list(self.ns.get(name, []))
        return list(self.records.get((name, rtype), []))


class CaptureIO(IO):
    def __init__(self, interactive=False, answers=None):
        super().__init__(interactive)
        self.lines, self.answers = [], list(answers or [])

    def say(self, msg=""):
        self.lines.append(msg)

    def ask(self, question, default="", secret=False):
        return (self.answers.pop(0) if self.answers else "") or default

    def confirm(self, question, default=True):
        return default

    def open_url(self, url):
        self.lines.append(url)

    @property
    def text(self):
        return "\n".join(self.lines)


class FakeShell:
    """Answers commands by substring; records everything it was asked."""

    def __init__(self, replies: dict[str, Result] | None = None, uid="0"):
        self.replies, self.calls, self.uid = replies or {}, [], uid

    def run(self, cmd, input=None):
        self.calls.append((cmd, input))
        if cmd == "id -u":
            return Result(0, self.uid + "\n")
        for k, v in self.replies.items():
            if k in cmd:
                return v
        return Result(0, "")

    def cmds(self):
        return [c for c, _ in self.calls]


def deps(ns=None, records=None, **kw) -> Deps:
    r = FakeResolver(records, ns or {"example.com": NETLIFY_NS})
    return Deps(resolver=r, public_resolvers=lambda: [("Google", r), ("Cloudflare", r)],
                resolve_host=lambda h: "203.0.113.7", sleep=lambda s: None, compose_local=lambda: b"name: tico\n", **kw)


class _Exc:
    class EntityAlreadyExistsException(Exception):
        pass

    class NoSuchEntityException(Exception):
        pass

    class InvalidResourceId(Exception):
        pass

    class InvocationDoesNotExist(Exception):
        pass


class FakeWaiter:
    def wait(self, **kw):
        pass


class FakeEC2:
    """Only what the deployer calls; records calls and holds a little state so re-runs can be tested."""

    def __init__(self, extra_instances=(), extra_sgs=()):
        self.calls, self.instances, self.sgs, self.eips = [], list(extra_instances), list(extra_sgs), []

    def _rec(self, name, **kw):
        self.calls.append((name, kw))

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]

    def describe_instances(self, Filters):
        return {"Reservations": [{"Instances": self.instances}]}

    def describe_security_groups(self, Filters):
        return {"SecurityGroups": self.sgs}

    def describe_addresses(self, Filters):
        return {"Addresses": self.eips}

    def describe_vpcs(self, Filters):
        return {"Vpcs": [{"VpcId": "vpc-1"}]}

    subnets = [{"SubnetId": "subnet-pub", "DefaultForAz": True}]
    route_tables = [{"Associations": [{"Main": True}], "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-1"}]}]

    def describe_subnets(self, Filters):
        return {"Subnets": self.subnets}

    def describe_route_tables(self, Filters):
        return {"RouteTables": self.route_tables}

    def create_security_group(self, **kw):
        self._rec("create_security_group", **kw)
        self.sgs.append({"GroupId": "sg-1", "Tags": kw["TagSpecifications"][0]["Tags"]})
        return {"GroupId": "sg-1"}

    def authorize_security_group_ingress(self, **kw):
        self._rec("authorize_security_group_ingress", **kw)

    def allocate_address(self, **kw):
        self._rec("allocate_address", **kw)
        self.eips.append({"AllocationId": "eipalloc-1", "PublicIp": "203.0.113.9", "Tags": kw["TagSpecifications"][0]["Tags"]})
        return self.eips[-1]

    def associate_address(self, **kw):
        self._rec("associate_address", **kw)

    def run_instances(self, **kw):
        self._rec("run_instances", **kw)
        self.instances.append({"InstanceId": "i-1", "Tags": kw["TagSpecifications"][0]["Tags"]})
        return {"Instances": [{"InstanceId": "i-1"}]}

    def get_waiter(self, name):
        return FakeWaiter()

    def terminate_instances(self, **kw):
        self._rec("terminate_instances", **kw)

    def release_address(self, **kw):
        self._rec("release_address", **kw)

    def delete_security_group(self, **kw):
        self._rec("delete_security_group", **kw)


class FakeIAM:
    exceptions = _Exc

    def __init__(self, role_tags=None):
        self.calls, self.role_tags = [], role_tags

    def _rec(self, name, **kw):
        self.calls.append((name, kw))

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]

    def list_role_tags(self, RoleName):
        if self.role_tags is None:
            raise _Exc.NoSuchEntityException()
        return {"Tags": self.role_tags}

    def __getattr__(self, name):
        return lambda **kw: self._rec(name, **kw)


class FakeSSM:
    exceptions = _Exc

    def __init__(self, param_tags=None, ready=True):
        self.calls, self.param_tags, self.ready = [], param_tags, ready
        self.commands = []

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]

    def get_parameter(self, Name):
        return {"Parameter": {"Value": "ami-123"}}

    def put_parameter(self, **kw):
        self.calls.append(("put_parameter", kw))

    def add_tags_to_resource(self, **kw):
        self.calls.append(("add_tags_to_resource", kw))

    def delete_parameter(self, **kw):
        self.calls.append(("delete_parameter", kw))

    def list_tags_for_resource(self, **kw):
        if self.param_tags is None:
            raise _Exc.InvalidResourceId()
        return {"TagList": self.param_tags}

    def describe_instance_information(self, Filters):
        return {"InstanceInformationList": [{"PingStatus": "Online"}] if self.ready else []}

    def send_command(self, **kw):
        self.commands.append(kw["Parameters"]["commands"][0])
        return {"Command": {"CommandId": "c1"}}

    def get_command_invocation(self, **kw):
        return {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "", "StandardErrorContent": ""}


def aws_clients(ec2=None, iam=None, ssm=None):
    ec2, iam, ssm = ec2 or FakeEC2(), iam or FakeIAM(), ssm or FakeSSM()
    return ec2, iam, ssm, (lambda region, profile: (ec2, iam, ssm, "123456789012"))


R53_NS = ["ns-1.awsdns-01.org", "ns-2.awsdns-02.com"]


class FakeRoute53:
    """One public hosted zone, authoritative with R53_NS; record sets as Route 53 lists them (sorted by name, then type)."""

    def __init__(self, zone: str, records=(), fail: bool = False):
        self.zone, self.records, self.fail, self.calls = zone, list(records), fail, []

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]

    def list_hosted_zones_by_name(self, DNSName, MaxItems):
        if self.fail:
            raise ConnectionError("route53 down")
        return {"HostedZones": [{"Id": "/hostedzone/Z1", "Name": self.zone + "."}]}

    def get_hosted_zone(self, Id):
        return {"DelegationSet": {"NameServers": R53_NS}}

    def list_resource_record_sets(self, HostedZoneId, StartRecordName, StartRecordType, MaxItems):
        rows = sorted(self.records, key=lambda r: (r["Name"].rstrip("."), r["Type"]))
        return {"ResourceRecordSets": [r for r in rows if (r["Name"].rstrip("."), r["Type"]) >= (StartRecordName, StartRecordType)]}

    def change_resource_record_sets(self, HostedZoneId, ChangeBatch):
        self.calls.append(("change_resource_record_sets", {"HostedZoneId": HostedZoneId, "ChangeBatch": ChangeBatch}))
        for c in ChangeBatch["Changes"]:
            rs = c["ResourceRecordSet"]
            self.records = [r for r in self.records if (r["Name"], r["Type"]) != (rs["Name"], rs["Type"])]
            if c["Action"] == "UPSERT":
                self.records.append(rs)
