import json

import pytest

from setup import cli, dns, state, verify
from setup.remote import Result
from setup.tests import fakes
from setup.tests.fakes import CaptureIO, FakeShell, aws_clients

SECRET = "GOCSPX-supersecret"
BASE = ["--non-interactive", "--domain", "tico.example.com", "--auth", "google", "--client-id", "cid.apps.googleusercontent.com",
        "--company", "Acme", "--owner-email", "me@example.com"]


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])


def go(argv, deps=None, io=None):
    io = io or CaptureIO()
    code = cli.main(argv, deps=deps or fakes.deps(), io=io)
    return code, io.text


def test_dry_run_aws_caddy_google_prints_the_plan_and_changes_nothing(tmp_path):
    code, out = go(["--dry-run", "--target", "aws", "--front-door", "caddy", *BASE])
    assert code == 0
    for want in ("EC2 t4g.small", "no SSH key, no port 22", "hop limit 1", "Elastic IP", "Netlify DNS (NS1 nameservers)",
                 "A tico.example.com -> <elastic-ip>", "https://tico.example.com/auth/callback", "Estimated cost", "ManagedBy=tico-setup",
                 "Bots run on computers you add afterwards"):
        assert want in out
    assert SECRET not in out and "TICO_OIDC_CLIENT_SECRET=********" in out
    assert "Team 'Acme', owner me@example.com" in out
    assert not (tmp_path / "home").exists()


def test_ssh_run_orders_dns_before_starting_docker_and_never_leaks_the_secret(monkeypatch):
    shell = FakeShell()
    r = fakes.FakeResolver({("tico.example.com", dns.A): ["203.0.113.7"]}, {"example.com": fakes.NETLIFY_NS})
    d = fakes.deps()
    d.resolver, d.public_resolvers, d.ssh_shell = r, (lambda: [("Google", r)]), (lambda *a: shell)
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", *BASE], deps=d)
    assert code == 0, out
    assert out.index("Add these records there") < out.index("docker compose pull")
    assert SECRET not in out
    env_input = next(i for c, i in shell.calls if i and b"TICO_DOMAIN" in i)
    assert SECRET.encode() in env_input  # it reaches the server, via stdin only
    assert not any(SECRET in c for c in shell.cmds())
    assert "Done. Open https://tico.example.com and sign in as me@example.com" in out
    assert "Settings > Computers > Add computer" in out and "setup runner" in out
    saved, env = state.load("tico.example.com")
    assert SECRET not in json.dumps(saved) and SECRET in env


def test_ssh_run_stops_before_docker_when_dns_never_goes_live():
    shell = FakeShell()
    d = fakes.deps()
    d.ssh_shell = lambda *a: shell
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--dns-timeout", "0", *BASE], deps=d)
    assert code == 2 and "Still not resolving" in out and "Not starting Tico yet" in out
    assert shell.calls == []


def test_cloudflare_tunnel_flow_creates_tunnel_and_uses_its_token(monkeypatch):
    from setup.tests.test_cloudflare import Api
    api = Api({("GET", "/zones?name=tico.example.com"): [],
               ("GET", "/zones?name=example.com"): [{"id": "z1", "name": "example.com", "account": {"id": "a1"}}],
               ("GET", "/accounts/a1/cfd_tunnel?"): [], ("POST", "/accounts/a1/cfd_tunnel"): {"id": "t9", "token": "TUNTOK"},
               ("PUT", "/accounts/a1/cfd_tunnel/t9/configurations"): {},
               ("GET", "/zones/z1/dns_records?"): [], ("POST", "/zones/z1/dns_records"): {}})
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-secret-token")
    shell = FakeShell()
    r = fakes.FakeResolver({("tico.example.com", dns.A): ["104.16.0.1"]}, {"example.com": ["a.ns.cloudflare.com"]})
    d = fakes.deps()
    d.resolver, d.public_resolvers, d.ssh_shell = r, (lambda: [("G", r)]), (lambda *a: shell)
    d.cloudflare = lambda tok: __import__("setup.cloudflare", fromlist=["x"]).Cloudflare(tok, api)
    code, out = go(["--target", "ssh", "--ssh", "root@h", "--front-door", "cloudflared", "--yes", *BASE], deps=d)
    assert code == 0, out
    env = next(i for c, i in shell.calls if i and b"TICO_DOMAIN" in i).decode()
    assert "CLOUDFLARE_TUNNEL_TOKEN=TUNTOK" in env and "COMPOSE_PROFILES=cloudflared,updater" in env
    assert "cf-secret-token" not in out and "TUNTOK" not in out and "cf-secret-token" not in env
    assert any(m == "POST" and p == "/zones/z1/dns_records" and b["content"] == "t9.cfargotunnel.com" for m, p, b in api.calls)


def test_destroy_dry_run_and_confirmation():
    ec2, iam, ssm, factory = aws_clients(fakes.FakeEC2(extra_instances=[{"InstanceId": "i-mine", "Tags": [
        {"Key": "ManagedBy", "Value": "tico-setup"}, {"Key": "tico-setup-name", "Value": "tico-example-com"}]}]))
    d = fakes.deps()
    d.aws_clients = factory
    code, out = go(["destroy", "--domain", "tico.example.com", "--dry-run"], deps=d)
    assert code == 0 and "instance: i-mine" in out and not ec2.called("terminate_instances")
    code, out = go(["destroy", "--domain", "tico.example.com"], deps=d)  # non-interactive without --yes
    assert code == 1 and not ec2.called("terminate_instances")
    code, out = go(["destroy", "--domain", "tico.example.com", "--yes"], deps=d)
    assert code == 0 and ec2.called("terminate_instances")


ACCESS = ["--non-interactive", "--domain", "tico.example.com", "--auth", "cloudflare", "--access-issuer", "https://team.cloudflareaccess.com",
          "--access-audience", "aud1", "--company", "Acme", "--owner-email", "me@example.com"]


def _tunnel_run(argv, runner_record=None, tunnels=()):
    from setup.tests.test_cloudflare import Api
    api = Api({("GET", "/zones/z1/dns_records?name=tico-runner.example.com"): [runner_record] if runner_record else [],
               ("PUT", "/zones/z1/dns_records/r1"): {},
               ("GET", "/zones?name=tico.example.com"): [],
               ("GET", "/zones?name=example.com"): [{"id": "z1", "name": "example.com", "account": {"id": "a1"}}],
               ("GET", "/accounts/a1/cfd_tunnel/t9/token"): "TUNTOK",
               ("GET", "/accounts/a1/cfd_tunnel?"): list(tunnels), ("POST", "/accounts/a1/cfd_tunnel"): {"id": "t9", "token": "TUNTOK"},
               ("PUT", "/accounts/a1/cfd_tunnel/t9/configurations"): {},
               ("GET", "/zones/z1/dns_records?"): [], ("POST", "/zones/z1/dns_records"): {}})
    shell = FakeShell()
    r = fakes.FakeResolver({("tico.example.com", dns.A): ["104.16.0.1"]}, {"example.com": ["a.ns.cloudflare.com"]})
    d = fakes.deps()
    d.resolver, d.public_resolvers, d.ssh_shell = r, (lambda: [("G", r)]), (lambda *a: shell)
    d.cloudflare = lambda tok: __import__("setup.cloudflare", fromlist=["x"]).Cloudflare(tok, api)
    code, out = go(["--target", "ssh", "--ssh", "root@h", "--front-door", "cloudflared", "--yes", *argv], deps=d)
    env = next((i for c, i in shell.calls if i and b"TICO_DOMAIN" in i), b"").decode()
    ingress = next((b["config"]["ingress"] for m, p, b in api.calls if m == "PUT" and p.endswith("/configurations")), [])
    cnames = [b["name"] for m, p, b in api.calls if m in ("POST", "PUT") and p.startswith("/zones/z1/dns_records")]
    return code, out, env, ingress, cnames


def test_access_on_a_tunnel_gets_a_runner_hostname_by_default(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-secret-token")
    code, out, env, ingress, cnames = _tunnel_run(ACCESS)
    assert code == 0, out
    assert "TICO_RUNNER_URL=https://tico-runner.example.com" in env and "TICO_AUTH_PROXY=cloudflare" in env
    assert [r.get("hostname") for r in ingress] == ["tico.example.com", "tico-runner.example.com", None]
    assert ingress[1]["path"] == "^/(?:api/v2|download)(?:/.*)?$"
    assert cnames == ["tico.example.com", "tico-runner.example.com"]
    assert "runner hostname tico-runner.example.com" in out
    assert state.load("tico.example.com")[0]["runner_host"] == "tico-runner.example.com"


def test_runner_hostname_is_opt_in_without_access_and_none_turns_it_off(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-secret-token")
    code, out, env, ingress, cnames = _tunnel_run(BASE)
    assert code == 0, out
    assert "TICO_RUNNER_URL" not in env and len(ingress) == 2 and cnames == ["tico.example.com"]
    code, out, env, ingress, cnames = _tunnel_run(["--runner-hostname", "Agents.example.com", *BASE])
    assert code == 0, out
    assert "TICO_RUNNER_URL=https://agents.example.com" in env and cnames[-1] == "agents.example.com"
    code, out, env, ingress, cnames = _tunnel_run(["--runner-hostname", "none", *ACCESS])
    assert code == 0, out
    assert "TICO_RUNNER_URL" not in env and len(ingress) == 2 and cnames == ["tico.example.com"]


def test_runner_hostname_flag_is_refused_where_it_cannot_work(monkeypatch):
    code, out = go(["--dry-run", "--target", "aws", "--front-door", "caddy", "--runner-hostname", "r.example.com", *BASE])
    assert code == 2 and "--runner-hostname needs --front-door cloudflared" in out
    for bad, why in (("tico.example.com", "must differ from --domain"), ("runner.other.org", "zone, example.com"), ("not a host", "is not valid")):
        code, out = go(["--dry-run", "--target", "ssh", "--ssh", "root@h", "--front-door", "cloudflared", "--runner-hostname", bad, *ACCESS])
        assert code == 2 and why in out, (bad, out)


def test_dry_run_with_access_plans_the_runner_record_and_env():
    code, out = go(["--dry-run", "--target", "ssh", "--ssh", "root@h", "--front-door", "cloudflared", *ACCESS],
                   deps=fakes.deps(ns={"tico.example.com": ["a.ns.cloudflare.com"]}))
    assert code == 0, out
    assert "CNAME runner.tico.example.com -> <tunnel-id>.cfargotunnel.com (proxied)" in out
    assert "TICO_RUNNER_URL=https://runner.tico.example.com" in out


def test_the_default_runner_hostname_never_takes_over_a_record_that_points_elsewhere(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-secret-token")
    elsewhere = {"id": "r1", "type": "CNAME", "name": "tico-runner.example.com", "content": "app.other.net", "proxied": True}
    code, out, env, ingress, cnames = _tunnel_run(ACCESS, runner_record=elsewhere)
    assert code == 2 and "tico-runner.example.com, the default runner hostname, already has a CNAME record pointing at app.other.net" in out
    assert "--runner-hostname tico-runner.example.com" in out
    assert env == "" and ingress == [] and cnames == [] and not (tmp_path / "home" / "tico.example.com").exists()
    # Already this tunnel's (a re-run): kept, and the run goes on.
    code, out, env, ingress, cnames = _tunnel_run(ACCESS, runner_record={**elsewhere, "content": "t9.cfargotunnel.com"},
                                                  tunnels=[{"id": "t9"}])
    assert code == 0, out
    assert "Cloudflare DNS CNAME tico-runner.example.com: unchanged" in out and "TICO_RUNNER_URL=https://tico-runner.example.com" in env
    # Named with the flag: repointed, and the output says so.
    code, out, env, ingress, cnames = _tunnel_run(["--runner-hostname", "tico-runner.example.com", *ACCESS], runner_record=elsewhere)
    assert code == 0, out
    assert "Cloudflare DNS CNAME tico-runner.example.com: repointed from app.other.net" in out and "tico-runner.example.com" in cnames
