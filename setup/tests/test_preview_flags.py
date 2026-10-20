"""The Hosting preview's installer flags (tico-services Dev 268): HQ creates a 7-day preview server with --name pv-<id>
under DevOps's permissions boundary, and on day 7 runs destroy --dns. Fake AWS and Route 53 only."""
import json

import pytest

from setup import aws, cli, dns, route53, verify
from setup.tests import fakes
from setup.tests.fakes import CaptureIO, FakeEC2, FakeIAM, FakeRoute53, FakeSSM, aws_clients

ZONE = "preview.example"
NAME = "pv-abcdefgh"
DOMAIN = f"{NAME}.{ZONE}"
BOUNDARY = "arn:aws:iam::123456789012:policy/tico-preview-boundary"
OURS = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": NAME}]
CREATE = ["--cloud", "aws", "--aws-region", "us-east-2", "--aws-instance-type", "t4g.medium", "--name", NAME,
          "--aws-permissions-boundary", BOUNDARY, "--hosted", "invited", "--hosted-zone", ZONE, "--domain", DOMAIN,
          "--preset", "recommended", "--backup", "local", "--auth", "oidc", "--issuer", "https://accounts.example",
          "--client-id", "preview-client", "--signin-label", "Tico account", "--owner-email", "ana@acme.example",
          "--non-interactive", "--yes"]
DESTROY = ["destroy", "--name", NAME, "--domain", DOMAIN, "--aws-region", "us-east-2", "--dns", "--yes"]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "client-SECRET-0123456789abcdef")
    monkeypatch.setenv("TICO_GATEWAY_INVITE", "inv_SECRET-0123456789")


def go(argv, d):
    io = CaptureIO()
    return cli.main(argv, deps=d, io=io), io.text


def world(ec2=None, r53=None, ip="203.0.113.9"):
    ec2, iam, ssm, factory = aws_clients(ec2=ec2)
    r = fakes.FakeResolver({(DOMAIN, dns.A): [ip]}, {ZONE: fakes.R53_NS})
    d = fakes.deps()
    d.resolver, d.public_resolvers, d.aws_clients = r, (lambda: [("Google", r)]), factory
    d.aws_s3 = lambda region, profile: pytest.fail("--backup local makes no bucket")
    r53 = r53 or FakeRoute53(ZONE)
    d.route53_client = lambda profile: r53
    return d, ec2, iam, ssm, r53


def test_the_preview_create_command_names_everything_after_its_name_under_the_boundary():
    d, ec2, iam, ssm, r53 = world()
    code, out = go(CREATE, d)
    assert code == 0, out
    # One CreateRole call carries the boundary and both tags: DevOps's role refuses one that would get them later.
    (role,) = iam.called("create_role")
    assert role["RoleName"] == f"tico-{NAME}" and role["PermissionsBoundary"] == BOUNDARY and role["Tags"] == OURS
    assert iam.called("attach_role_policy") == [{"RoleName": f"tico-{NAME}", "PolicyArn": aws.SSM_CORE}]
    (inline,) = iam.called("put_role_policy")
    assert inline["RoleName"] == f"tico-{NAME}"
    assert json.loads(inline["PolicyDocument"])["Statement"][0]["Resource"] == f"arn:aws:ssm:us-east-2:123456789012:parameter/tico/{NAME}/env"
    (param,) = ssm.called("put_parameter")
    assert param["Name"] == f"/tico/{NAME}/env" and "TICO_BACKUP_URL" not in param["Value"]
    assert "TICO_INSTALL_PRESET=recommended" in param["Value"]
    (run,) = ec2.called("run_instances")
    assert run["InstanceType"] == "t4g.medium" and run["IamInstanceProfile"] == {"Name": f"tico-{NAME}"}
    # The instance, its disk and its network interface are each tagged in the launch call: DevOps's role refuses a
    # launch whose network interface lacks them (it attaches the address only to a tagged interface).
    specs = {s["ResourceType"]: s["Tags"] for s in run["TagSpecifications"]}
    assert set(specs) == {"instance", "volume", "network-interface"}
    assert all({"Key": "Name", "Value": f"tico-{NAME}"} in t and all(o in t for o in OURS) for t in specs.values())
    assert ec2.called("create_security_group")[0]["GroupName"] == f"tico-{NAME}"
    (change,) = r53.called("change_resource_record_sets")
    assert change["ChangeBatch"]["Changes"][0]["ResourceRecordSet"]["ResourceRecords"] == [{"Value": "203.0.113.9"}]
    assert "client-SECRET" not in out and "inv_SECRET" not in out


def test_without_a_boundary_or_name_the_role_is_as_before():
    ec2, iam, ssm, _ = aws_clients()
    p = aws.plan_aws(domain="tico.example.com", front_door="caddy", region="us-east-1")
    aws.Deployer(p, ec2, iam, ssm, "123456789012", say=lambda m: None, sleep=lambda s: None).ensure_role()
    (role,) = iam.called("create_role")
    assert role["RoleName"] == "tico-tico-example-com" and "PermissionsBoundary" not in role


@pytest.mark.parametrize("flag,value,wanted", [
    ("--name", "PV_bad!", "--name is 1 to 40"), ("--name", "x" * 41, "--name is 1 to 40"),
    ("--aws-permissions-boundary", "tico-preview-boundary", "is an IAM policy ARN")])
def test_a_bad_name_or_boundary_stops_before_anything_is_made(flag, value, wanted):
    d, ec2, iam, ssm, r53 = world()
    argv = list(CREATE)
    argv[argv.index(flag) + 1] = value
    code, out = go(argv, d)
    assert code == 2 and wanted in out, out
    assert not iam.calls and not ec2.calls and not ssm.calls


def test_name_and_boundary_are_only_for_aws():
    code, out = go(["--target", "ssh", "--ssh", "root@h", "--name", NAME, "--non-interactive", "--domain", "tico.example.com",
                    "--auth", "google", "--client-id", "c", "--owner-email", "me@example.com"], fakes.deps())
    assert code == 2 and "are for a server on AWS" in out


def server(ip="203.0.113.9", ipv6="2600:1f16::9"):
    return FakeEC2(extra_instances=[{"InstanceId": "i-pv", "Tags": OURS, "PublicIpAddress": ip,
                                     "NetworkInterfaces": [{"Ipv6Addresses": [{"Ipv6Address": ipv6}]}]}])


def record(kind, *values, alias=False):
    rs = {"Name": DOMAIN + ".", "Type": kind, "TTL": 300}
    return {**rs, "AliasTarget": {"DNSName": "x."}} if alias else {**rs, "ResourceRecords": [{"Value": v} for v in values]}


def test_destroy_dns_deletes_the_records_that_still_point_at_this_server_and_does_it_first():
    ec2 = server()
    ec2.eips = [{"AllocationId": "eip-pv", "PublicIp": "203.0.113.9", "Tags": OURS}]
    other_name = {"Name": f"pv-other.{ZONE}.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "203.0.113.9"}]}
    r53 = FakeRoute53(ZONE, [record("A", "203.0.113.9"), record("AAAA", "2600:1f16::9"), other_name])
    d, ec2, iam, ssm, _ = world(ec2=ec2, r53=r53)
    order = []
    r53_change, ec2_terminate = r53.change_resource_record_sets, ec2.terminate_instances
    r53.change_resource_record_sets = lambda **kw: (order.append("dns"), r53_change(**kw))
    ec2.terminate_instances = lambda **kw: (order.append("terminate"), ec2_terminate(**kw))
    code, out = go(DESTROY, d)
    assert code == 0, out
    assert order == ["dns", "terminate"]
    (change,) = r53.called("change_resource_record_sets")
    assert [c["Action"] for c in change["ChangeBatch"]["Changes"]] == ["DELETE", "DELETE"]
    assert r53.records == [other_name]            # another preview's record, same zone, untouched
    assert "DNS records were not touched" not in out


@pytest.mark.parametrize("moved", [record("A", "198.51.100.1"), record("A", "203.0.113.9", "198.51.100.1"),
                                   record("A", alias=True)])
def test_destroy_dns_leaves_a_record_that_points_elsewhere(moved):
    r53 = FakeRoute53(ZONE, [moved])
    d, ec2, iam, ssm, _ = world(ec2=server(), r53=r53)
    code, out = go(DESTROY, d)
    assert code == 0, out
    assert r53.records == [moved] and not r53.called("change_resource_record_sets")
    assert "Left alone: A" in out and ec2.called("terminate_instances")


def test_destroy_dns_deletes_nothing_when_route53_cannot_be_read():
    r53 = FakeRoute53(ZONE, [record("A", "203.0.113.9")], fail=True)
    d, ec2, iam, ssm, _ = world(ec2=server(), r53=r53)
    code, out = go(DESTROY, d)
    assert code == 1 and "Could not read Route 53" in out
    assert not ec2.called("terminate_instances") and not iam.calls and not ssm.called("delete_parameter")


def test_destroy_dns_dry_run_lists_the_record_and_deletes_nothing():
    r53 = FakeRoute53(ZONE, [record("A", "203.0.113.9")])
    d, ec2, iam, ssm, _ = world(ec2=server(), r53=r53)
    code, out = go([*DESTROY, "--dry-run"], d)
    assert code == 0 and f"dns: A {DOMAIN} -> 203.0.113.9" in out
    assert not r53.called("change_resource_record_sets") and not ec2.called("terminate_instances")


def test_pointing_at_reads_only_this_name():
    r53 = FakeRoute53(ZONE, [record("A", "203.0.113.9"), {"Name": f"{DOMAIN}.", "Type": "TXT", "TTL": 300,
                                                           "ResourceRecords": [{"Value": '"x"'}]}])
    ours, other = route53.pointing_at(r53, "Z1", DOMAIN, {"203.0.113.9"})
    assert [r["Type"] for r in ours] == ["A"] and other == []
