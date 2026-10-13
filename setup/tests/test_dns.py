

from setup import dns
from setup.tests.fakes import NETLIFY_NS, FakeResolver


def test_zone_walks_up_to_the_first_name_with_ns():
    r = FakeResolver(ns={"example.com": NETLIFY_NS})
    z = dns.detect_zone("tico.corp.example.com", r)
    assert z.name == "example.com" and z.provider.key == "netlify"


def test_resolves_to_requires_every_planned_address():
    rec = [dns.Record("A", "t.example.com", "1.2.3.4")]
    assert dns.resolves_to("t.example.com", rec, FakeResolver({("t.example.com", dns.A): ["1.2.3.4"]}))
    assert not dns.resolves_to("t.example.com", rec, FakeResolver({("t.example.com", dns.A): ["9.9.9.9"]}))
    assert not dns.resolves_to("t.example.com", rec, FakeResolver())


def test_a_runner_hostname_gets_its_own_proxied_cname_to_the_same_tunnel():
    assert dns.plan_records("t.example.com", "cloudflared", tunnel_id="abc") == [dns.Record("CNAME", "t.example.com", "abc.cfargotunnel.com", proxied=True)]
    recs = dns.plan_records("t.example.com", "cloudflared", tunnel_id="abc", runner_host="t-runner.example.com")
    assert recs == [dns.Record("CNAME", "t.example.com", "abc.cfargotunnel.com", proxied=True),
                    dns.Record("CNAME", "t-runner.example.com", "abc.cfargotunnel.com", proxied=True)]
    assert dns.plan_records("t.example.com", "cloudflared", runner_host="r.example.com")[1].value == "<tunnel-id>.cfargotunnel.com"
    zone = dns.Zone("example.com", ("ns1",), dns.detect_provider(NETLIFY_NS))
    assert any("name t-runner" in line and "proxied" in line for line in dns.manual_instructions(recs, zone))
