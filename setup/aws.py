"""Create (and destroy) the one EC2 server. boto3 is imported only when the AWS target is used.

Everything gets two tags, Names.managed_tag and Names.name_tag, and destroy only touches what carries both. Under the
default prefix (tico) this module is all there is. Under another --asset-prefix, setup runs in strict mode (owned.py):
every resource also carries the install's id and is acted on only once proved to be the one this install made.
"""

from __future__ import annotations

import gzip
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from . import contract, remote
from .ui import MissingInput

# The offered Hosting sizes include larger disks on both servers and bot computers.
VOLUME_GIB = {"m8i-flex.xlarge": 100, "m8i-flex.2xlarge": 200}

AMI_PARAMS = {
    "ubuntu": "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id",
    "al2023": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64",
}
# us-east-1 on-demand list prices in USD/hour, approximate; the wizard says so.
HOURLY = {
    "t4g.small": 0.0168,
    "t4g.medium": 0.0336,
    "t4g.large": 0.0672,
    "m8i-flex.xlarge": 0.2011,
    "m8i-flex.2xlarge": 0.4022,
}
MEMORY_GIB = {"t4g.small": 2, "t4g.medium": 4, "t4g.large": 8, "m8i-flex.xlarge": 16, "m8i-flex.2xlarge": 32}
EBS_GB_MONTH, PUBLIC_IPV4_HOUR, HOURS = 0.08, 0.005, 730


# --name: what slug() makes, so <prefix>-<name> fits every AWS name limit (IAM roles allow 64).
NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
REGION_RE = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d{1,2}")
POLICY_ARN_RE = re.compile(r"arn:aws[a-z-]*:iam::\d{12}:policy/[A-Za-z0-9+=,.@/_-]{1,512}")
SSM_CORE = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"


def slug(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")[:40]


DEFAULT_PREFIX = "tico"
# --asset-prefix: up to 10, so <prefix>-backup-<40-character name> still fits IAM's 64 and the backup bucket S3's 63.
PREFIX_RE = re.compile(r"[a-z](?:[a-z0-9-]{0,8}[a-z0-9])?")
# Starts AWS keeps for itself: SSM parameter names (aws, ssm), S3 buckets (xn--, sthree-), security groups (sg-).
PREFIX_RESERVED = re.compile(r"aws|ssm|sthree|sg$|sg-|.*--")


@dataclass(frozen=True)
class Names:
    """Every AWS name, tag and path setup makes, from --asset-prefix (default tico): the one place they are spelled.
    Names on the server itself (/opt/tico, containers, TICO_* settings) are not AWS names and do not change."""

    prefix: str = DEFAULT_PREFIX
    # A new install's random id (new_install_id). Set, a resource is this install's only when it carries it too: two
    # installs of the same prefix and name, in any region or account, never take each other's. Empty (an install saved
    # before it had one): the two tags alone, as before.
    install_id: str = ""

    @property
    def install_tag(self) -> str:
        return f"{self.prefix}-setup-install"

    @property
    def managed_tag(self) -> tuple[str, str]:
        return ("ManagedBy", f"{self.prefix}-setup")

    @property
    def name_tag(self) -> str:
        return f"{self.prefix}-setup-name"

    def resource(self, name: str) -> str:
        """The instance, security group, Elastic IP, role and instance profile, and their Name tags."""
        return f"{self.prefix}-{name}"

    def param(self, name: str) -> str:
        """The SecureString the instance reads its settings from at boot (remote.bootstrap_script's pull-env.sh)."""
        return f"/{self.prefix}/{name}/env"

    @property
    def role_policy(self) -> str:
        return f"{self.prefix}-env"

    @property
    def sg_description(self) -> str:
        return "Tico server" if self.prefix == DEFAULT_PREFIX else "Server"

    def backup_bucket(self, domain: str, account_id: str) -> str:
        """Bucket names are global; the account id keeps two companies' names from colliding. Under another prefix a new
        install's ends in its id instead: no one else holds that name, so CreateBucket never meets an existing bucket."""
        if self.install_id and self.prefix != DEFAULT_PREFIX:
            return f"{self.prefix}-backup-{slug(domain)[:28].rstrip('-')}-{self.install_id}"
        return f"{self.prefix}-backup-{slug(domain)[:30]}-{account_id}"[:63].rstrip("-")

    def backup_user(self, name: str) -> str:
        return f"{self.prefix}-backup-{name}"[:64]

    @property
    def backup_policy(self) -> str:
        return f"{self.prefix}-backup"

    @property
    def backup_key_prefix(self) -> str:
        """The folder in the backup bucket the server writes to (TICO_BACKUP_URL=s3://<bucket>/<this>)."""
        return self.prefix

    @property
    def backup_rule(self) -> str:
        return f"{self.prefix}-backups"

    def plan_bucket(self, token: str) -> str:
        return f"{self.prefix}-setup-plans-{token}"

    @property
    def dns_comment(self) -> str:
        return f"{self.prefix} setup"

    def tags(self, plan_name: str, resource_name: str = "") -> list[dict]:
        t = [{"Key": self.managed_tag[0], "Value": self.managed_tag[1]}, {"Key": self.name_tag, "Value": plan_name}]
        t += [{"Key": self.install_tag, "Value": self.install_id}] if self.install_id else []
        return t + ([{"Key": "Name", "Value": resource_name}] if resource_name else [])

    def is_ours(self, resource_tags: list[dict] | None, name: str) -> bool:
        """The destroy filter: both tags, exact values, and this install's id when it has one. A same-named resource
        without them is never touched."""
        d = {t["Key"]: t["Value"] for t in resource_tags or []}
        if self.install_id and d.get(self.install_tag) != self.install_id:
            return False
        return d.get(self.managed_tag[0]) == self.managed_tag[1] and d.get(self.name_tag) == name

    def filters(self, name: str) -> list[dict]:
        return [
            {"Name": f"tag:{self.managed_tag[0]}", "Values": [self.managed_tag[1]]},
            {"Name": f"tag:{self.name_tag}", "Values": [name]},
        ]


DEFAULT_NAMES = Names()
# The default prefix's tags, as they were spelled before Names.
MANAGED_TAG = DEFAULT_NAMES.managed_tag
NAME_TAG = DEFAULT_NAMES.name_tag
tags = DEFAULT_NAMES.tags
is_ours = DEFAULT_NAMES.is_ours
_filters = DEFAULT_NAMES.filters


def new_install_id() -> str:
    return secrets.token_hex(8)


def asset_prefix(given: str | None, saved: dict | None, read_only: bool = False, what: str = "") -> str:
    """--asset-prefix on the first run, then the one saved with the install: its resources are found by their names, so
    a different one later is refused rather than applied. Saved state from before the flag means tico. An install saved
    under another prefix than tico (strict mode) is acted on only when the command names that prefix again: a command
    that changes AWS resources without it is refused before any AWS call (`read_only`: doctor, which changes none)."""
    given = (given or "").strip()
    if given and not PREFIX_RE.fullmatch(given):
        raise MissingInput(
            "--asset-prefix is 1 to 10 lowercase letters, digits and dashes, starting with a letter and not ending "
            "with a dash"
        )
    if given and PREFIX_RESERVED.match(given):
        raise MissingInput(
            f"--asset-prefix {given} would make names AWS refuses: it cannot start with aws, ssm, sthree or sg-, be sg, or "
            "have two dashes in a row"
        )
    if not saved:
        return given or DEFAULT_PREFIX
    kept = saved.get("asset_prefix") or DEFAULT_PREFIX
    if kept != DEFAULT_PREFIX and given != kept and (given or not read_only):
        what = what or saved.get("domain") or saved.get("server_url") or "This install"
        raise MissingInput(
            f"{what} was set up with --asset-prefix {kept}, and every command that changes its AWS resources must name "
            f"it; refusing to go on {'under --asset-prefix ' + given if given else 'without it'}, and nothing was "
            f"changed. Run the command again with --asset-prefix {kept}."
        )
    if given and given != kept:
        raise MissingInput(
            f"This install's AWS resources are named with --asset-prefix {kept}, and it cannot change on a later run. "
            f"Leave --asset-prefix out, or pass --asset-prefix {kept}."
        )
    return kept


def saved_name(saved: dict) -> str:
    """The --name an install's saved state was set up with. Before --name was saved, an AWS install's came from its
    domain."""
    if saved.get("aws_name"):
        return saved["aws_name"]
    return slug(saved["domain"]) if saved.get("aws_instance_id") and saved.get("domain") else ""


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
    boundary: str = ""  # the permissions boundary the IAM role is created under, when the account requires one
    plan_bucket: str = ""  # --plan-bucket: the role may read its own starter plan there, plans/<name>/*
    prefix: str = DEFAULT_PREFIX  # --asset-prefix
    install_id: str = ""  # Names.install_id (strict mode only)

    @property
    def names(self) -> Names:
        return Names(self.prefix, self.install_id)

    @property
    def volume_gib(self) -> int:
        return VOLUME_GIB.get(self.instance_type, 30)

    @property
    def marker(self) -> str:
        return (contract.REMOTE_DIR if self.role == "server" else contract.RUNNER_DIR) + "/.bootstrapped"

    @property
    def monthly_usd(self) -> float:
        ipv4 = PUBLIC_IPV4_HOUR * HOURS  # every public IPv4 is billed, elastic or not
        return round(HOURLY[self.instance_type] * HOURS + self.volume_gib * EBS_GB_MONTH + ipv4, 2)

    def lines(self) -> list[str]:
        n = self.names
        sg = (
            ("inbound TCP " + ", ".join(map(str, self.ports)) + " (and UDP 443) from anywhere")
            if self.ports
            else "no inbound rules at all"
        )
        return [
            f"EC2 {self.instance_type} ({MEMORY_GIB[self.instance_type]} GiB), {'Ubuntu 24.04' if self.os_family == 'ubuntu' else 'Amazon Linux 2023'} {'x86_64' if self.instance_type.startswith('m8i-') else 'arm64'}, "
            f"{self.volume_gib} GiB encrypted gp3, region {self.region}",
            f"Security group {n.resource(self.name)}: {sg}",
            "Instance metadata: IMDSv2 required, hop limit 1 (containers and bots cannot reach the instance role)",
            "Shell access through SSM (IAM role %s with AmazonSSMManagedInstanceCore); no SSH key, no port 22"
            % n.resource(self.name),
            *([f"The IAM role is created under the permissions boundary {self.boundary}"] if self.boundary else []),
            *(
                [
                    f"The starter plan goes to the server by S3 bucket {self.plan_bucket} (plans/{self.name}/, read by the "
                    "server's role once at first boot, deleted once it is up)"
                ]
                if self.plan_bucket
                else []
            ),
            f"SSM SecureString {n.param(self.name)} holds the "
            + ("one-time join code" if self.role == "runner" else ".env")
            + " for the instance to fetch at boot",
            (
                "Elastic IP so the DNS record survives a stop/start"
                if self.elastic_ip
                else "No Elastic IP: nothing connects in"
            ),
            f"Tags on everything: {n.managed_tag[0]}={n.managed_tag[1]}, {n.name_tag}={self.name}"
            + (f", {n.install_tag}={self.install_id}" if self.install_id else ""),
            f"Estimated cost: about ${self.monthly_usd:.0f}/month running 24/7 (list prices, us-east-1; other regions differ)",
        ]


def plan_aws(
    *,
    domain: str,
    front_door: str,
    region: str,
    instance_type: str = "t4g.small",
    os_family: str = "ubuntu",
    name: str = "",
    role: str = "server",
    boundary: str = "",
    plan_bucket: str = "",
    prefix: str = DEFAULT_PREFIX,
    install_id: str = "",
) -> AwsPlan:
    if instance_type not in HOURLY:
        raise ValueError(f"instance type must be one of {', '.join(HOURLY)}")
    if os_family not in AMI_PARAMS:
        raise ValueError("os must be ubuntu or al2023")
    p = AwsPlan(
        name or slug(domain),
        region,
        instance_type,
        os_family,
        front_door,
        domain,
        ports=[80, 443] if front_door == "caddy" else [],
        elastic_ip=front_door == "caddy",
        role=role,
        boundary=boundary,
        plan_bucket=plan_bucket,
        prefix=prefix,
        install_id=install_id,
    )
    if role == "runner":
        p.warnings.append(
            f"Plan on about 0.5-1 GiB of RAM per bot working at the same time: {instance_type} "
            f"({MEMORY_GIB[instance_type]} GiB) suits roughly {max(1, MEMORY_GIB[instance_type] - 1)} at once."
        )
    return p


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


USER_DATA_LIMIT = 16384  # EC2's cap on user data, before base64


def fit_user_data(script: str) -> str | bytes:
    """The boot script as EC2 user data. Over EC2's 16 KB cap (the inline compose file and visual bundle alone pass it)
    it goes gzipped: cloud-init unpacks gzip user data before running it, and boto3 base64-encodes bytes as-is."""
    raw = script.encode()
    if len(raw) <= USER_DATA_LIMIT:
        return script
    packed = gzip.compress(raw, mtime=0)
    if len(packed) > USER_DATA_LIMIT:
        raise SystemExit(f"The server's boot script is {len(packed)} bytes even gzipped; EC2 allows {USER_DATA_LIMIT}.")
    return packed


class Deployer:
    def __init__(
        self,
        plan: AwsPlan,
        ec2,
        iam,
        ssm,
        account_id: str,
        say: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.p, self.ec2, self.iam, self.ssm, self.say, self.sleep = plan, ec2, iam, ssm, say, sleep
        self.account_id = account_id
        self.n = plan.names

    @property
    def param_name(self) -> str:
        return self.n.param(self.p.name)

    def find_instance(self) -> dict | None:
        r = self.ec2.describe_instances(
            Filters=self.n.filters(self.p.name)
            + [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
        )
        found = [i for res in r["Reservations"] for i in res["Instances"] if self.n.is_ours(i.get("Tags"), self.p.name)]
        return found[0] if found else None

    def put_env(self, env_text: str, *, company_file=False) -> None:
        # The normalized team stays with the encrypted env, under the existing
        # instance role. Explicit Advanced avoids an attempted downgrade on retry.
        tier = {"Tier": "Advanced"} if company_file else {}
        if company_file and len(env_text.encode()) > 8192:
            from backend.company_file import starter

            raise SystemExit(
                "The starter plan and install settings exceed AWS Parameter Store's 8 KB limit. "
                + starter.IMPORT_IN_APP
            )
        self.ssm.put_parameter(Name=self.param_name, Value=env_text, Type="SecureString", Overwrite=True, **tier)
        self.ssm.add_tags_to_resource(
            ResourceType="Parameter", ResourceId=self.param_name, Tags=self.n.tags(self.p.name)
        )

    def ensure_plan_bucket(self, s3, region: str) -> None:
        """Configure only setup's dedicated bucket, in the installer's account. Never adopt an unrelated bucket.
        Every safety setting precedes the first chart upload. Re-running repairs an interrupted configuration.
        """
        bucket = self.p.plan_bucket
        owned = {"Bucket": bucket, "ExpectedBucketOwner": self.account_id}
        try:
            s3.head_bucket(**owned)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code not in ("404", "NoSuchBucket", "NotFound"):
                raise
            location = {} if region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": region}}
            # A collision is an error, never permission to reuse someone else's name.
            s3.create_bucket(Bucket=bucket, **location)
            s3.put_bucket_tagging(**owned, Tagging={"TagSet": self.n.tags(self.p.name)})
        else:
            if not self.n.is_ours(s3.get_bucket_tagging(**owned).get("TagSet"), self.p.name):
                raise SystemExit("The starter plan bucket was not created by this setup; refusing to reuse it.")
        s3.put_public_access_block(
            **owned,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_ownership_controls(
            **owned, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
        )
        s3.put_bucket_encryption(
            **owned,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
        s3.put_bucket_lifecycle_configuration(
            **owned,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "expire-starter-plans",
                        "Status": "Enabled",
                        "Filter": {"Prefix": f"plans/{self.p.name}/"},
                        "Expiration": {"Days": 1},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                    }
                ]
            },
        )

    def put_plan(self, s3, key: str, data: str) -> None:
        """The packed starter plan, for the server to copy once at first boot (remote.bootstrap_script). Plan only:
        the env names the key and its sha256, never the plan."""
        if not key.startswith(f"plans/{self.p.name}/"):
            raise ValueError("a starter plan key is plans/<this server's name>/<sha256>")
        s3.put_object(Bucket=self.p.plan_bucket, Key=key, Body=data.encode("ascii"), ContentType="text/plain")

    def delete_plan(self, s3, key: str) -> str:
        """Once the server is up and its app reads its copy. Returns "" when deleted, else the error code: the server is
        already working, and the plan stays until someone deletes it or the bucket's lifecycle rule, if it has one,
        expires it."""
        try:
            s3.delete_object(Bucket=self.p.plan_bucket, Key=key)
        except Exception as e:  # botocore ClientError, a network error; without importing botocore here
            return str(getattr(e, "response", {}).get("Error", {}).get("Code") or type(e).__name__)
        return ""

    def ensure_role(self) -> str:
        n = self.n.resource(self.p.name)
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}
            ],
        }
        # The tags and the boundary go in the CreateRole call itself: an account that only lets setup create roles under a
        # boundary, tagged ManagedBy=<prefix>-setup, refuses a role that would get them afterwards.
        boundary = {"PermissionsBoundary": self.p.boundary} if self.p.boundary else {}
        try:
            self.iam.create_role(
                RoleName=n, AssumeRolePolicyDocument=json.dumps(trust), Tags=self.n.tags(self.p.name), **boundary
            )
        except self.iam.exceptions.EntityAlreadyExistsException:
            if not self.n.is_ours(self.iam.list_role_tags(RoleName=n)["Tags"], self.p.name):
                raise SystemExit(
                    f"IAM role {n} exists but was not created by tico setup; refusing to reuse it."
                ) from None
        self.iam.attach_role_policy(RoleName=n, PolicyArn=SSM_CORE)
        arn = self.ssm_param_arn()
        pol = {
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": arn}],
        }
        if self.p.plan_bucket:
            # Its own plan only; a boundary, when there is one, only limits this and grants nothing.
            pol["Statement"].append(
                {
                    "Effect": "Allow",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{self.p.plan_bucket}/plans/{self.p.name}/*",
                }
            )
        self.iam.put_role_policy(RoleName=n, PolicyName=self.n.role_policy, PolicyDocument=json.dumps(pol))
        try:
            self.iam.create_instance_profile(InstanceProfileName=n, Tags=self.n.tags(self.p.name))
            self.iam.add_role_to_instance_profile(InstanceProfileName=n, RoleName=n)
        except self.iam.exceptions.EntityAlreadyExistsException:
            pass
        return n

    def ssm_param_arn(self) -> str:
        return f"arn:aws:ssm:{self.p.region}:{self.account_id}:parameter{self.param_name}"

    def ensure_sg(self) -> str:
        n = self.n.resource(self.p.name)
        r = self.ec2.describe_security_groups(Filters=self.n.filters(self.p.name))["SecurityGroups"]
        if r:
            return r[0]["GroupId"]
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise SystemExit(
                "This region has no default VPC. Pick another region (--aws-region) or create a default VPC."
            )
        gid = self.ec2.create_security_group(
            GroupName=n,
            Description=self.n.sg_description,
            VpcId=vpcs[0]["VpcId"],
            TagSpecifications=[{"ResourceType": "security-group", "Tags": self.n.tags(self.p.name, n)}],
        )["GroupId"]
        if self.p.ports:
            perms = [
                {
                    "IpProtocol": "tcp",
                    "FromPort": p,
                    "ToPort": p,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                    "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
                }
                for p in self.p.ports
            ]
            perms.append({"IpProtocol": "udp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]})
            self.ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=perms)
        return gid

    def ensure_eip(self) -> tuple[str, str]:
        r = self.ec2.describe_addresses(Filters=self.n.filters(self.p.name))["Addresses"]
        if r:
            return r[0]["AllocationId"], r[0]["PublicIp"]
        a = self.ec2.allocate_address(
            Domain="vpc",
            TagSpecifications=[
                {"ResourceType": "elastic-ip", "Tags": self.n.tags(self.p.name, self.n.resource(self.p.name))}
            ],
        )
        return a["AllocationId"], a["PublicIp"]

    def public_subnet(self) -> str:
        """A subnet of the default VPC whose route table sends 0.0.0.0/0 to an internet gateway.

        A subnet that routes through a NAT gateway looks healthy from the box but is unreachable from outside,
        so Let's Encrypt cannot validate and nobody can open the site.
        """
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise SystemExit(
                "This region has no default VPC. Pick another region (--aws-region) or create a default VPC."
            )
        vpc = vpcs[0]["VpcId"]
        flt = [{"Name": "vpc-id", "Values": [vpc]}]
        tables = self.ec2.describe_route_tables(Filters=flt)["RouteTables"]

        def to_igw(t: dict) -> bool:
            return any(
                r.get("DestinationCidrBlock") == "0.0.0.0/0" and str(r.get("GatewayId", "")).startswith("igw-")
                for r in t.get("Routes", [])
            )

        main = [t for t in tables if any(a.get("Main") for a in t.get("Associations", []))]
        explicit = {a["SubnetId"]: t for t in tables for a in t.get("Associations", []) if a.get("SubnetId")}
        subnets = self.ec2.describe_subnets(Filters=flt)["Subnets"]
        # A subnet with no explicit association uses the VPC's main route table.
        ok = [s for s in subnets if to_igw(explicit.get(s["SubnetId"]) or (main[0] if main else {}))]
        if not ok:
            raise SystemExit(
                f"No subnet in VPC {vpc} routes 0.0.0.0/0 to an internet gateway (igw-...), so the server would be "
                "unreachable from the internet and Let's Encrypt could not issue a certificate. Add such a route to a "
                "subnet's route table (or create a public subnet), or use --front-door cloudflared, which needs no inbound access."
            )
        ok.sort(key=lambda s: not s.get("DefaultForAz"))
        return ok[0]["SubnetId"]

    def launch(self, user_data: str, profile: str, sg: str, subnet: str = "") -> str:
        parameter = AMI_PARAMS[self.p.os_family]
        if self.p.instance_type.startswith('m8i-'):
            parameter = parameter.replace('arm64', 'amd64' if self.p.os_family == 'ubuntu' else 'x86_64')
        ami = self.ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
        n = self.n.resource(self.p.name)
        args = dict(
            ImageId=ami,
            InstanceType=self.p.instance_type,
            MinCount=1,
            MaxCount=1,
            UserData=fit_user_data(user_data),
            IamInstanceProfile={"Name": profile},
            SecurityGroupIds=[sg],
            MetadataOptions={"HttpTokens": "required", "HttpPutResponseHopLimit": 1, "HttpEndpoint": "enabled"},
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1" if self.p.os_family == "ubuntu" else "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": self.p.volume_gib,
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            TagSpecifications=[
                {"ResourceType": rt, "Tags": self.n.tags(self.p.name, n)}
                for rt in ("instance", "volume", "network-interface")
            ],
        )
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
        subnet = (
            self.public_subnet() if self.p.ports else ""
        )  # only a server that takes inbound traffic needs a public subnet
        return {"sg": sg, "alloc": alloc, "public_ip": ip, "subnet": subnet}

    def launch_or_reuse(self, prep: dict, user_data: str) -> tuple[str, bool]:
        """Returns (instance id, created). Running it again reuses what exists."""
        inst = self.find_instance()
        if inst:
            self.say(f"  instance {inst['InstanceId']} already exists; reusing it.")
            iid, created = inst["InstanceId"], False
            if self.p.plan_bucket:
                self.ensure_role()  # a server set up before --plan-bucket gets the read of its own plan
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
        self.boot_failed = False
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            info = self.ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [iid]}])
            if info["InstanceInformationList"] and runner.run(f"test -f {self.p.marker}").returncode == 0:
                return True
            # A boot that stopped itself (the starter plan's sha256, say) never writes the marker: no need to wait it out.
            # Its log's last tico-setup line says so; a re-run of setup can then finish it (remote.rerun_script).
            if info["InstanceInformationList"]:
                failed = runner.run(f"grep 'tico-setup:' {remote.BOOT_LOG} | tail -n 1 | grep 'tico-setup: FAILED'")
                if failed.returncode == 0:
                    self.say("  " + failed.stdout.strip()[:300])
                    self.boot_failed = True
                    return False
            self.sleep(15)
        return False


# --- destroy -----------------------------------------------------------------------------------------------


def discover(ec2, iam, ssm, name: str, names: Names = DEFAULT_NAMES) -> dict[str, list[str]]:
    """Everything tagged for `name`, by kind. Anything without both tags is invisible here."""
    out: dict[str, list[str]] = {}
    r = ec2.describe_instances(
        Filters=names.filters(name)
        + [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    )
    out["instance"] = [
        i["InstanceId"] for res in r["Reservations"] for i in res["Instances"] if names.is_ours(i.get("Tags"), name)
    ]
    out["elastic-ip"] = [
        a["AllocationId"]
        for a in ec2.describe_addresses(Filters=names.filters(name))["Addresses"]
        if names.is_ours(a.get("Tags"), name)
    ]
    out["security-group"] = [
        g["GroupId"]
        for g in ec2.describe_security_groups(Filters=names.filters(name))["SecurityGroups"]
        if names.is_ours(g.get("Tags"), name)
    ]
    n = names.resource(name)
    try:
        if names.is_ours(iam.list_role_tags(RoleName=n)["Tags"], name):
            out["iam-role"] = [n]
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        tg = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=names.param(name))["TagList"]
        if names.is_ours(tg, name):
            out["ssm-parameter"] = [names.param(name)]
    except ssm.exceptions.InvalidResourceId:
        pass
    return {k: v for k, v in out.items() if v}


def addresses(ec2, name: str, names: Names = DEFAULT_NAMES) -> set[str]:
    """The public addresses of what is tagged for `name`: its Elastic IPs and its instance's IPv4 and IPv6 addresses. A DNS
    record pointing anywhere else is not this server's."""
    out = {
        a["PublicIp"]
        for a in ec2.describe_addresses(Filters=names.filters(name))["Addresses"]
        if names.is_ours(a.get("Tags"), name) and a.get("PublicIp")
    }
    r = ec2.describe_instances(
        Filters=names.filters(name)
        + [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    )
    for i in (i for res in r["Reservations"] for i in res["Instances"] if names.is_ours(i.get("Tags"), name)):
        out |= {i[k] for k in ("PublicIpAddress", "Ipv6Address") if i.get(k)}
        out |= {v["Ipv6Address"] for n in i.get("NetworkInterfaces", []) for v in n.get("Ipv6Addresses", [])}
    return out


def destroy(
    ec2,
    iam,
    ssm,
    found: dict[str, list[str]],
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    dns_done: bool = False,
    names: Names = DEFAULT_NAMES,
) -> None:
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
        iam.delete_role_policy(RoleName=n, PolicyName=names.role_policy)
        iam.delete_role(RoleName=n)
    for p in found.get("ssm-parameter", []):
        ssm.delete_parameter(Name=p)
    say("  done." if dns_done else "  done. DNS records were not touched; remove them at your DNS provider.")
