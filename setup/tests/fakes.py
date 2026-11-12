"""Stand-ins for every outside dependency: no test here touches the network or a cloud."""

from __future__ import annotations

import pytest

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


SERVER_RELEASE = "v0.9.0"


def deps(ns=None, records=None, **kw) -> Deps:
    r = FakeResolver(records, ns or {"example.com": NETLIFY_NS})
    # No git, no network: a checkout on no release tag, and a server that reports the release SERVER_RELEASE.
    kw.setdefault("checkout_version", lambda: "")
    kw.setdefault("server_release", lambda url: SERVER_RELEASE)
    kw.setdefault("latest_release", lambda: pytest.fail("unit tests never ask GitHub for its latest release"))
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
        self.enis = []  # {"NetworkInterfaceId", "TagSet", "Attachment": {"InstanceId", "DeleteOnTermination"}}
        self.hidden = {}  # resource id: how many more listings miss it (hide)

    def _rec(self, name, **kw):
        self.calls.append((name, kw))

    def called(self, name):
        return [kw for n, kw in self.calls if n == name]

    def hide(self, rid: str, listings: int) -> None:
        """EC2 is eventually consistent: `rid` (an instance, address or security group) is missing from the next
        `listings` describe calls made by filter, while a describe naming its id still finds it."""
        self.hidden[rid] = listings

    def listed(self, rows: list[dict], key: str) -> list[dict]:
        """The rows a listing (a describe by filter, no ids) shows: each hidden one is missed, one listing fewer."""
        out = []
        for r in rows:
            if self.hidden.get(r.get(key), 0) > 0:
                self.hidden[r[key]] -= 1
                continue
            out.append(r)
        return out

    def describe_instances(self, Filters=(), InstanceIds=()):
        rows = [i for i in self.instances if not InstanceIds or i["InstanceId"] in InstanceIds]
        return {"Reservations": [{"Instances": rows if InstanceIds else self.listed(rows, "InstanceId")}]}

    def describe_security_groups(self, Filters=(), GroupIds=()):
        if GroupIds:
            return {"SecurityGroups": [g for g in self.sgs if g["GroupId"] in GroupIds]}
        return {"SecurityGroups": self.listed(self.sgs, "GroupId")}

    def describe_addresses(self, Filters=(), AllocationIds=()):
        rows = [a for a in self.eips if not AllocationIds or a["AllocationId"] in AllocationIds]
        return {"Addresses": rows if AllocationIds else self.listed(rows, "AllocationId")}

    def describe_volumes(self, Filters):
        (ids,) = [f["Values"] for f in Filters if f["Name"] == "attachment.instance-id"]
        return {"Volumes": [v for v in self.volumes if any(a["InstanceId"] in ids for a in v["Attachments"])]}

    def describe_network_interfaces(self, Filters):
        (ids,) = [f["Values"] for f in Filters if f["Name"] == "attachment.instance-id"]
        return {"NetworkInterfaces": [n for n in self.enis if n["Attachment"]["InstanceId"] in ids]}

    def describe_vpcs(self, Filters):
        return {"Vpcs": [{"VpcId": "vpc-1"}]}

    subnets = [{"SubnetId": "subnet-pub", "DefaultForAz": True}]
    route_tables = [
        {"Associations": [{"Main": True}], "Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-1"}]}
    ]

    def describe_subnets(self, Filters=(), SubnetIds=()):
        return {"Subnets": [r for r in self.subnets if not SubnetIds or r["SubnetId"] in SubnetIds]}

    def describe_route_tables(self, Filters):
        return {"RouteTables": self.route_tables}

    def create_security_group(self, **kw):
        self._rec("create_security_group", **kw)
        n = next(k for k in range(1, 1000) if not any(g["GroupId"] == f"sg-{k}" for g in self.sgs))
        self.sgs.append(
            {"GroupId": f"sg-{n}", "GroupName": kw["GroupName"], "Tags": kw["TagSpecifications"][0]["Tags"]}
        )
        return {"GroupId": f"sg-{n}"}

    def authorize_security_group_ingress(self, **kw):
        self._rec("authorize_security_group_ingress", **kw)
        for g in self.sgs:  # AWS refuses a rule the group already has (InvalidPermission.Duplicate)
            if g["GroupId"] == kw["GroupId"]:
                g.setdefault("IpPermissions", []).extend(kw["IpPermissions"])

    def allocate_address(self, **kw):
        self._rec("allocate_address", **kw)
        n = len(self.called("allocate_address"))  # each its own id, as EC2's: the first stays eipalloc-1
        self.eips.append(
            {"AllocationId": f"eipalloc-{n}", "PublicIp": "203.0.113.9", "Tags": kw["TagSpecifications"][0]["Tags"]}
        )
        return self.eips[-1]

    def associate_address(self, **kw):
        """As EC2 does for an address in a VPC: one attached elsewhere moves unless AllowReassociation is False (leaving
        it out allows it)."""
        self._rec("associate_address", **kw)
        for a in self.eips:
            if a["AllocationId"] == kw["AllocationId"]:
                if a.get("InstanceId") not in (None, kw["InstanceId"]) and kw.get("AllowReassociation", True) is False:
                    raise RuntimeError("Resource.AlreadyAssociated")
                a["InstanceId"] = kw["InstanceId"]

    def run_instances(self, **kw):
        self.raw_user_data = kw.get("UserData")
        if isinstance(self.raw_user_data, bytes):  # gzipped (aws.fit_user_data): record what cloud-init runs
            import gzip

            kw = {**kw, "UserData": gzip.decompress(self.raw_user_data).decode()}
        self._rec("run_instances", **kw)
        token = kw.get("ClientToken")
        again = [i for i in self.instances if token and i.get("ClientToken") == token]
        if again:  # as EC2 does: a repeated ClientToken answers with the instance it already launched
            return {"Instances": [{"InstanceId": again[0]["InstanceId"]}]}
        root = (kw.get("BlockDeviceMappings") or [{}])[0].get("DeviceName", "/dev/xvda")
        # i-1 for the first launch; a second one (a test of the second server setup must never make) gets i-2 and so on.
        n = next(k for k in range(1, 1000) if not any(i["InstanceId"] == f"i-{k}" for i in self.instances))
        iid, vid, nid = f"i-{n}", f"vol-{n}", f"eni-{n}"
        self.instances.append(
            {
                "InstanceId": iid,
                "Tags": kw["TagSpecifications"][0]["Tags"],
                "RootDeviceName": root,
                "BlockDeviceMappings": [{"DeviceName": root, "Ebs": {"VolumeId": vid}}],
                "NetworkInterfaces": [{"NetworkInterfaceId": nid, "Attachment": {"DeviceIndex": 0}}],
            }
        )
        if token:
            self.instances[-1]["ClientToken"] = token
        vol = [t["Tags"] for t in kw["TagSpecifications"] if t["ResourceType"] == "volume"]
        attached = [{"InstanceId": iid, "DeleteOnTermination": True}]
        self.volumes.append({"VolumeId": vid, "Tags": vol[0] if vol else [], "Attachments": attached})
        eni = [t["Tags"] for t in kw["TagSpecifications"] if t["ResourceType"] == "network-interface"]
        self.enis.append({"NetworkInterfaceId": nid, "TagSet": eni[0] if eni else [], "Attachment": {**attached[0]}})
        return {"Instances": [{"InstanceId": iid}]}

    def get_waiter(self, name):
        return FakeWaiter()

    def terminate_instances(self, **kw):
        """As EC2 does: an address associated with a terminated instance is disassociated from it."""
        self._rec("terminate_instances", **kw)
        for a in self.eips:
            if a.get("InstanceId") in kw["InstanceIds"]:
                for k in ("InstanceId", "AssociationId", "NetworkInterfaceId", "PrivateIpAddress"):
                    a.pop(k, None)

    def release_address(self, **kw):
        self._rec("release_address", **kw)

    def delete_security_group(self, **kw):
        self._rec("delete_security_group", **kw)


class FakeIAM:
    exceptions = _Exc

    def __init__(self, role_tags=None, profile_tags=...):
        # role_tags: every role exists with these tags (None: none until made). profile_tags: every instance profile
        # exists with these tags, holding the role of its name (default: role_tags; None: none until made). roles and
        # profiles: the ones made here or set by a test (a role as its tags, or {"RoleId", "Tags"}); gone: deleted.
        self.calls, self.role_tags, self.profiles, self.roles, self.gone = [], role_tags, {}, {}, set()
        self.profile_tags = role_tags if profile_tags is ... else profile_tags
        # Inline policies: a role made here has one once put; a role a test set up has one unless named in `no_policy`.
        self.policies, self.no_policy = set(), set()

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

    def put_role_policy(self, **kw):
        self._rec("put_role_policy", **kw)
        self.policies.add((kw["RoleName"], kw["PolicyName"]))
        self.no_policy.discard(kw["RoleName"])

    def delete_role_policy(self, **kw):
        """As IAM does: NoSuchEntity for a policy the role does not have."""
        self._rec("delete_role_policy", **kw)
        made_here = (
            isinstance(self.roles.get(kw["RoleName"]), dict) and "AROANEW" in self.roles[kw["RoleName"]]["RoleId"]
        )
        if (kw["RoleName"], kw["PolicyName"]) in self.policies:
            self.policies.discard((kw["RoleName"], kw["PolicyName"]))
        elif made_here or kw["RoleName"] in self.no_policy:
            raise _Exc.NoSuchEntityException()

    def delete_role(self, **kw):
        self._rec("delete_role", **kw)
        self.roles.pop(kw["RoleName"], None)
        self.gone.add(kw["RoleName"])

    def create_instance_profile(self, **kw):
        self._rec("create_instance_profile", **kw)
        try:
            self.get_instance_profile(kw["InstanceProfileName"])
        except _Exc.NoSuchEntityException:
            pass
        else:
            raise _Exc.EntityAlreadyExistsException()
        self.gone.discard(("profile", kw["InstanceProfileName"]))
        pid = f"AIPANEW{kw['InstanceProfileName']}"
        self.profiles[kw["InstanceProfileName"]] = {"Tags": kw.get("Tags", []), "Roles": [], "InstanceProfileId": pid}
        return {"InstanceProfile": {"InstanceProfileName": kw["InstanceProfileName"], "InstanceProfileId": pid}}

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
        self.gone.add(("profile", kw["InstanceProfileName"]))

    def get_instance_profile(self, InstanceProfileName):
        p = self.profiles.get(InstanceProfileName)
        if p is None:  # exists on its own only as profile_tags says; never made up from a role's tags
            if self.profile_tags is None or ("profile", InstanceProfileName) in self.gone:
                raise _Exc.NoSuchEntityException()
            p = {"Tags": self.profile_tags, "Roles": [InstanceProfileName]}
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
            # A DELETE names the whole record set as it is now: TTL and every value (or the alias), not just its name.
            exact = lambda a, b: {**a, "Name": key(a)[0]} == {**b, "Name": key(b)[0]}  # noqa: E731
            if c["Action"] == "DELETE" and (not at or not exact(at[0], rs)):
                raise InvalidChangeBatch(f"Tried to delete resource record set {key(rs)} but it was not found")
            after = [r for r in after if key(r) != key(rs)]
            if c["Action"] in ("CREATE", "UPSERT"):
                after.append(rs)
        for r in after:
            if r["Type"] == "CNAME" and any(o is not r and key(o)[0] == key(r)[0] for o in after):
                raise InvalidChangeBatch(f"RRSet of type CNAME with DNS name {key(r)[0]} is not permitted")
        self.records = after


class InvalidChangeBatch(Exception):
    """As botocore raises it: a ClientError carrying Route 53's 400 answer."""

    def __init__(self, message: str):
        super().__init__(message)
        self.response = {
            "Error": {"Code": "InvalidChangeBatch", "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        }
