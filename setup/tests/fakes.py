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
    return Deps(
        resolver=r,
        public_resolvers=lambda: [("Google", r), ("Cloudflare", r)],
        resolve_host=lambda h: "203.0.113.7",
        sleep=lambda s: None,
        compose_local=lambda: b"name: tico\n",
        **kw,
    )


class _Exc:
    class EntityAlreadyExistsException(Exception):
        pass

    class NoSuchEntityException(Exception):
        pass

    class InvalidResourceId(Exception):
        pass

    class InvocationDoesNotExist(Exception):
        pass

    class ParameterAlreadyExists(Exception):
        pass


class FakeWaiter:
    def wait(self, **kw):
        pass


class FakeEC2:
    """Only what the deployer calls; records calls and holds a little state so re-runs can be tested."""

    def __init__(self, extra_instances=(), extra_sgs=()):
        self.calls, self.instances, self.sgs, self.eips = [], list(extra_instances), list(extra_sgs), []
        self.volumes = []  # {"VolumeId", "Tags", "Attachments": [{"InstanceId", "DeleteOnTermination"}]}

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

    def describe_volumes(self, Filters):
        (ids,) = [f["Values"] for f in Filters if f["Name"] == "attachment.instance-id"]
        return {"Volumes": [v for v in self.volumes if any(a["InstanceId"] in ids for a in v["Attachments"])]}

    def describe_vpcs(self, Filters):
        return {"Vpcs": [{"VpcId": "vpc-1"}]}

    subnets = [{"SubnetId": "subnet-pub", "DefaultForAz": True}]
    route_tables = [
        {"Associations": [{"Main": True}], "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-1"}]}
    ]

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
        self.eips.append(
            {"AllocationId": "eipalloc-1", "PublicIp": "203.0.113.9", "Tags": kw["TagSpecifications"][0]["Tags"]}
        )
        return self.eips[-1]

    def associate_address(self, **kw):
        self._rec("associate_address", **kw)

    def run_instances(self, **kw):
        self.raw_user_data = kw.get("UserData")
        if isinstance(self.raw_user_data, bytes):  # gzipped (aws.fit_user_data): record what cloud-init runs
            import gzip

            kw = {**kw, "UserData": gzip.decompress(self.raw_user_data).decode()}
        self._rec("run_instances", **kw)
        self.instances.append({"InstanceId": "i-1", "Tags": kw["TagSpecifications"][0]["Tags"]})
        vol = [t["Tags"] for t in kw["TagSpecifications"] if t["ResourceType"] == "volume"]
        attached = [{"InstanceId": "i-1", "DeleteOnTermination": True}]
        self.volumes.append({"VolumeId": "vol-1", "Tags": vol[0] if vol else [], "Attachments": attached})
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
        # role_tags: every role exists with these tags (None: none until made), and so does its profile; roles and
        # profiles: the ones made here or set by a test (a role as its tags, or {"RoleId", "Tags"}); gone: deleted.
        self.calls, self.role_tags, self.profiles, self.roles, self.gone = [], role_tags, {}, {}, set()

    def _rec(self, name, **kw):
        self.calls.append((name, kw))

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]

    def _role(self, name) -> dict:
        r = self.roles.get(name)
        if r is not None:
            return r if isinstance(r, dict) else {"RoleId": f"AROA{name}", "Tags": r}
        if self.role_tags is None or name in self.gone:
            raise _Exc.NoSuchEntityException()
        return {"RoleId": f"AROA{name}", "Tags": self.role_tags}

    def list_role_tags(self, RoleName):
        return {"Tags": self._role(RoleName)["Tags"]}

    def get_role(self, RoleName):
        tags = self.list_role_tags(RoleName)["Tags"]  # first: a test's own list_role_tags decides existence
        try:
            rid = self._role(RoleName)["RoleId"]
        except _Exc.NoSuchEntityException:
            rid = f"AROA{RoleName}"
        return {"Role": {"RoleName": RoleName, "RoleId": rid, "Tags": tags}}

    def create_role(self, **kw):
        self._rec("create_role", **kw)
        try:
            self._role(kw["RoleName"])
        except _Exc.NoSuchEntityException:
            self.gone.discard(kw["RoleName"])
            self.roles[kw["RoleName"]] = {"RoleId": f"AROANEW{kw['RoleName']}", "Tags": kw.get("Tags", [])}
            return {"Role": {"RoleName": kw["RoleName"], "RoleId": self.roles[kw["RoleName"]]["RoleId"]}}
        raise _Exc.EntityAlreadyExistsException()

    def delete_role(self, **kw):
        self._rec("delete_role", **kw)
        self.roles.pop(kw["RoleName"], None)
        self.gone.add(kw["RoleName"])

    def create_instance_profile(self, **kw):
        self._rec("create_instance_profile", **kw)
        if kw["InstanceProfileName"] in self.profiles:
            raise _Exc.EntityAlreadyExistsException()
        self.profiles[kw["InstanceProfileName"]] = {"Tags": kw.get("Tags", []), "Roles": []}
        return {"InstanceProfile": {"InstanceProfileName": kw["InstanceProfileName"], "InstanceProfileId": "AIPANEW"}}

    def add_role_to_instance_profile(self, **kw):
        self._rec("add_role_to_instance_profile", **kw)
        self.profiles.setdefault(kw["InstanceProfileName"], {"Tags": [], "Roles": []})["Roles"].append(kw["RoleName"])

    def remove_role_from_instance_profile(self, **kw):
        self._rec("remove_role_from_instance_profile", **kw)
        if kw["InstanceProfileName"] in self.profiles:
            self.profiles[kw["InstanceProfileName"]]["Roles"].remove(kw["RoleName"])

    def delete_instance_profile(self, **kw):
        self._rec("delete_instance_profile", **kw)
        self.profiles.pop(kw["InstanceProfileName"], None)

    def get_instance_profile(self, InstanceProfileName):
        p = self.profiles.get(InstanceProfileName)
        if p is None:  # a role that exists, set up by setup, has its profile beside it
            p = {"Tags": self.list_role_tags(RoleName=InstanceProfileName)["Tags"], "Roles": [InstanceProfileName]}
        roles = [{"RoleName": r} for r in p["Roles"]]
        pid = p.get("InstanceProfileId", f"AIPA{InstanceProfileName}")
        return {
            "InstanceProfile": {
                "InstanceProfileName": InstanceProfileName,
                "InstanceProfileId": pid,
                "Tags": p["Tags"],
                "Roles": roles,
            }
        }

    def __getattr__(self, name):
        return lambda **kw: self._rec(name, **kw)


class FakeSSM:
    exceptions = _Exc

    def __init__(self, param_tags=None, ready=True):
        # param_tags: every parameter exists with these tags (None: none exists until put); params: the ones put here.
        self.calls, self.param_tags, self.ready = [], param_tags, ready
        self.commands, self.params = [], {}

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]

    def get_parameter(self, Name):
        return {"Parameter": {"Value": "ami-123"}}

    def put_parameter(self, **kw):
        self.calls.append(("put_parameter", kw))
        if kw["Name"] in self.params and not kw.get("Overwrite"):
            raise _Exc.ParameterAlreadyExists()
        self.params[kw["Name"]] = kw.get("Tags") or self.params.get(kw["Name"], [])

    def add_tags_to_resource(self, **kw):
        self.calls.append(("add_tags_to_resource", kw))
        self.params[kw["ResourceId"]] = kw["Tags"]

    def delete_parameter(self, **kw):
        self.calls.append(("delete_parameter", kw))
        self.params.pop(kw["Name"], None)

    def list_tags_for_resource(self, **kw):
        if kw.get("ResourceId") in self.params:
            return {"TagList": self.params[kw["ResourceId"]]}
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
        return {
            "ResourceRecordSets": [
                r for r in rows if (r["Name"].rstrip("."), r["Type"]) >= (StartRecordName, StartRecordType)
            ]
        }

    def change_resource_record_sets(self, HostedZoneId, ChangeBatch):
        """As Route 53 does: the whole batch or nothing. CREATE of an existing set, DELETE of a missing or changed one
        (the keys given must match), and a CNAME sharing its name with another type are refused."""
        self.calls.append(("change_resource_record_sets", {"HostedZoneId": HostedZoneId, "ChangeBatch": ChangeBatch}))
        key = lambda r: (r["Name"].rstrip(".").lower(), r["Type"])  # noqa: E731
        after = list(self.records)
        for c in ChangeBatch["Changes"]:
            rs, at = c["ResourceRecordSet"], [r for r in after if key(r) == key(c["ResourceRecordSet"])]
            if c["Action"] == "CREATE" and at:
                raise InvalidChangeBatch(f"Tried to create resource record set {key(rs)} but it already exists")
            if c["Action"] == "DELETE" and (not at or any(at[0].get(k) != v for k, v in rs.items() if k != "Name")):
                raise InvalidChangeBatch(f"Tried to delete resource record set {key(rs)} but it was not found")
            after = [r for r in after if key(r) != key(rs)]
            if c["Action"] in ("CREATE", "UPSERT"):
                after.append(rs)
        for r in after:
            if r["Type"] == "CNAME" and any(o is not r and key(o)[0] == key(r)[0] for o in after):
                raise InvalidChangeBatch(f"RRSet of type CNAME with DNS name {key(r)[0]} is not permitted")
        self.records = after


class InvalidChangeBatch(Exception):
    pass
