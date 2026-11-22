from setup import dns, verify
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
    assert dns.plan_records("t.example.com", "cloudflared", tunnel_id="abc") == [
        dns.Record("CNAME", "t.example.com", "abc.cfargotunnel.com", proxied=True)
    ]
    recs = dns.plan_records("t.example.com", "cloudflared", tunnel_id="abc", runner_host="t-runner.example.com")
    assert recs == [
        dns.Record("CNAME", "t.example.com", "abc.cfargotunnel.com", proxied=True),
        dns.Record("CNAME", "t-runner.example.com", "abc.cfargotunnel.com", proxied=True),
    ]
    assert (
        dns.plan_records("t.example.com", "cloudflared", runner_host="r.example.com")[1].value
        == "<tunnel-id>.cfargotunnel.com"
    )
    zone = dns.Zone("example.com", ("ns1",), dns.detect_provider(NETLIFY_NS))
    assert any("name t-runner" in line and "proxied" in line for line in dns.manual_instructions(recs, zone))


# --- waiting for and checking the record: retries, most resolvers, the zone's own nameservers -------------------------

REC = [dns.Record("A", "t.example.com", "1.2.3.4")]


class Flaky:
    """Times out `fails` times, then answers from `records`; logs each ask."""

    def __init__(self, records=None, fails=0):
        self.inner, self.fails, self.asked = FakeResolver(records), fails, 0

    def query(self, name, rtype):
        self.asked += 1
        if self.fails:
            self.fails -= 1
            raise TimeoutError("timed out")
        return self.inner.query(name, rtype)


LIVE = {("t.example.com", dns.A): ["1.2.3.4"]}
STALE = {("t.example.com", dns.A): ["9.9.9.9"]}


def test_a_resolver_that_times_out_is_asked_again_and_one_stale_answer_does_not_fail_the_check():
    slept = []
    resolvers = [("Google", Flaky(LIVE, fails=2)), ("Cloudflare", Flaky(STALE)), ("Quad9", Flaky(LIVE))]
    check = verify.check_dns("t.example.com", REC, resolvers, sleep=slept.append)
    assert check.ok and "not yet on Cloudflare" in check.detail and slept == [1, 2]
    lone = [("Google", Flaky(LIVE)), ("Cloudflare", Flaky(STALE)), ("Quad9", Flaky(fails=9))]
    assert not verify.check_dns("t.example.com", REC, lone, sleep=lambda s: None).ok


def test_the_wait_asks_public_resolvers_only_once_the_zone_has_the_record():
    public = Flaky(LIVE)
    ns = Flaky()  # the record is not there yet
    rounds = iter(range(100))

    def sleep(seconds):
        if seconds == 10 and next(rounds) == 2:
            ns.inner.records.update(LIVE)  # the person adds it

    said = []
    live = dns.wait_until_live(
        "t.example.com",
        REC,
        [("Google", public)],
        timeout=600,
        say=said.append,
        sleep=sleep,
        clock=lambda: 0.0,
        authoritative=[("ns-1.awsdns-01.org", ns)],
    )
    assert live and public.asked == 0 and "its nameservers" in said[-1]


def test_unreachable_nameservers_fall_back_to_most_public_resolvers():
    said = []
    live = dns.wait_until_live(
        "t.example.com",
        REC,
        [("Google", Flaky(LIVE)), ("Cloudflare", Flaky(STALE)), ("Quad9", Flaky(LIVE))],
        timeout=0,
        say=said.append,
        sleep=lambda s: None,
        authoritative=[("ns1", Flaky(fails=99))],
    )
    assert live and said == ["  t.example.com resolves correctly on Google, Quad9."]
