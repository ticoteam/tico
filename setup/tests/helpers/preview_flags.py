"""Helpers that other tests share, moved out of test_preview_flags.py so they import no test file."""

import pytest
from setup import cli, dns, verify
from setup.tests import fakes
from setup.tests.fakes import CaptureIO, FakeEC2, FakeRoute53, aws_clients


ZONE = "preview.example"


NAME = "pv-abcdefgh"


DOMAIN = f"{NAME}.{ZONE}"


BOUNDARY = "arn:aws:iam::123456789012:policy/tico-preview-boundary"


OURS = [{"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": NAME}]


CREATE = [
    "--cloud",
    "aws",
    "--aws-region",
    "us-east-2",
    "--aws-instance-type",
    "t4g.medium",
    "--name",
    NAME,
    "--aws-permissions-boundary",
    BOUNDARY,
    "--hosted",
    "invited",
    "--hosted-zone",
    ZONE,
    "--domain",
    DOMAIN,
    "--preset",
    "recommended",
    "--backup",
    "local",
    "--auth",
    "oidc",
    "--issuer",
    "https://accounts.example",
    "--client-id",
    "preview-client",
    "--signin-label",
    "Tico account",
    "--owner-email",
    "ana@acme.example",
    "--non-interactive",
    "--yes",
]


# HQ's day-7 command, as it runs it: no saved install on that computer, no --asset-prefix.
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


def server(ip="203.0.113.9", ipv6="2600:1f16::9"):
    return FakeEC2(
        extra_instances=[
            {
                "InstanceId": "i-pv",
                "Tags": OURS,
                "PublicIpAddress": ip,
                "NetworkInterfaces": [{"Ipv6Addresses": [{"Ipv6Address": ipv6}]}],
            }
        ]
    )


def record(kind, *values, alias=False):
    rs = {"Name": DOMAIN + ".", "Type": kind, "TTL": 300}
    return (
        {**rs, "AliasTarget": {"DNSName": "x."}} if alias else {**rs, "ResourceRecords": [{"Value": v} for v in values]}
    )
