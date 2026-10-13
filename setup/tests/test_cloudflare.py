import json

import pytest

from setup.cloudflare import Cloudflare, CloudflareError


class Api:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body):
        path = url.split("/client/v4")[1]
        self.calls.append((method, path, json.loads(body) if body else None))
        for (m, p), resp in self.routes.items():
            if m == method and path.startswith(p):
                return resp if isinstance(resp, tuple) else (200, {"success": True, "result": resp})
        return 404, {"success": False, "errors": [{"message": "no route"}]}


def test_ensure_tunnel_creates_and_configures_ingress_to_the_server():
    api = Api({("GET", "/accounts/a1/cfd_tunnel?"): [], ("POST", "/accounts/a1/cfd_tunnel"): {"id": "t2", "token": "T2"},
               ("PUT", "/accounts/a1/cfd_tunnel/t2/configurations"): {}})
    cf = Cloudflare("tok", api)
    assert cf.ensure_tunnel("a1", "n") == ("t2", "T2")
    cf.configure_tunnel("a1", "t2", "t.example.com")
    ing = api.calls[-1][2]["config"]["ingress"]
    assert ing[0] == {"hostname": "t.example.com", "service": "http://server:8765"} and ing[-1]["service"] == "http_status:404"


def test_errors_never_include_the_token():
    api = Api({})
    with pytest.raises(CloudflareError) as e:
        Cloudflare("SECRET-TOKEN", api).call("GET", "/zones")
    assert "SECRET-TOKEN" not in str(e.value)


def test_a_runner_hostname_gets_the_api_and_downloads_only_before_the_404():
    from setup.cloudflare import ingress_config
    assert ingress_config("t.example.com") == {"ingress": [{"hostname": "t.example.com", "service": "http://server:8765"},
                                                          {"service": "http_status:404"}]}
    ing = ingress_config("t.example.com", "t-runner.example.com")["ingress"]
    assert ing[1] == {"hostname": "t-runner.example.com", "path": "^/(?:api/v2|download)(?:/.*)?$", "service": "http://server:8765"}
    assert [r.get("hostname") for r in ing] == ["t.example.com", "t-runner.example.com", None]
    api = Api({("PUT", "/accounts/a1/cfd_tunnel/t2/configurations"): {}})
    Cloudflare("tok", api).configure_tunnel("a1", "t2", "t.example.com", "t-runner.example.com")
    assert api.calls[-1][2]["config"]["ingress"] == ing


def test_upsert_cname_repoints_a_record_elsewhere_only_when_allowed():
    elsewhere = [{"id": "r1", "type": "CNAME", "name": "r.example.com", "content": "app.other.net", "proxied": True}]
    api = Api({("GET", "/zones/z1/dns_records?"): elsewhere, ("PUT", "/zones/z1/dns_records/r1"): {}})
    cf = Cloudflare("tok", api)
    with pytest.raises(CloudflareError, match="already points at app.other.net"):
        cf.upsert_cname("z1", "r.example.com", "t9.cfargotunnel.com", repoint=False)
    assert not any(m == "PUT" for m, _, _ in api.calls)
    assert cf.upsert_cname("z1", "r.example.com", "t9.cfargotunnel.com") == "repointed from app.other.net"
    assert api.calls[-1][:2] == ("PUT", "/zones/z1/dns_records/r1")
    mine = [{**elsewhere[0], "content": "t9.cfargotunnel.com"}]
    assert Cloudflare("tok", Api({("GET", "/zones/z1/dns_records?"): mine})).upsert_cname(
        "z1", "r.example.com", "t9.cfargotunnel.com", repoint=False) == "unchanged"
