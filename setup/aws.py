"""Create (and destroy) the one EC2 server. boto3 is imported only when the AWS target is used.

Everything gets two tags, MANAGED_TAG and NAME_TAG, and destroy only touches what carries both.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import contract, remote

MANAGED_TAG = ("ManagedBy", "tico-setup")
NAME_TAG = "tico-setup-name"
VOLUME_GIB = 30

AMI_PARAMS = {
    "ubuntu": "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id",
    "al2023": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64",
}
# us-east-1 on-demand list prices in USD/hour, approximate; the wizard says so.
HOURLY = {"t4g.small": 0.0168, "t4g.medium": 0.0336, "t4g.large": 0.0672,
          "m8i-flex.xlarge": 0.2011, "m8i-flex.2xlarge": 0.4022}
MEMORY_GIB = {"t4g.small": 2, "t4g.medium": 4, "t4g.large": 8,
              "m8i-flex.xlarge": 16, "m8i-flex.2xlarge": 32}
EBS_GB_MONTH, PUBLIC_IPV4_HOUR, HOURS = 0.08, 0.005, 730


# --name: what slug() makes, so tico-<name> fits every AWS name limit (IAM roles allow 64).
NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
POLICY_ARN_RE = re.compile(r"arn:aws[a-z-]*:iam::\d{12}:policy/[A-Za-z0-9+=,.@/_-]{1,512}")
SSM_CORE = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"


def slug(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:40]


@dataclass
class AwsPlan:
    name: str
    region: str
    instance_type: str
    os_family: str
    front_door: str
    domain: str
    ports: list[int] = field(default_factory=list)
    elastic_ip: bool = False
    role: str = "server"
    warnings: list[str] = field(default_factory=list)
    boundary: str = ""      # the permissions boundary the IAM role is created under, when the account requires one

    @property
    def marker(self) -> str:
        return (contract.REMOTE_DIR if self.role == "server" else contract.RUNNER_DIR) + "/.bootstrapped"

    @property
    def monthly_usd(self) -> float:
        ipv4 = PUBLIC_IPV4_HOUR * HOURS  # every public IPv4 is billed, elastic or not
        return round(HOURLY[self.instance_type] * HOURS + VOLUME_GIB * EBS_GB_MONTH + ipv4, 2)

    def lines(self) -> list[str]:
        sg = ("inbound TCP " + ", ".join(map(str, self.ports)) + " (and UDP 443) from anywhere") if self.ports \
            else "no inbound rules at all"
        return [
            f"EC2 {self.instance_type} ({MEMORY_GIB[self.instance_type]} GiB), {'Ubuntu 24.04' if self.os_family == 'ubuntu' else 'Amazon Linux 2023'} {'x86_64' if self.instance_type.startswith('m8i-') else 'arm64'}, "
            f"{VOLUME_GIB} GiB encrypted gp3, region {self.region}",
            f"Security group tico-{self.name}: {sg}",
            "Instance metadata: IMDSv2 required, hop limit 1 (containers and bots cannot reach the instance role)",
            "Shell access through SSM (IAM role tico-%s with AmazonSSMManagedInstanceCore); no SSH key, no port 22" % self.name,
            *([f"The IAM role is created under the permissions boundary {self.boundary}"] if self.boundary else []),
            f"SSM SecureString /tico/{self.name}/env holds the " + ("one-time join code" if self.role == "runner" else ".env") + " for the instance to fetch at boot",
            ("Elastic IP so the DNS record survives a stop/start" if self.elastic_ip else "No Elastic IP: nothing connects in"),
            f"Tags on everything: {MANAGED_TAG[0]}={MANAGED_TAG[1]}, {NAME_TAG}={self.name}",
            f"Estimated cost: about ${self.monthly_usd:.0f}/month running 24/7 (list prices, us-east-1; other regions differ)",
        ]


def plan_aws(*, domain: str, front_door: str, region: str, instance_type: str = "t4g.small",
             os_family: str = "ubuntu", name: str = "", role: str = "server", boundary: str = "") -> AwsPlan:
    if instance_type not in HOURLY:
        raise ValueError(f"instance type must be one of {', '.join(HOURLY)}")
    if os_family not in AMI_PARAMS:
        raise ValueError("os must be ubuntu or al2023")
    p = AwsPlan(name or slug(domain), region, instance_type, os_family, front_door, domain,
                ports=[80, 443] if front_door == "caddy" else [], elastic_ip=front_door == "caddy", role=role, boundary=boundary)
    if role == "runner":
        p.warnings.append(f"Plan on about 0.5-1 GiB of RAM per bot working at the same time: {instance_type} "
                          f"({MEMORY_GIB[instance_type]} GiB) suits roughly {max(1, MEMORY_GIB[instance_type] - 1)} at once.")
    return p


def tags(plan_name: str, resource_name: str = "") -> list[dict]:
    t = [{"Key": MANAGED_TAG[0], "Value": MANAGED_TAG[1]}, {"Key": NAME_TAG, "Value": plan_name}]
    return t + ([{"Key": "Name", "Value": resource_name}] if resource_name else [])


def is_ours(resource_tags: list[dict] | None, name: str) -> bool:
    """The destroy filter: both tags, exact values. A same-named resource without them is never touched."""
    d = {t["Key"]: t["Value"] for t in resource_tags or []}
    return d.get(MANAGED_TAG[0]) == MANAGED_TAG[1] and d.get(NAME_TAG) == name


def make_clients(region: str, profile: str = ""):
    try:
        import boto3
    except ImportError:
        raise SystemExit("The AWS target needs boto3: pip install boto3") from None
    s = boto3.Session(region_name=region, profile_name=profile or None)
    return s.client("ec2"), s.client("iam"), s.client("ssm"), s.client("sts").get_caller_identity()["Account"]


def make_s3(region: str, profile: str = ""):
    import boto3
    return boto3.Session(region_name=region, profile_name=profile or None).client("s3")


def _filters(name: str) -> list[dict]:
    return [{"Name": f"tag:{MANAGED_TAG[0]}", "Values": [MANAGED_TAG[1]]}, {"Name": f"tag:{NAME_TAG}", "Values": [name]}]


class Deployer:
    def __init__(self, plan: AwsPlan, ec2, iam, ssm, account_id: str, say: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep):
        self.p, self.ec2, self.iam, self.ssm, self.say, self.sleep = plan, ec2, iam, ssm, say, sleep
        self.account_id = account_id

    @property
    def param_name(self) -> str:
        return f"/tico/{self.p.name}/env"

    def find_instance(self) -> dict | None:
        r = self.ec2.describe_instances(Filters=_filters(self.p.name) + [
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
        found = [i for res in r["Reservations"] for i in res["Instances"] if is_ours(i.get("Tags"), self.p.name)]
        return found[0] if found else None

    def put_env(self, env_text: str, *, company_file=False) -> None:
        # The normalized team stays with the encrypted env, under the existing
        # instance role. Explicit Advanced avoids an attempted downgrade on retry.
        tier = {"Tier": "Advanced"} if company_file else {}
        if company_file and len(env_text.encode()) > 8192:
            raise SystemExit("The starter plan and install settings exceed AWS Parameter Store's 8 KB limit")
        self.ssm.put_parameter(Name=self.param_name, Value=env_text, Type="SecureString", Overwrite=True, **tier)
        self.ssm.add_tags_to_resource(ResourceType="Parameter", ResourceId=self.param_name, Tags=tags(self.p.name))

    def ensure_role(self) -> str:
        n = f"tico-{self.p.name}"
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                                                         "Action": "sts:AssumeRole"}]}
        # The tags and the boundary go in the CreateRole call itself: an account that only lets setup create roles under a
        # boundary, tagged ManagedBy=tico-setup, refuses a role that would get them afterwards.
        boundary = {"PermissionsBoundary": self.p.boundary} if self.p.boundary else {}
        try:
            self.iam.create_role(RoleName=n, AssumeRolePolicyDocument=json.dumps(trust), Tags=tags(self.p.name), **boundary)
        except self.iam.exceptions.EntityAlreadyExistsException:
            if not is_ours(self.iam.list_role_tags(RoleName=n)["Tags"], self.p.name):
                raise SystemExit(f"IAM role {n} exists but was not created by tico setup; refusing to reuse it.") from None
        self.iam.attach_role_policy(RoleName=n, PolicyArn=SSM_CORE)
        arn = self.ssm_param_arn()
        pol = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": arn}]}
        self.iam.put_role_policy(RoleName=n, PolicyName="tico-env", PolicyDocument=json.dumps(pol))
        try:
            self.iam.create_instance_profile(InstanceProfileName=n, Tags=tags(self.p.name))
            self.iam.add_role_to_instance_profile(InstanceProfileName=n, RoleName=n)
        except self.iam.exceptions.EntityAlreadyExistsException:
            pass
        return n

    def ssm_param_arn(self) -> str:
        return f"arn:aws:ssm:{self.p.region}:{self.account_id}:parameter{self.param_name}"

    def ensure_sg(self) -> str:
        n = f"tico-{self.p.name}"
        r = self.ec2.describe_security_groups(Filters=_filters(self.p.name))["SecurityGroups"]
        if r:
            return r[0]["GroupId"]
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise SystemExit("This region has no default VPC. Pick another region (--aws-region) or create a default VPC.")
        gid = self.ec2.create_security_group(GroupName=n, Description="Tico server", VpcId=vpcs[0]["VpcId"],
                                             TagSpecifications=[{"ResourceType": "security-group", "Tags": tags(self.p.name, n)}])["GroupId"]
        if self.p.ports:
            perms = [{"IpProtocol": "tcp", "FromPort": p, "ToPort": p, "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                      "Ipv6Ranges": [{"CidrIpv6": "::/0"}]} for p in self.p.ports]
            perms.append({"IpProtocol": "udp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]})
            self.ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=perms)
        return gid

    def ensure_eip(self) -> tuple[str, str]:
        r = self.ec2.describe_addresses(Filters=_filters(self.p.name))["Addresses"]
        if r:
            return r[0]["AllocationId"], r[0]["PublicIp"]
        a = self.ec2.allocate_address(Domain="vpc", TagSpecifications=[{"ResourceType": "elastic-ip", "Tags": tags(self.p.name, f"tico-{self.p.name}")}])
        return a["AllocationId"], a["PublicIp"]

    def public_subnet(self) -> str:
        """A subnet of the default VPC whose route table sends 0.0.0.0/0 to an internet gateway.

        A subnet that routes through a NAT gateway looks healthy from the box but is unreachable from outside,
        so Let's Encrypt cannot validate and nobody can open the site.
        """
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise SystemExit("This region has no default VPC. Pick another region (--aws-region) or create a default VPC.")
        vpc = vpcs[0]["VpcId"]
        flt = [{"Name": "vpc-id", "Values": [vpc]}]
        tables = self.ec2.describe_route_tables(Filters=flt)["RouteTables"]

        def to_igw(t: dict) -> bool:
            return any(r.get("DestinationCidrBlock") == "0.0.0.0/0" and str(r.get("GatewayId", "")).startswith("igw-")
                       for r in t.get("Routes", []))

        main = [t for t in tables if any(a.get("Main") for a in t.get("Associations", []))]
        explicit = {a["SubnetId"]: t for t in tables for a in t.get("Associations", []) if a.get("SubnetId")}
        subnets = self.ec2.describe_subnets(Filters=flt)["Subnets"]
        # A subnet with no explicit association uses the VPC's main route table.
        ok = [s for s in subnets if to_igw(explicit.get(s["SubnetId"]) or (main[0] if main else {}))]
        if not ok:
            raise SystemExit(f"No subnet in VPC {vpc} routes 0.0.0.0/0 to an internet gateway (igw-...), so the server would be "
                             "unreachable from the internet and Let's Encrypt could not issue a certificate. Add such a route to a "
                             "subnet's route table (or create a public subnet), or use --front-door cloudflared, which needs no inbound access.")
        ok.sort(key=lambda s: not s.get("DefaultForAz"))
        return ok[0]["SubnetId"]

    def launch(self, user_data: str, profile: str, sg: str, subnet: str = "") -> str:
        parameter = AMI_PARAMS[self.p.os_family]
        if self.p.instance_type.startswith('m8i-'):
            parameter = parameter.replace('arm64', 'amd64' if self.p.os_family == 'ubuntu' else 'x86_64')
        ami = self.ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
        n = f"tico-{self.p.name}"
        args = dict(
            ImageId=ami, InstanceType=self.p.instance_type, MinCount=1, MaxCount=1, UserData=user_data,
            IamInstanceProfile={"Name": profile}, SecurityGroupIds=[sg],
            MetadataOptions={"HttpTokens": "required", "HttpPutResponseHopLimit": 1, "HttpEndpoint": "enabled"},
            BlockDeviceMappings=[{"DeviceName": "/dev/sda1" if self.p.os_family == "ubuntu" else "/dev/xvda",
                                  "Ebs": {"VolumeSize": VOLUME_GIB, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True}}],
            TagSpecifications=[{"ResourceType": rt, "Tags": tags(self.p.name, n)} for rt in ("instance", "volume", "network-interface")])
        if subnet:
            args["SubnetId"] = subnet
        for attempt in range(8):  # a new instance profile takes a few seconds to be usable
            try:
                return self.ec2.run_instances(**args)["Instances"][0]["InstanceId"]
            except Exception as e:  # botocore ClientError, without importing botocore here
                if not ("Instance Profile" in str(e) or "iamInstanceProfile" in str(e)) or attempt == 7:
                    raise
                self.sleep(5)
        raise AssertionError

    def prepare(self) -> dict:
        """Security group and Elastic IP, so DNS can be set before the server exists."""
        sg = self.ensure_sg()
        alloc, ip = self.ensure_eip() if self.p.elastic_ip else ("", "")
        subnet = self.public_subnet() if self.p.ports else ""  # only a server that takes inbound traffic needs a public subnet
        return {"sg": sg, "alloc": alloc, "public_ip": ip, "subnet": subnet}

    def launch_or_reuse(self, prep: dict, user_data: str) -> tuple[str, bool]:
        """Returns (instance id, created). Running it again reuses what exists."""
        inst = self.find_instance()
        if inst:
            self.say(f"  instance {inst['InstanceId']} already exists; reusing it.")
            iid, created = inst["InstanceId"], False
        else:
            iid, created = self.launch(user_data, self.ensure_role(), prep["sg"], prep.get("subnet", "")), True
            self.say(f"  launched {iid}")
        self.ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
        if prep["alloc"]:
            self.ec2.associate_address(AllocationId=prep["alloc"], InstanceId=iid, AllowReassociation=True)
        return iid, created

    def wait_ready(self, timeout: int = 1200) -> bool:
        """Waits for the SSM agent and the bootstrap script to finish."""
        iid = self.find_instance()["InstanceId"]
        runner = remote.SSMShell(self.ssm, iid, self.sleep)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            info = self.ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [iid]}])
            if info["InstanceInformationList"] and runner.run(f"test -f {self.p.marker}").returncode == 0:
                return True
            self.sleep(15)
        return False


# --- destroy -----------------------------------------------------------------------------------------------

def discover(ec2, iam, ssm, name: str) -> dict[str, list[str]]:
    """Everything tagged for `name`, by kind. Anything without both tags is invisible here."""
    out: dict[str, list[str]] = {}
    r = ec2.describe_instances(Filters=_filters(name) + [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    out["instance"] = [i["InstanceId"] for res in r["Reservations"] for i in res["Instances"] if is_ours(i.get("Tags"), name)]
    out["elastic-ip"] = [a["AllocationId"] for a in ec2.describe_addresses(Filters=_filters(name))["Addresses"] if is_ours(a.get("Tags"), name)]
    out["security-group"] = [g["GroupId"] for g in ec2.describe_security_groups(Filters=_filters(name))["SecurityGroups"] if is_ours(g.get("Tags"), name)]
    n = f"tico-{name}"
    try:
        if is_ours(iam.list_role_tags(RoleName=n)["Tags"], name):
            out["iam-role"] = [n]
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        tg = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=f"/tico/{name}/env")["TagList"]
        if is_ours(tg, name):
            out["ssm-parameter"] = [f"/tico/{name}/env"]
    except ssm.exceptions.InvalidResourceId:
        pass
    return {k: v for k, v in out.items() if v}


def addresses(ec2, name: str) -> set[str]:
    """The public addresses of what is tagged for `name`: its Elastic IPs and its instance's IPv4 and IPv6 addresses. A DNS
    record pointing anywhere else is not this server's."""
    out = {a["PublicIp"] for a in ec2.describe_addresses(Filters=_filters(name))["Addresses"]
           if is_ours(a.get("Tags"), name) and a.get("PublicIp")}
    r = ec2.describe_instances(Filters=_filters(name) + [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    for i in (i for res in r["Reservations"] for i in res["Instances"] if is_ours(i.get("Tags"), name)):
        out |= {i[k] for k in ("PublicIpAddress", "Ipv6Address") if i.get(k)}
        out |= {v["Ipv6Address"] for n in i.get("NetworkInterfaces", []) for v in n.get("Ipv6Addresses", [])}
    return out


def destroy(ec2, iam, ssm, found: dict[str, list[str]], say: Callable[[str], None] = print,
            sleep: Callable[[float], None] = time.sleep, dns_done: bool = False) -> None:
    if found.get("instance"):
        ec2.terminate_instances(InstanceIds=found["instance"])
        say("  waiting for the instance to terminate ...")
        ec2.get_waiter("instance_terminated").wait(InstanceIds=found["instance"])
    for a in found.get("elastic-ip", []):
        ec2.release_address(AllocationId=a)
    for g in found.get("security-group", []):
        for attempt in range(6):  # the network interface lingers briefly after termination
            try:
                ec2.delete_security_group(GroupId=g)
                break
            except Exception:
                if attempt == 5:
                    raise
                sleep(5)
    for n in found.get("iam-role", []):
        try:
            iam.remove_role_from_instance_profile(InstanceProfileName=n, RoleName=n)
            iam.delete_instance_profile(InstanceProfileName=n)
        except iam.exceptions.NoSuchEntityException:
            pass
        iam.detach_role_policy(RoleName=n, PolicyArn=SSM_CORE)
        iam.delete_role_policy(RoleName=n, PolicyName="tico-env")
        iam.delete_role(RoleName=n)
    for p in found.get("ssm-parameter", []):
        ssm.delete_parameter(Name=p)
    say("  done." if dns_done else "  done. DNS records were not touched; remove them at your DNS provider.")
