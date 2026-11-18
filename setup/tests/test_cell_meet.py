"""Tico Meet on a cell (`cell init --meet-domain`, docs/cell.md "Tico Meet"), run with the fake System."""

import base64
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from setup import cell, contract, envfile
from setup.cell import Result
from setup.tests.helpers.cell import ROOT, add_args, host, ready, secret_file  # noqa: F401  (fixtures)

MEET = "meet-cell-1.tico.team"
CERT = cell.MEET_CERT_PATH.format(d=MEET)


def env_of(host, slug="boatyard"):
    return envfile.parse((host.root / "companies" / slug / ".env").read_text())


def keys_of(host):
    return cell.meet_keys((host.root / cell.MEET_ENV).read_text())


def edge_of(host):
    return yaml.safe_load((host.root / "edge.yaml").read_text())


def caddyfile(host):
    return (host.root / "router" / "Caddyfile").read_text()


@pytest.fixture
def meet(ready):
    answer = ready("init", "--meet-domain", MEET, "--json")
    assert answer["ok"] and answer["meet_domain"] == MEET
    return ready


# --- init ----------------------------------------------------------------------------------------------------------


def test_init_meet_domain_makes_the_key_pair_once_and_never_prints_it(ready):
    answer = ready("init", "--meet-domain", MEET, "--json")
    assert {"meet keys", "meet certificates", "edge", "cell.json"} <= set(answer["changed"])
    key, secret = keys_of(ready)
    assert key.startswith("API") and len(secret) >= 32
    assert stat.S_IMODE((ready.root / cell.MEET_ENV).stat().st_mode) == 0o600
    assert stat.S_IMODE((ready.root / cell.MEET_DIR / "certs").stat().st_mode) == 0o700
    assert secret not in json.dumps(answer) and secret not in (ready.root / "edge.yaml").read_text()
    assert secret not in (ready.root / "cell.json").read_text()
    assert json.loads((ready.root / "cell.json").read_text())["meet_domain"] == MEET
    again = ready("init", "--json")  # left out: kept, and nothing changes
    assert again["ok"] and again["changed"] == [] and again["meet_domain"] == MEET
    assert keys_of(ready) == (key, secret)


def test_init_meet_domain_off_removes_livekit_and_keeps_the_keys(meet):
    pair = keys_of(meet)
    answer = meet("init", "--meet-domain", "", "--json")
    assert answer["ok"] and "meet_domain" not in answer and "edge" in answer["changed"]
    assert set(edge_of(meet)["services"]) == {"router"}
    assert "meet_domain" not in json.loads((meet.root / "cell.json").read_text())
    up = [c for c in meet.system.calls if cell.EDGE_PROJECT in c and "up" in c][-1]
    assert "--remove-orphans" in up  # what takes certs and livekit away
    assert keys_of(meet) == pair  # on again later: the same pair
    assert meet("init", "--meet-domain", MEET, "--json")["ok"] and keys_of(meet) == pair


@pytest.mark.parametrize("bad", ["tico.team", "10.0.0.1", "*.tico.team", "https://meet.tico.team", "meet"])
def test_init_meet_domain_must_be_a_hostname_of_its_own(ready, bad):
    assert ready("init", "--meet-domain", bad, "--json")["error"] == "bad_domain"
    assert not (ready.root / cell.MEET_ENV).exists()


def test_the_meet_host_and_a_company_host_never_collide(ready):
    ready(*add_args(ready))
    assert ready("init", "--meet-domain", "boatyard.tico.team", "--json")["error"] == "domain_taken"
    assert ready("init", "--meet-domain", MEET, "--json")["ok"]
    assert ready(*add_args(ready, "meet-cell-1"))["error"] == "domain_taken"


# --- the edge project --------------------------------------------------------------------------------------------


def test_edge_runs_the_certificate_caddy_and_livekit_on_the_host_network(meet):
    services = edge_of(meet)["services"]
    assert set(services) == {"router", "certs", "livekit"}
    certs, livekit = services["certs"], services["livekit"]
    for s in (certs, livekit):
        assert s["network_mode"] == "host" and s["restart"] == "unless-stopped" and s["read_only"] is True
        assert s["cap_drop"] == ["ALL"] and s["cap_add"] == ["NET_BIND_SERVICE"]
        assert s["security_opt"] == ["no-new-privileges:true"] and "ports" not in s
    assert certs["image"] == cell.ROUTER_IMAGE and certs["environment"] == {"TICO_MEET_DOMAIN": MEET}
    assert f"{meet.root}/meet/certs:/data" in certs["volumes"]
    assert livekit["image"] == cell.MEET_IMAGE and livekit["container_name"] == cell.MEET_CONTAINER
    assert livekit["env_file"] == [f"{meet.root}/meet.env"]
    assert livekit["volumes"] == [f"{meet.root}/meet/certs:/certs:ro"]
    config = yaml.safe_load(livekit["environment"]["LIVEKIT_CONFIG"])
    assert config["port"] == 7880 and config["rtc"]["use_external_ip"] is True
    assert (config["rtc"]["tcp_port"], config["rtc"]["port_range_start"], config["rtc"]["port_range_end"]) == (
        7881,
        50000,
        50100,
    )
    turn = config["turn"]
    assert turn["domain"] == MEET and turn["tls_port"] == 443 and turn["udp_port"] == 3478
    assert turn["cert_file"] == f"/certs/{CERT}.crt" and turn["key_file"] == f"/certs/{CERT}.key"


def test_the_certificate_caddy_uses_http01_only_and_leaves_the_routers_admin_port_alone(meet):
    text = (meet.root / "meet" / "caddy" / "Caddyfile").read_text()
    assert "admin off" in text and "http_port 80" in text and "https_port 8443" in text
    assert "disable_tlsalpn_challenge" in text and "{$TICO_MEET_DOMAIN} {" in text
    assert MEET not in text  # the domain is in edge.yaml, so a new one recreates the service


def test_without_meet_the_edge_file_is_what_it_was():
    assert cell.edge_compose(Path("/srv/tico-cell"), True, None) == cell.edge_compose(Path("/srv/tico-cell"), True)
    assert "livekit" not in cell.edge_compose(Path("/srv/tico-cell"), True)


def test_livekit_is_the_image_compose_pins():
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    image = services["livekit"]["image"]
    assert image == "${TICO_MEET_IMAGE:-" + cell.MEET_IMAGE + "}"
    assert "@sha256:" in cell.MEET_IMAGE


# --- the router ----------------------------------------------------------------------------------------------------


def company(state="running", slug="acme", port=20000):
    return {"slug": slug, "domain": f"{slug}.tico.team", "port": port, "state": state, "aliases": ["acme.example.com"]}


def test_the_router_sends_a_running_companys_rtc_to_livekit_first():
    text = cell.router_config([company()], meet=True)
    site = text[text.index("http://acme.tico.team:8080, http://acme.example.com:8080 {") :]
    assert site.index("@rtc path /rtc /rtc/*") < site.index("handle @rtc {") < site.index("127.0.0.1:7880")
    assert site.index("127.0.0.1:7880") < site.index("handle {") < site.index("127.0.0.1:20000")
    rtc = site[site.index("handle @rtc {") : site.index("handle {")]
    assert "stream_close_delay 5m" in rtc and "lb_try_duration 30s" in rtc and "X-Forwarded-Proto" not in rtc


def test_without_meet_the_router_file_is_what_it_was():
    companies = [company(), company("sleeping", "beta", 20001), company("stopped", "gamma", 20002)]
    plain = cell.router_config(companies)
    assert "rtc" not in plain and plain == cell.router_config(companies, meet=False)
    on = cell.router_config(companies, meet=True)
    assert on.count("@rtc") == 2  # the running company only: the matcher and its handle
    assert on[on.index("http://beta.tico.team") :] == plain[plain.index("http://beta.tico.team") :]


def test_init_meet_domain_reloads_the_router_with_the_rtc_route(ready):
    ready(*add_args(ready))
    assert "/rtc" not in caddyfile(ready)
    n = len(ready.system.calls)
    assert "router" in ready("init", "--meet-domain", MEET, "--json")["changed"]
    assert "@rtc path /rtc /rtc/*" in caddyfile(ready)
    assert any("reload" in c for c in ready.system.calls[n:])
    ready("init", "--meet-domain", "", "--json")
    assert "/rtc" not in caddyfile(ready)


# --- each company's .env ---------------------------------------------------------------------------------------------


KEYS = {"domain": MEET, "key": "APIabcdef012345", "secret": "s" * 43}


def test_cell_env_writes_the_meet_keys_while_on_and_removes_them_while_off():
    c = {"index": 3, "domain": "acme.tico.team"}
    on = cell.cell_env({"TICO_TAG": "v1.2.3"}, c, KEYS)
    assert {k: on[k] for k in cell.MEET_ENV_KEYS} == {
        "TICO_MEET_URL": "wss://acme.tico.team",
        "TICO_MEET_INTERNAL_URL": "http://10.208.3.1:7880",
        "TICO_MEET_DOMAIN": MEET,
        "TICO_MEET_API_KEY": "APIabcdef012345",
        "TICO_MEET_API_SECRET": "s" * 43,
    }
    off = cell.cell_env(on, c)
    assert not set(cell.MEET_ENV_KEYS) & set(off) and off["TICO_TAG"] == "v1.2.3"
    assert cell.MEET_ENV_KEYS and all(cell.cell_owned(k) for k in cell.MEET_ENV_KEYS)


def test_add_while_meet_is_on_writes_the_keys(meet):
    assert meet(*add_args(meet))["ok"]
    env = env_of(meet)
    key, secret = keys_of(meet)
    assert (
        env["TICO_MEET_URL"] == "wss://boatyard.tico.team" and env["TICO_MEET_INTERNAL_URL"] == "http://10.208.0.1:7880"
    )
    assert env["TICO_MEET_API_KEY"] == key and env["TICO_MEET_API_SECRET"] == secret
    assert env["TICO_MEET_DOMAIN"] == MEET


def test_update_on_the_same_release_turns_calls_on_and_off(ready):
    ready(*add_args(ready))
    assert "TICO_MEET_URL" not in env_of(ready)
    assert ready("update", "--company", "boatyard", "--version", "v1.2.3")["changed"] is False
    ready("init", "--meet-domain", MEET, "--json")
    n = len(ready.system.calls)
    answer = ready("update", "--company", "boatyard", "--version", "v1.2.3")
    assert answer["ok"] and answer["changed"] is True and env_of(ready)["TICO_MEET_API_KEY"] == keys_of(ready)[0]
    assert any("up" in c and "tico-boatyard" in c for c in ready.system.calls[n:])  # the server restarts once
    assert ready("update", "--company", "boatyard", "--version", "v1.2.3")["changed"] is False
    ready("init", "--meet-domain", "", "--json")
    assert ready("update", "--company", "boatyard", "--version", "v1.2.3")["changed"] is True
    assert not set(cell.MEET_ENV_KEYS) & set(env_of(ready))


def test_update_to_a_new_release_keeps_the_meet_keys(meet):
    meet(*add_args(meet))
    assert meet("update", "--company", "boatyard", "--version", "v1.3.0")["ok"]
    assert env_of(meet)["TICO_TAG"] == "v1.3.0" and env_of(meet)["TICO_MEET_API_KEY"] == keys_of(meet)[0]


def test_carried_meet_settings_are_the_cells_own(meet):
    carried = secret_file(
        meet.tmp, "carried.env", "TICO_MEET_URL=wss://elsewhere.example\nTICO_MEET_API_SECRET=xxxxxxxx"
    )
    answer = meet(*add_args(meet, "boatyard", "--env-file", carried))
    assert answer["ok"] and {"TICO_MEET_URL", "TICO_MEET_API_SECRET"} <= set(answer["ignored"])
    assert env_of(meet)["TICO_MEET_URL"] == "wss://boatyard.tico.team"


def test_missing_keys_while_on_change_nothing(meet):
    meet(*add_args(meet))
    before = (meet.root / "companies" / "boatyard" / ".env").read_text()
    (meet.root / cell.MEET_ENV).unlink()
    answer = meet("update", "--company", "boatyard", "--version", "v1.3.0")
    assert answer["error"] == "meet_failed"
    assert (meet.root / "companies" / "boatyard" / ".env").read_text() == before
    assert meet("init", "--json")["ok"] and keys_of(meet)[0]  # init makes a new pair


# --- the secret ----------------------------------------------------------------------------------------------------


def test_the_secret_is_scrubbed_from_errors(meet):
    secret = keys_of(meet)[1]
    meet.system.fail["up -d --remove-orphans"] = Result(1, "", f"compose: bad value LIVEKIT_KEYS=APIx: {secret}")
    answer = meet("init", "--json")
    assert answer["error"] == "docker_failed" and secret not in answer["detail"] and "****" in answer["detail"]
    del meet.system.fail["up -d --remove-orphans"]
    meet(*add_args(meet))
    meet.system.fail["docker pull"] = Result(1, "", f"pull failed {secret}")
    answer = meet("update", "--company", "boatyard", "--version", "v1.4.0")
    assert answer["error"] == "pull_failed" and secret not in answer["detail"]


def test_the_meet_secret_is_a_secret_key():
    assert "TICO_MEET_API_SECRET" in contract.SECRET_KEYS
    shown = envfile.redact_env("TICO_MEET_API_KEY=APIabc\nTICO_MEET_API_SECRET=hunter2hunter2\n")
    assert "hunter2" not in shown and "APIabc" in shown


# --- certificate renewal -------------------------------------------------------------------------------------------


def renew(host, now=None):
    return cell.Cell(host.root, host.system, host.etc).meet_renew(now)


def issue(host, mtime):
    crt = host.root / "meet" / "certs" / (CERT + ".crt")
    crt.parent.mkdir(parents=True, exist_ok=True)
    crt.write_text("cert")
    os.utime(crt, (mtime, mtime))


def restarts(host):
    return [c for c in host.system.calls if c[-2:] == ["restart", "livekit"] and cell.EDGE_PROJECT in c]


def test_renewal_restarts_livekit_only_when_no_room_is_open(meet):
    assert renew(meet) == "no_certificate"
    issue(meet, 1_800_000_000)
    assert renew(meet) == "not_running"
    meet.system.livekit_started = "2027-01-15T08:00:00.123456789Z"  # 1_800_000_000 is 2027-01-15T08:00:00Z
    assert renew(meet) == "current"  # started after the certificate was written
    issue(meet, 1_800_000_600)
    meet.system.rooms = 2
    assert renew(meet) == "busy"
    meet.system.rooms = None  # LiveKit does not answer: not known to be empty
    assert renew(meet) == "busy" and restarts(meet) == []
    meet.system.rooms = 0
    assert renew(meet) == "restarted" and len(restarts(meet)) == 1


def test_renewal_is_off_while_meet_is(ready):
    assert renew(ready) == "off" and ready.system.tokens == []


def test_the_list_token_is_signed_with_the_cells_secret_and_may_only_list(meet):
    issue(meet, 1_800_000_600)
    meet.system.livekit_started = "2027-01-15T08:00:00Z"
    renew(meet, now=1_800_001_000)
    key, secret = keys_of(meet)
    head, body, sig = meet.system.tokens[-1].split(".")
    mac = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64encode(mac).rstrip(b"=").decode() == sig
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert claims["iss"] == key and claims["video"] == {"roomList": True}
    assert claims["exp"] - claims["nbf"] == 70 and claims["exp"] == 1_800_001_060


def test_docker_times():
    assert cell.docker_time("2027-01-15T08:00:00Z") == 1_800_000_000
    assert cell.docker_time("2027-01-15T08:00:00.5Z\n") == 1_800_000_000.5
    assert cell.docker_time("") is None and cell.docker_time("yesterday") is None


def test_the_updater_checks_the_certificate_without_ever_stopping(ready, monkeypatch):
    from setup import cell_updater as U

    svc = U.Service(ready.root, ready.system, make_cell=lambda: cell.Cell(ready.root, ready.system, ready.etc))
    logged = []
    monkeypatch.setattr(U, "log", logged.append)
    svc.meet_renew()  # off: nothing to say
    assert logged == []

    def boom(self, now=None):
        raise OSError("disk")

    monkeypatch.setattr(cell.Cell, "meet_renew", boom)
    svc.meet_renew()
    assert logged == ["tico meet: the certificate check failed: OSError"]


# --- AWS -----------------------------------------------------------------------------------------------------------


def test_the_aws_template_opens_exactly_livekits_public_ports():
    template = yaml.safe_load((ROOT / "infra" / "cell-meet" / "cell-meet.yaml").read_text())
    resources = template["Resources"]
    rules = [r["Properties"] for r in resources.values() if r["Type"] == "AWS::EC2::SecurityGroupIngress"]
    assert sorted((r["IpProtocol"], r["FromPort"], r["ToPort"]) for r in rules) == sorted(cell.MEET_PUBLIC_PORTS)
    assert all(r["CidrIp"] == "0.0.0.0/0" and r["GroupId"] == {"Ref": "SecurityGroupId"} for r in rules)
    for closed in (22, cell.MEET_PORT, cell.MEET_CERTS_HTTPS):
        assert not any(r["FromPort"] <= closed <= r["ToPort"] and r["IpProtocol"] == "tcp" for r in rules)
    association = resources["MeetIpAssociation"]["Properties"]
    assert association == {
        "AllocationId": {"Fn::GetAtt": ["MeetIp", "AllocationId"]},
        "InstanceId": {"Ref": "InstanceId"},
    }
    assert "AWS::EC2::Instance" not in {r["Type"] for r in resources.values()}  # never touches the host itself
