"""Public .dev workspace and scoped Route53 origin: fake AWS, DNS and shell only."""
import pytest

from setup import dns, state, verify
from setup.tests import fakes
from setup.tests.test_preview_flags import CREATE, DESTROY, DOMAIN, NAME, ZONE, go, record, server, world

PUBLIC = "acme.tico.dev"


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "client-SECRET-0123456789abcdef")
    monkeypatch.setenv("TICO_GATEWAY_INVITE", "inv_SECRET-0123456789")
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])


def create():
    argv = list(CREATE)
    argv[argv.index("--domain") + 1] = PUBLIC
    argv[argv.index("--hosted-zone") + 1] = "tico.dev"
    return [*argv, "--dns-name", DOMAIN, "--dns-zone", ZONE]


def alias_world(**kwargs):
    d, ec2, iam, ssm, r53 = world(**kwargs)
    resolver = fakes.FakeResolver({(DOMAIN, dns.A): ["203.0.113.9"], (PUBLIC, dns.A): ["203.0.113.9"]},
                                  {ZONE: fakes.R53_NS, "tico.dev": ["a.ns.cloudflare.com"]})
    d.resolver, d.public_resolvers = resolver, lambda: [("G", resolver)]
    d.cloudflare = lambda *a: pytest.fail("the installer must not write the Cloudflare root")
    return d, ec2, iam, ssm, r53


def test_public_domain_tls_callback_and_owner_link_stay_public_while_only_origin_dns_is_written(monkeypatch):
    d, ec2, iam, ssm, r53 = alias_world()
    checked = []
    monkeypatch.setattr(verify, "run_all", lambda **kw: checked.append(kw) or [verify.Check("stub", True, "ok")])
    code, out = go(create(), d)
    assert code == 0, out
    [change] = r53.called("change_resource_record_sets")
    [rr] = change["ChangeBatch"]["Changes"]
    assert rr["ResourceRecordSet"]["Name"].rstrip(".") == DOMAIN
    [param] = ssm.called("put_parameter")
    assert f"TICO_DOMAIN={PUBLIC}\n" in param["Value"] and f"TICO_DOMAIN={DOMAIN}" not in param["Value"]
    [launch] = ec2.called("run_instances")
    assert PUBLIC in launch["UserData"] and f"tico-{NAME}" == launch["IamInstanceProfile"]["Name"]
    assert checked[0]["domain"] == PUBLIC
    saved, env = state.load(PUBLIC)
    assert (saved["domain"], saved["dns_name"], saved["dns_zone"]) == (PUBLIC, DOMAIN, ZONE)
    assert (state.home() / PUBLIC / "owner-link.txt").read_text().startswith(f"https://{PUBLIC}/")
    assert DOMAIN not in env
    assert f"{DOMAIN} resolves correctly" in out and f"{PUBLIC} resolves correctly" in out


def test_aws_final_dns_check_uses_the_allocated_ip_for_the_public_hostname(monkeypatch):
    d, ec2, iam, ssm, r53 = alias_world()
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.check_dns(kw["domain"], kw["records"], kw["resolvers"])])
    code, out = go(create(), d)
    assert code == 0, out
    assert f"[ok] DNS: {PUBLIC} resolves correctly" in out
    assert state.load(PUBLIC)[0]["server_ip"] == "203.0.113.9"


@pytest.mark.parametrize("flag,value", [("--dns-name", "*.preview.example"), ("--dns-name", "https://pv-x.preview.example"),
                                       ("--dns-name", "pv-x.other.example"), ("--dns-zone", "preview.example/path"),
                                       ("--dns-name", "pv-x.preview.example\n")])
def test_bad_alias_input_is_refused_before_any_aws_write(flag, value):
    d, ec2, iam, ssm, r53 = alias_world()
    argv = create()
    argv[argv.index(flag) + 1] = value
    code, out = go(argv, d)
    assert code == 2, out
    assert not ec2.calls and not iam.calls and not ssm.calls and not r53.calls


def test_authority_must_match_the_explicit_route53_zone_before_aws():
    d, ec2, iam, ssm, r53 = alias_world()
    d.resolver = fakes.FakeResolver({}, {"example": fakes.R53_NS})
    code, out = go(create(), d)
    assert code == 2 and "detected authoritative Route 53 zone" in out
    assert not ec2.calls and not iam.calls and not ssm.calls


def test_public_dns_must_resolve_before_the_install_can_succeed():
    d, ec2, iam, ssm, r53 = alias_world()
    resolver = fakes.FakeResolver({(DOMAIN, dns.A): ["203.0.113.9"]}, {ZONE: fakes.R53_NS})
    d.public_resolvers = lambda: [("G", resolver)]
    code, out = go([*create(), "--dns-timeout", "0"], d)
    assert code != 0 and "Still not resolving" in out
    assert "Done. Open" not in out


@pytest.mark.parametrize("saved", [False, True])
def test_destroy_uses_persisted_or_explicit_operational_dns_and_deletes_it_first(saved):
    r53 = fakes.FakeRoute53(ZONE, [record("A", "203.0.113.9")])
    d, ec2, iam, ssm, r53 = alias_world(ec2=server(), r53=r53)
    if saved:
        state.save(PUBLIC, {"domain": PUBLIC, "target": "aws", "aws_name": NAME, "dns_name": DOMAIN,
                            "dns_zone": ZONE}, "")
    argv = ["destroy", "--domain", PUBLIC, "--name", NAME, "--dns", "--yes"]
    if not saved:
        argv += ["--dns-name", DOMAIN, "--dns-zone", ZONE]
    order = []
    change, terminate = r53.change_resource_record_sets, ec2.terminate_instances
    r53.change_resource_record_sets = lambda **kw: (order.append("dns"), change(**kw))
    ec2.terminate_instances = lambda **kw: (order.append("terminate"), terminate(**kw))
    code, out = go(argv, d)
    assert code == 0, out
    assert order == ["dns", "terminate"] and r53.records == []


def test_destroy_refuses_mismatched_dns_authority_without_releasing_resources():
    d, ec2, iam, ssm, r53 = alias_world(ec2=server())
    code, out = go(["destroy", "--domain", PUBLIC, "--name", NAME, "--dns", "--yes",
                    "--dns-name", "pv-abcdefgh.child." + ZONE, "--dns-zone", "child." + ZONE], d)
    assert code == 1 and "nothing was deleted" in out
    assert not ec2.called("terminate_instances") and not r53.calls


@pytest.fixture
def saved_alias():
    d, ec2, iam, ssm, r53 = alias_world()
    argv = create()
    argv[argv.index("--dns-name") + 1] = NAME + ".child." + ZONE
    argv[argv.index("--dns-zone") + 1] = "child." + ZONE
    d.resolver.records[(NAME + ".child." + ZONE, dns.A)] = ["203.0.113.9"]
    d.resolver.ns = {"child." + ZONE: fakes.R53_NS}
    r53.zone = "child." + ZONE
    assert go(argv, d)[0] == 0
    return argv, d, ec2, r53


@pytest.mark.parametrize("flag,value", [("--dns-name", "pv-ijklmnop.child." + ZONE), ("--dns-zone", ZONE)])
def test_resume_rejects_conflicting_saved_dns_identity_before_state_or_cloud_changes(saved_alias, flag, value):
    argv, d, ec2, r53 = saved_alias
    before = {p.name: p.read_bytes() for p in (state.home() / PUBLIC).iterdir()}
    requested = []
    clients, route53_client = d.aws_clients, d.route53_client
    d.aws_clients = lambda *a: requested.append("aws") or clients(*a)
    d.route53_client = lambda *a: requested.append("route53") or route53_client(*a)
    argv[argv.index(flag) + 1] = value
    if flag == "--dns-zone":
        d.resolver.ns = {value: fakes.R53_NS}
        r53.zone = value
    code, out = go([*argv, "--skip-dns-wait"], d)
    assert {p.name: p.read_bytes() for p in (state.home() / PUBLIC).iterdir()} == before
    assert code == 2, out
    assert requested == []


@pytest.mark.parametrize("flag,value", [("--dns-name", "pv-ijklmnop.child." + ZONE), ("--dns-zone", ZONE)])
def test_destroy_rejects_conflicting_saved_dns_identity_before_releasing_the_old_address(saved_alias, flag, value):
    _, d, ec2, r53 = saved_alias
    original = list(r53.records)
    before = state.load(PUBLIC)
    requested = []
    clients, route53_client = d.aws_clients, d.route53_client
    d.aws_clients = lambda *a: requested.append("aws") or clients(*a)
    d.route53_client = lambda *a: requested.append("route53") or route53_client(*a)
    if flag == "--dns-zone":
        d.resolver.ns = {value: fakes.R53_NS}
        r53.zone = value
    code, out = go(["destroy", "--domain", PUBLIC, "--name", NAME, "--dns", "--yes", flag, value], d)
    assert not ec2.called("release_address"), (ec2.calls, r53.records)
    assert not ec2.called("terminate_instances") and r53.records == original
    assert code == 2, out
    assert state.load(PUBLIC) == before and requested == []


def test_resume_keeps_saved_dns_identity_with_omitted_or_unchanged_explicit_flags():
    d, ec2, iam, ssm, r53 = alias_world()
    assert go(create(), d)[0] == 0
    before = state.load(PUBLIC)
    original = list(r53.records)
    for flags in ([], ["--dns-name", DOMAIN.upper(), "--dns-zone", ZONE.upper()]):
        code, out = go(["--domain", PUBLIC, "--non-interactive", "--yes", *flags], d)
        assert code == 0, out
        assert state.load(PUBLIC) == before
    assert len(ec2.called("run_instances")) == len(ec2.called("allocate_address")) == 1
    assert r53.records == original
