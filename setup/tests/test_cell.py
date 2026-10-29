"""`python3 -m setup cell`: the multi-company host, run with a fake System (no Docker, no network, no root)."""
import fcntl
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from setup import cell, envfile
from setup.cell import Result

ROOT = Path(__file__).resolve().parents[2]
SECRET = "oidc-secret-" + "s" * 40
INVITE = "inv_" + "i" * 30
BACKUP_KEY, BACKUP_SECRET = "AKIA" + "B" * 16, "bk/" + "x" * 37
TOKEN = "eyJ" + "t" * 80


class FakeSystem:
    """Records every call; `docker compose up` makes a company's container running and healthy, as Docker would."""

    def __init__(self):
        self.calls, self.chowns, self.t = [], [], 0.0
        self.ps = {}                       # slug -> (State, Status)
        self.images = set()
        self.health = {}                   # host -> status; default 200 while its container runs
        self.busy_ports, self.fail = set(), {}
        self.imds = True                   # the metadata block is DOCKER-USER's first rule
        self.docker_user = None            # or the chain's rules as `iptables -S` prints them
        self.release = "v1.2.3"

    def run(self, argv, *, input=None, timeout=900):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for key, res in self.fail.items():
            if key in joined:
                return res
        if argv[:2] == ["iptables", "-S"]:
            rules = self.docker_user if self.docker_user is not None else \
                ([cell.IMDS_LINE] if self.imds else []) + ["-A DOCKER-USER -j RETURN"]
            return Result(0, "-N DOCKER-USER\n" + "".join(r + "\n" for r in rules))
        if argv[:2] == ["docker", "pull"]:
            self.images.add(argv[-1])
            return Result(0)
        if argv[:2] == ["docker", "ps"]:
            flt = argv[argv.index("--filter") + 1]
            want = flt.split("=", 2)[2] if flt.count("=") == 2 else ""
            return Result(0, "".join(json.dumps({"Labels": f"com.docker.compose.service=server,{cell.LABEL}={s}",
                                                 "State": state, "Status": status}) + "\n"
                                     for s, (state, status) in self.ps.items() if not want or s == want))
        if argv[:3] == ["docker", "image", "inspect"]:
            return Result(0 if argv[-1] in self.images else 1)
        if "--project-name" in argv:
            proj = argv[argv.index("--project-name") + 1]
            slug = proj[len(cell.PROJECT_PREFIX):]
            if proj == cell.EDGE_PROJECT:
                return Result(0)
            if "up" in argv:
                self.ps[slug] = ("running", "Up 2 seconds (healthy)")
            elif "stop" in argv:
                self.ps[slug] = ("exited", "Exited (0) 1 second ago")
            elif "down" in argv:
                self.ps.pop(slug, None)
        return Result(0)

    def http_status(self, host, path, timeout=3):
        if host == cell.ROUTER_HEALTH_HOST:
            return 200
        slug = host.split(".")[0]
        if self.ps.get(slug, ("",))[0] != "running":
            return 502
        return self.health.get(host, 200)

    def port_free(self, port):
        return port not in self.busy_ports

    def chown(self, path, uid, gid):
        self.chowns.append((str(path), uid, gid))

    def linux(self):
        return True

    def latest_release(self):
        return self.release

    def sleep(self, seconds):
        self.t += seconds

    def monotonic(self):
        return self.t

    def argv_text(self):
        return "\n".join(" ".join(c) for c in self.calls)


@pytest.fixture
def host(tmp_path):
    system = FakeSystem()
    root, etc = tmp_path / "cell", tmp_path / "host"

    def call(*argv):
        out = io.StringIO()
        rc = cell.main(list(argv), system=system, root=root, host_root=etc, compose_source=ROOT / "compose.yaml", out=out)
        lines = out.getvalue().splitlines()
        assert len(lines) == 1, "exactly one JSON object on stdout"
        answer = json.loads(lines[0])
        assert rc == (0 if answer["ok"] else 1)
        return answer

    call.system, call.root, call.etc, call.tmp = system, root, etc, tmp_path
    return call


def secret_file(tmp_path, name, text, mode=0o600):
    p = tmp_path / name
    p.write_text(text + "\n")
    os.chmod(p, mode)
    return str(p)


def add_args(host, slug="boatyard", *extra):
    return ["add", "--company", slug, "--domain", f"{slug}.tico.team", "--owner-email", "owner@boatyard.example",
            "--auth", "oidc", "--issuer", "https://accounts.tico.team", "--client-id", "tc_" + "0" * 24,
            "--client-secret-file", secret_file(host.tmp, "client-secret", SECRET), "--version", "v1.2.3", *extra, "--json"]


@pytest.fixture
def ready(host):
    assert host("init", "--json")["ok"]
    return host


# --- arguments and the contract ----------------------------------------------------------------------------------

def test_argument_errors_answer_in_json(host):
    assert host("add", "--company", "boatyard") == {
        "ok": False, "error": "bad_args",
        "detail": "the following arguments are required: --domain, --owner-email, --auth, --issuer, --client-id, "
                  "--client-secret-file"}
    assert host("frobnicate")["error"] == "bad_args"
    assert host()["error"] == "bad_args"
    assert host("status", "--company", "boatyard", "--bogus")["error"] == "bad_args"


def test_add_before_init_is_refused(host):
    assert host(*add_args(host))["error"] == "not_initialized"


@pytest.mark.parametrize("slug", ["Boatyard", "-boat", "boat-", "boat--yard", "boat_yard", "b" * 41, "", "boat.yard"])
def test_bad_slugs_are_refused(ready, slug):
    assert ready(add_args(ready)[0], f"--company={slug}", *add_args(ready)[3:])["error"] == "bad_slug"
    assert ready("status", f"--company={slug}")["error"] == "bad_slug"


@pytest.mark.parametrize("slug", sorted(cell.RESERVED))
def test_reserved_names_are_never_given_to_a_company(ready, slug):
    answer = ready(*add_args(ready, slug))
    assert answer["error"] == "reserved_name" and slug in answer["detail"]
    assert not (ready.root / "companies" / slug).exists()


def test_the_brief_reserved_list_is_complete():
    assert cell.RESERVED == {"hub", "admin", "accounts", "updates", "preview", "runner", "www", "api", "app", "mail",
                             "status", "docs", "help", "support", "hq", "gateway", "hooks", "cloud", "tico", "team"}


def test_domain_must_be_the_slug_under_the_cell_domain(ready):
    args = add_args(ready)
    args[args.index("--domain") + 1] = "boatyard.example.com"
    assert ready(*args)["error"] == "bad_domain"


@pytest.mark.parametrize("flag,value,error", [
    ("--owner-email", "not-an-email", "bad_args"), ("--owner-email", "a$b@c.de", "bad_args"),
    ("--issuer", "http://accounts.tico.team", "bad_args"), ("--client-id", "bad id", "bad_args"),
    ("--version", "latest", "bad_args"), ("--auth", "google", "bad_args")])
def test_add_checks_every_value_before_it_changes_anything(ready, flag, value, error):
    args = add_args(ready)
    args[args.index(flag) + 1] = value
    assert ready(*args)["error"] == error
    assert not (ready.root / "companies" / "boatyard").exists()


@pytest.mark.parametrize("memory,ok", [("2g", True), ("1536m", True), ("512m", True), ("16g", True), ("256m", False),
                                       ("17g", False), ("2gb", False), ("two", False)])
def test_memory_limits(memory, ok):
    if ok:
        assert cell.check_memory(memory) == memory
    else:
        with pytest.raises(cell.CellError):
            cell.check_memory(memory)


def test_backup_options_go_together(ready):
    assert ready(*add_args(ready, "boatyard", "--backup-url", "s3://bucket/boatyard"))["error"] == "bad_args"


# --- secrets ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_secret_files_others_can_read_are_refused(ready, mode):
    args = add_args(ready)
    args[args.index("--client-secret-file") + 1] = secret_file(ready.tmp, "loose", SECRET, mode)
    answer = ready(*args)
    assert answer["error"] == "secret_file" and "--client-secret-file" in answer["detail"]
    assert SECRET not in json.dumps(answer)


def test_missing_or_empty_secret_files(ready):
    args = add_args(ready)
    args[args.index("--client-secret-file") + 1] = str(ready.tmp / "nope")
    assert ready(*args)["error"] == "secret_file"
    args[args.index("--client-secret-file") + 1] = secret_file(ready.tmp, "empty", "")
    assert ready(*args)["error"] == "secret_file"


def test_secrets_never_reach_argv_or_stdout_and_live_only_in_0600_files(tmp_path):
    system = FakeSystem()
    root = tmp_path / "cell"
    out = io.StringIO()

    def call(*argv):
        cell.main(list(argv), system=system, root=root, host_root=tmp_path / "host",
                  compose_source=ROOT / "compose.yaml", out=out)

    call("init", "--tunnel-token-file", secret_file(tmp_path, "token", TOKEN), "--json")
    host_args = ["add", "--company", "boatyard", "--domain", "boatyard.tico.team", "--owner-email", "o@boatyard.example",
                 "--auth", "oidc", "--issuer", "https://accounts.tico.team", "--client-id", "tc_1",
                 "--client-secret-file", secret_file(tmp_path, "cs", SECRET),
                 "--gateway-invite-file", secret_file(tmp_path, "invite", INVITE),
                 "--backup-url", "s3://tico-hosting-backups/boatyard", "--backup-region", "us-west-2",
                 "--backup-credentials-file", secret_file(tmp_path, "keys", json.dumps(
                     {"AccessKey": {"AccessKeyId": BACKUP_KEY, "SecretAccessKey": BACKUP_SECRET}})),
                 "--version", "v1.2.3", "--json"]
    call(*host_args)
    call(*host_args)
    call("status", "--company", "boatyard")
    call("update", "--company", "boatyard", "--version", "v1.2.4")
    system.health["boatyard.tico.team"] = 503       # an error path that prints log lines
    system.fail["logs"] = Result(0, f"server: TICO_OIDC_CLIENT_SECRET={SECRET} leaked by a bad log line\n")
    call("update", "--company", "boatyard", "--version", "v1.2.5", "--timeout", "1")
    printed, argv = out.getvalue(), system.argv_text()
    # Both releases stay unhealthy here, so the rollback says so too: two error paths with log lines.
    assert "rollback_failed" in printed and "did not come up" in printed and "********" in printed
    for secret in (SECRET, INVITE, BACKUP_SECRET, TOKEN):
        assert secret not in printed and secret not in argv
    env = root / "companies" / "boatyard" / ".env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    values = envfile.parse(env.read_text())
    assert values["TICO_OIDC_CLIENT_SECRET"] == SECRET and values["TICO_GATEWAY_INVITE"] == INVITE
    assert values["LITESTREAM_ACCESS_KEY_ID"] == BACKUP_KEY and values["LITESTREAM_SECRET_ACCESS_KEY"] == BACKUP_SECRET
    assert stat.S_IMODE((root / "tunnel.env").stat().st_mode) == 0o600
    assert (root / "tunnel.env").read_text() == f"TUNNEL_TOKEN={TOKEN}\n"
    link = root / "companies" / "boatyard" / "owner-link.txt"
    assert stat.S_IMODE(link.stat().st_mode) == 0o600 and link.read_text() not in printed


def test_an_unexpected_failure_is_still_one_scrubbed_json_answer(ready):
    def boom(*a, **k):
        raise RuntimeError(f"surprise with {SECRET} inside")
    ready.system.port_free = boom
    answer = ready(*add_args(ready))
    assert answer["ok"] is False and answer["error"] == "internal" and set(answer) == {"ok", "error", "detail"}
    assert SECRET not in answer["detail"] and "********" in answer["detail"]


@pytest.mark.parametrize("text", [
    f"LITESTREAM_ACCESS_KEY_ID={BACKUP_KEY}\nLITESTREAM_SECRET_ACCESS_KEY={BACKUP_SECRET}",
    f"AWS_ACCESS_KEY_ID={BACKUP_KEY}\nAWS_SECRET_ACCESS_KEY={BACKUP_SECRET}",
    json.dumps({"AccessKey": {"AccessKeyId": BACKUP_KEY, "SecretAccessKey": BACKUP_SECRET}}),
    json.dumps({"AccessKeyId": BACKUP_KEY, "SecretAccessKey": BACKUP_SECRET})])
def test_backup_credential_formats(text):
    assert cell.backup_keys(text) == (BACKUP_KEY, BACKUP_SECRET)


def test_backup_credentials_without_keys_are_refused():
    with pytest.raises(cell.CellError) as e:
        cell.backup_keys("nothing=here")
    assert e.value.code == "secret_file"


# --- add, and adding again ---------------------------------------------------------------------------------------

def test_add_makes_the_hosted_server_and_answers_once_healthy(ready):
    answer = ready(*add_args(ready))
    assert answer == {"ok": True, "company": "boatyard", "url": "https://boatyard.tico.team", "port": 20000,
                      "ready_ms": answer["ready_ms"], "version": "v1.2.3", "changed": True,
                      "owner_link_file": str(ready.root / "companies" / "boatyard" / "owner-link.txt")}
    assert isinstance(answer["ready_ms"], int)
    d = ready.root / "companies" / "boatyard"
    assert (d / "compose.yaml").read_text() == (ROOT / "compose.yaml").read_text()
    env = envfile.parse((d / ".env").read_text())
    assert env["TICO_DOMAIN"] == "boatyard.tico.team" and env["TICO_PORT"] == "20000"
    assert env["COMPOSE_PROJECT_NAME"] == "tico-boatyard" and env["TICO_TAG"] == "v1.2.3"
    assert env["TICO_HOSTED"] == "invited" and env["TICO_AUTH_PROXY"] == "oidc"
    assert env["TICO_OIDC_ISSUER"] == "https://accounts.tico.team" and env["TICO_OIDC_LABEL"] == "Tico account"
    assert env["TICO_INSTALL_PRESET"] == "recommended" and len(env["TICO_OWNER_LINK_HASH"]) == 64
    assert env["TICO_TRUSTED_PROXIES"] == "127.0.0.1/32,::1/128,10.208.0.1/32,10.224.0.1/32"
    assert env["TICO_CLIENT_IP_HEADER"] == "cf-connecting-ip" and env["TICO_EDGE_SUBNET"] == "10.224.0.0/28"
    assert env["TICO_COMPANY_FILE_DIR"] == str(d / "company-file") and env["TICO_TEAM_NAME"] == "Boatyard"
    # No front door, updater or profile of its own: the cell's router and tunnel are the front door.
    for key in ("COMPOSE_PROFILES", "TICO_UPDATER_URL", "CLOUDFLARE_TUNNEL_TOKEN"):
        assert key not in env
    # Tico runs its updates (`cell update`): the app shows no Update now (backend/releases.py managed()).
    assert env["TICO_UPDATES"] == "tico"
    override = (d / "compose.cell.yaml").read_text()
    assert "    environment:\n      TICO_UPDATES:\n" in override     # passed to the server from .env
    assert "mem_limit: 2g" in override and "memswap_limit: 2g" in override and f"{d / 'data'}:/data" in override
    assert "subnet: 10.208.0.0/24" in override and f"{cell.LABEL}: boatyard" in override
    assert (str(d / "data"), 10001, 10001) in ready.system.chowns
    calls = ready.system.argv_text()
    assert "--project-name tico-boatyard" in calls and "up -d" in calls
    assert f"docker pull --quiet {cell.SERVER_IMAGE}:v1.2.3" in calls
    assert "caddy reload" in calls
    assert "http://boatyard.tico.team:8080 {\n\treverse_proxy 127.0.0.1:20000" in (ready.root / "router" / "Caddyfile").read_text()


def test_adding_again_with_the_same_settings_changes_nothing(ready):
    first = ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    files = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}
    calls = len(ready.system.calls)
    second = ready(*add_args(ready))
    assert second["changed"] is False and {k: v for k, v in second.items() if k not in ("ready_ms", "changed")} == \
        {k: v for k, v in first.items() if k not in ("ready_ms", "changed")}
    assert {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()} == files      # the owner link hash is kept
    again = " ".join(" ".join(c) for c in ready.system.calls[calls:])
    assert "pull" not in again and "caddy reload" not in again


def test_adding_again_keeps_what_was_saved_when_an_option_is_left_out(ready):
    ready(*add_args(ready, "boatyard", "--memory", "3g", "--gateway-invite-file", secret_file(ready.tmp, "inv", INVITE)))
    args = add_args(ready)
    del args[args.index("--version"):args.index("--version") + 2]
    answer = ready(*args)
    assert answer["changed"] is False and answer["version"] == "v1.2.3"
    d = ready.root / "companies" / "boatyard"
    assert "mem_limit: 3g" in (d / "compose.cell.yaml").read_text()
    assert envfile.parse((d / ".env").read_text())["TICO_GATEWAY_INVITE"] == INVITE


def test_adding_again_with_a_new_secret_converges(ready):
    ready(*add_args(ready))
    args = add_args(ready)
    args[args.index("--client-secret-file") + 1] = secret_file(ready.tmp, "rotated", "rotated-" + "r" * 30)
    assert ready(*args)["changed"] is True
    env = envfile.parse((ready.root / "companies" / "boatyard" / ".env").read_text())
    assert env["TICO_OIDC_CLIENT_SECRET"] == "rotated-" + "r" * 30


def test_default_version_is_the_installers_release(ready):
    args = add_args(ready)
    del args[args.index("--version"):args.index("--version") + 2]
    ready.system.release = "v7.0.1"
    assert ready(*args)["version"] == (cell._installer_version() or "v7.0.1")


def test_add_waits_for_health_and_says_why_it_failed(ready):
    ready.system.health["boatyard.tico.team"] = 502
    ready.system.fail["logs"] = Result(0, "tico: error: set TICO_OWNER_EMAIL\n")
    answer = ready(*add_args(ready, "boatyard", "--timeout", "2"))
    assert answer["error"] == "unhealthy" and "set TICO_OWNER_EMAIL" in answer["detail"]
    # The company stays, so the same add resumes it once the cause is fixed.
    ready.system.health.clear()
    assert ready(*add_args(ready))["ok"]


def test_add_refuses_when_containers_could_reach_the_metadata_service(ready):
    ready.system.imds = False
    answer = ready(*add_args(ready))
    assert answer["error"] == "not_initialized" and "metadata" in answer["detail"]


def test_a_company_file_becomes_the_servers_starter_plan(ready):
    from backend import company_file as C
    draft = C.Turn(C.blank()).finish()
    draft["bots"] = draft["bots"][:5]
    draft["company"]["name"] = "Boatyard Marine"
    path = ready.tmp / "tico.company.yaml"
    path.write_text(C.to_yaml(draft))
    assert ready(*add_args(ready, "boatyard", "--company-file", str(path)))["ok"]
    d = ready.root / "companies" / "boatyard"
    env = envfile.parse((d / ".env").read_text())
    plan = d / "company-file" / "plan.b64"
    assert env["TICO_TEAM_NAME"] == "Boatyard Marine" and env["TICO_SAMPLE_COMPANY"] == "off"
    assert env["TICO_COMPANY_FILE"] == "/run/tico/company-file/plan.b64" and "TICO_COMPANY_FILE_B64" not in env
    assert stat.S_IMODE(plan.stat().st_mode) == 0o600 and (str(plan), 10001, 10001) in ready.system.chowns
    assert ready(*add_args(ready))["changed"] is False          # left out again: the saved plan stays


def test_a_bad_company_file_is_refused(ready):
    path = ready.tmp / "bad.yaml"
    path.write_text("not: [a, company")
    assert ready(*add_args(ready, "boatyard", "--company-file", str(path)))["error"] == "company_file"


# --- ports and networks ---------------------------------------------------------------------------------------

def test_ports_never_collide_and_survive_restarts(ready):
    assert [ready(*add_args(ready, s))["port"] for s in ("alpha", "bravo", "charlie")] == [20000, 20001, 20002]
    assert ready("remove", "--company", "alpha")["removed"] is True
    ready.system.busy_ports.add(20000)       # something else on the host took it meanwhile
    assert ready(*add_args(ready, "delta"))["port"] == 20003
    ready.system.busy_ports.clear()
    assert ready(*add_args(ready, "echo"))["port"] == 20000
    # A new process (a host restart) reads the same ports back from disk.
    assert {c["company"]: c["port"] for c in ready("list")["companies"]} == \
        {"bravo": 20001, "charlie": 20002, "delta": 20003, "echo": 20000}


def test_remove_keep_data_keeps_the_port_for_a_later_add(ready):
    ready(*add_args(ready, "alpha"))
    ready(*add_args(ready, "bravo"))
    assert ready("remove", "--company", "alpha", "--keep-data") == {
        "ok": True, "company": "alpha", "removed": True, "kept_data": True}
    assert (ready.root / "companies" / "alpha" / "data").is_dir()
    assert ready(*add_args(ready, "charlie"))["port"] == 20002
    assert ready(*add_args(ready, "alpha"))["port"] == 20000


def test_each_index_has_its_own_networks():
    seen = set()
    for i in (0, 1, 255, 256, cell.MAX_COMPANIES - 1):
        n = cell.networks(i)
        assert n["default_subnet"] not in seen and n["edge_subnet"] not in seen
        seen |= {n["default_subnet"], n["edge_subnet"]}
    import ipaddress
    last = cell.networks(cell.MAX_COMPANIES - 1)
    assert ipaddress.ip_network(last["default_subnet"]).subnet_of(cell.DEFAULT_NETS)
    assert ipaddress.ip_network(last["edge_subnet"]).subnet_of(cell.EDGE_NETS)


# --- the router ------------------------------------------------------------------------------------------------

def test_router_config_routes_each_host_and_refuses_the_rest():
    text = cell.router_config([{"domain": "b.tico.team", "port": 20001}, {"domain": "a.tico.team", "port": 20000}])
    assert text.index("http://a.tico.team:8080") < text.index("http://b.tico.team:8080")
    assert "reverse_proxy 127.0.0.1:20001" in text and "header_up X-Forwarded-Proto https" in text
    assert "default_bind 127.0.0.1" in text and "auto_https off" in text
    assert text.rstrip().endswith('respond "No Tico company at this address." 404\n}')


def test_router_drops_removed_companies(ready):
    ready(*add_args(ready, "alpha"))
    ready(*add_args(ready, "bravo"))
    ready("remove", "--company", "alpha", "--keep-data")
    text = (ready.root / "router" / "Caddyfile").read_text()
    assert "alpha.tico.team" not in text and "bravo.tico.team" in text


def test_a_config_the_router_refuses_is_put_back(ready):
    ready(*add_args(ready, "alpha"))
    before = (ready.root / "router" / "Caddyfile").read_text()
    ready.system.fail["caddy reload"] = Result(1, "", "Error: adapting config")
    answer = ready(*add_args(ready, "bravo"))
    assert answer["error"] == "router_failed"
    assert (ready.root / "router" / "Caddyfile").read_text() == before


def test_edge_images_are_the_ones_compose_yaml_pins():
    text = (ROOT / "compose.yaml").read_text()
    assert cell.ROUTER_IMAGE in text and cell.TUNNEL_IMAGE in text


# --- init ------------------------------------------------------------------------------------------------------

def test_init_sets_up_the_host_once(host):
    daemon = host.etc / "etc/docker/daemon.json"
    daemon.parent.mkdir(parents=True)
    daemon.write_text(json.dumps({"log-opts": {"max-size": "50m"}, "data-root": "/var/lib/docker"}))
    first = host("init", "--tunnel-token-file", secret_file(host.tmp, "token", TOKEN), "--json")
    assert first["ok"] and first["tunnel"] is True and first["domain"] == "tico.team"
    merged = json.loads(daemon.read_text())
    assert merged["log-opts"] == {"max-size": "50m"} and merged["data-root"] == "/var/lib/docker"
    assert merged["live-restore"] is True
    script = (host.etc / cell.FIREWALL_SCRIPT).read_text()
    assert "169.254.169.254/32 -j REJECT" in script and "DOCKER-USER" in script
    assert "Before=docker.service" in (host.etc / cell.FIREWALL_UNIT).read_text()
    assert "ExecStartPost=/usr/local/sbin/tico-cell-firewall" in (host.etc / cell.DOCKER_DROPIN).read_text()
    edge = (host.root / "edge.yaml").read_text()
    assert "network_mode: host" in edge and "tunnel:" in edge and f"{host.root}/tunnel.env" in edge
    assert '"*.tico.team"' in (host.root / "tunnel" / "cloudflared.yml").read_text()
    calls = host.system.argv_text()
    assert "systemctl restart docker" in calls and "systemctl enable tico-cell-firewall.service" in calls
    n = len(host.system.calls)
    second = host("init", "--json")
    assert second["ok"] and second["changed"] == [] and second["tunnel"] is True
    assert "systemctl" not in " ".join(" ".join(c) for c in host.system.calls[n:])


def test_init_without_a_token_runs_only_the_router(host):
    answer = host("init", "--domain", "cell.example.com", "--json")
    assert answer["tunnel"] is False and "tunnel:" not in (host.root / "edge.yaml").read_text()
    assert json.loads((host.root / "cell.json").read_text())["domain"] == "cell.example.com"


def test_init_installs_docker_when_missing(host):
    host.system.fail["compose version"] = Result(1, "", "docker: not found")
    host("init", "--json")
    assert any(c[:2] == ["sh", "-c"] and "get.docker.com" in c[2] for c in host.system.calls)


def test_init_stops_when_the_metadata_block_is_not_in_place(host):
    host.system.imds = False
    assert host("init", "--json")["error"] == "firewall_failed"


def test_init_cannot_move_a_cell_with_companies(ready):
    ready(*add_args(ready))
    assert ready("init", "--domain", "other.example.com", "--json")["error"] == "bad_domain"


# --- the other commands ----------------------------------------------------------------------------------------

def test_list_status_stop_start(ready):
    assert ready("list") == {"ok": True, "companies": []}
    ready(*add_args(ready))
    status = ready("status", "--company", "boatyard", "--json")
    assert status["state"] == "running" and status["healthy"] is True and status["port"] == 20000
    assert ready("stop", "--company", "boatyard") == {"ok": True, "company": "boatyard", "state": "stopped", "changed": True}
    assert ready("stop", "--company", "boatyard")["changed"] is False
    assert ready("status", "--company", "boatyard")["state"] == "stopped"
    assert ready("list")["companies"][0]["state"] == "stopped"
    started = ready("start", "--company", "boatyard")
    assert started["ok"] and started["changed"] is True and started["port"] == 20000
    assert ready("status", "--company", "nobody")["error"] == "not_found"


def test_update_snapshots_then_runs_the_new_release(ready):
    ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("db v1")
    answer = ready("update", "--company", "boatyard", "--version", "v1.3.0", "--json")
    assert answer["ok"] and answer["version"] == "v1.3.0" and answer["changed"] is True
    assert envfile.parse((d / ".env").read_text())["TICO_TAG"] == "v1.3.0"
    assert [p.name.endswith("-v1.2.3") for p in (d / "snapshots").iterdir()] == [True]
    assert ready("update", "--company", "boatyard", "--version", "v1.3.0")["changed"] is False
    # A later add without --version keeps the updated release.
    args = add_args(ready)
    del args[args.index("--version"):args.index("--version") + 2]
    assert ready(*args) | {"ready_ms": 0} == answer | {"ready_ms": 0, "changed": False}


def test_a_failed_update_puts_the_old_release_and_database_back(ready):
    ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("db v1")
    ready.system.health["boatyard.tico.team"] = 503

    real_run = ready.system.run

    def migrating(argv, **kw):          # the new release migrates the database, then never gets healthy
        if "up" in argv and "--project-name" in argv:
            env = envfile.parse((d / ".env").read_text())
            if env["TICO_TAG"] == "v2.0.0":
                (d / "data" / "hub.sqlite").write_text("db v2")
            else:
                ready.system.health.clear()
        return real_run(argv, **kw)

    ready.system.run = migrating
    answer = ready("update", "--company", "boatyard", "--version", "v2.0.0", "--timeout", "1")
    assert answer["ok"] is False and answer["error"] == "update_failed" and answer["version"] == "v1.2.3"
    assert (d / "data" / "hub.sqlite").read_text() == "db v1"
    assert envfile.parse((d / ".env").read_text())["TICO_TAG"] == "v1.2.3"
    assert ready("status", "--company", "boatyard")["version"] == "v1.2.3"


def test_a_release_that_cannot_be_pulled_changes_nothing(ready):
    ready(*add_args(ready))
    env_before = (ready.root / "companies" / "boatyard" / ".env").read_text()
    ready.system.fail["pull --quiet"] = Result(1, "", "manifest unknown")
    answer = ready("update", "--company", "boatyard", "--version", "v9.0.0")
    assert answer["error"] == "pull_failed"
    assert (ready.root / "companies" / "boatyard" / ".env").read_text() == env_before
    assert "stop" not in ready.system.calls[-1]


def test_remove_cleans_up_and_is_idempotent(ready):
    ready(*add_args(ready))
    assert ready("remove", "--company", "boatyard", "--json") == {
        "ok": True, "company": "boatyard", "removed": True, "kept_data": False}
    assert "down --remove-orphans --volumes" in ready.system.argv_text()
    assert not (ready.root / "companies" / "boatyard").exists()
    assert "boatyard" not in (ready.root / "router" / "Caddyfile").read_text()
    assert ready("remove", "--company", "boatyard") == {"ok": True, "company": "boatyard", "removed": False,
                                                        "kept_data": False}


def test_the_setup_cli_hands_cell_commands_over(monkeypatch):
    from setup import cli
    seen = []
    monkeypatch.setattr(cell, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["cell", "list", "--json"]) == 0 and seen == [["list", "--json"]]


def test_updating_a_stopped_company_keeps_it_stopped(ready):
    ready(*add_args(ready))
    ready("stop", "--company", "boatyard")
    answer = ready("update", "--company", "boatyard", "--version", "v1.4.0")
    assert answer["ok"] and answer["version"] == "v1.4.0" and answer["ready_ms"] == 0
    assert ready("status", "--company", "boatyard")["state"] == "stopped"
    assert ready("start", "--company", "boatyard")["version"] == "v1.4.0"


# --- review fixes: remove, the metadata block, updates and locks ---------------------------------------------------

def held(root, name):
    """Whether some command holds the lock `name` now (another open file's flock conflicts, in this process too)."""
    fd = os.open(root / "locks" / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def record(host, slug="boatyard"):
    return json.loads((host.root / "companies" / slug / "company.json").read_text())


def test_remove_frees_the_slot_only_after_the_router_dropped_the_route(ready):
    ready(*add_args(ready, "alpha"))
    seen, real = [], ready.system.run

    def watch(argv, **kw):
        if "reload" in argv and "caddy" in argv:
            seen.append((held(ready.root, "cell"), (ready.root / "companies" / "alpha").exists()))
        return real(argv, **kw)
    ready.system.run = watch
    assert ready("remove", "--company", "alpha")["removed"] is True
    assert seen == [(True, True)]           # reloaded under the cell lock, while alpha still held its port


def test_a_router_that_refuses_the_removal_keeps_the_slot_taken(ready):
    ready(*add_args(ready, "alpha"))
    ready.system.fail["caddy reload"] = Result(1, "", "Error: adapting config")
    assert ready("remove", "--company", "alpha")["error"] == "router_failed"
    assert record(ready, "alpha")["state"] == "removed"
    ready.system.fail.clear()
    # alpha's route may still be live, so its port goes to no one else until the removal finishes.
    assert ready(*add_args(ready, "bravo"))["port"] == 20001
    assert ready("remove", "--company", "alpha")["removed"] is True
    assert not (ready.root / "companies" / "alpha").exists()
    assert "alpha" not in (ready.root / "router" / "Caddyfile").read_text()
    assert ready(*add_args(ready, "charlie"))["port"] == 20000


def test_the_metadata_block_must_be_the_first_rule(ready):
    ready.system.docker_user = ["-A DOCKER-USER -s 10.0.0.0/8 -j ACCEPT", cell.IMDS_LINE, "-A DOCKER-USER -j RETURN"]
    assert ready(*add_args(ready))["error"] == "not_initialized"
    assert ready("init", "--json")["error"] == "firewall_failed"
    ready.system.docker_user = [cell.IMDS_LINE, "-A DOCKER-USER -s 10.0.0.0/8 -j ACCEPT"]
    assert ready(*add_args(ready))["ok"]


IPTABLES_STUB = """
import json, os, sys
path, a = os.environ["RULES"], sys.argv[1:]
rules = json.load(open(path))
if a[0] == "-N":
    sys.exit(1)                                       # the chain exists
if a[:2] == ["-S", "DOCKER-USER"]:
    print("-N DOCKER-USER")
    print("".join(r + "\\n" for r in rules), end="")
    sys.exit(0)
if a[:3] == ["-I", "DOCKER-USER", "1"]:
    json.dump(["-A DOCKER-USER " + " ".join(a[3:])] + rules, open(path, "w"))
    sys.exit(0)
sys.exit(2)
"""


@pytest.mark.parametrize("rules,inserted", [
    (["-A DOCKER-USER -s 10.0.0.0/8 -j ACCEPT", cell.IMDS_LINE, "-A DOCKER-USER -j RETURN"], True),
    ([], True), ([cell.IMDS_LINE, "-A DOCKER-USER -j RETURN"], False)])
def test_the_firewall_script_puts_the_block_first(ready, tmp_path, rules, inserted):
    bindir, state = tmp_path / "bin", tmp_path / "rules.json"
    bindir.mkdir()
    (bindir / "iptables").write_text(f"#!{sys.executable}\n" + IPTABLES_STUB)
    os.chmod(bindir / "iptables", 0o755)
    state.write_text(json.dumps(rules))
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "RULES": str(state)}
    for _ in range(2):                  # the second run (Docker started again) finds it first and adds nothing
        subprocess.run(["sh", str(ready.etc / cell.FIREWALL_SCRIPT)], env=env, check=True)
    after = json.loads(state.read_text())
    assert after[0] == cell.IMDS_LINE and len(after) == len(rules) + inserted


def migrate_then_fail(host, bad="v2.0.0"):
    """`bad` migrates the database and never answers /healthz; any other release is healthy."""
    d = host.root / "companies" / "boatyard"
    host.system.health["boatyard.tico.team"] = 503
    real = host.system.run

    def run(argv, **kw):
        if "up" in argv and "--project-name" in argv:
            if envfile.parse((d / ".env").read_text())["TICO_TAG"] == bad:
                (d / "data" / "hub.sqlite").write_text("db v2")
                host.system.health["boatyard.tico.team"] = 503
            else:
                host.system.health.clear()
        return real(argv, **kw)
    host.system.run = run
    return d


@pytest.mark.parametrize("how", ["start", "add"])
def test_updating_a_stopped_company_is_guarded_when_it_next_starts(ready, how):
    ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("db v1")
    ready("stop", "--company", "boatyard")
    assert ready("update", "--company", "boatyard", "--version", "v2.0.0")["ok"]
    assert [p.name.endswith("-v1.2.3") for p in (d / "snapshots").iterdir()] == [True]
    assert record(ready)["pending"]["from"] == "v1.2.3"
    migrate_then_fail(ready)
    args = add_args(ready)
    del args[args.index("--version"):args.index("--version") + 2]
    answer = ready("start", "--company", "boatyard", "--timeout", "1") if how == "start" else \
        ready(*args[:-1], "--timeout", "1", "--json")
    assert answer["error"] == "update_failed" and answer["version"] == "v1.2.3"
    assert (d / "data" / "hub.sqlite").read_text() == "db v1"
    assert envfile.parse((d / ".env").read_text())["TICO_TAG"] == "v1.2.3" and "pending" not in record(ready)
    assert ready("status", "--company", "boatyard")["state"] == "running"


def test_a_good_release_after_a_stopped_update_ends_the_pending_update(ready):
    ready(*add_args(ready))
    ready("stop", "--company", "boatyard")
    ready("update", "--company", "boatyard", "--version", "v1.4.0")
    assert ready("start", "--company", "boatyard")["version"] == "v1.4.0"
    assert "pending" not in record(ready)


def test_going_back_while_an_update_is_pending_restores_the_database(ready):
    ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("db v1")
    ready("stop", "--company", "boatyard")
    ready("update", "--company", "boatyard", "--version", "v2.0.0")
    ready("update", "--company", "boatyard", "--version", "v2.1.0")     # still pending: the same snapshot
    assert len(list((d / "snapshots").iterdir())) == 1 and record(ready)["pending"]["from"] == "v1.2.3"
    answer = ready("update", "--company", "boatyard", "--version", "v1.2.3")
    assert answer["ok"] and answer["version"] == "v1.2.3" and "pending" not in record(ready)
    assert ready("status", "--company", "boatyard")["state"] == "stopped"


@pytest.mark.parametrize("stop", ["fails", "ignored"])
def test_a_rollback_never_replaces_the_database_under_a_running_server(ready, stop):
    ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("db v1")
    migrate_then_fail(ready)
    inner = ready.system.run

    def run(argv, **kw):
        new_up = any(c[-3:] == ["up", "-d", "--remove-orphans"] for c in ready.system.calls[-30:]) and \
            envfile.parse((d / ".env").read_text())["TICO_TAG"] == "v2.0.0"
        if "stop" in argv and new_up:
            ready.system.calls.append(list(argv))
            return Result(1, "", "cannot stop") if stop == "fails" else Result(0)   # "ignored": still running
        return inner(argv, **kw)
    ready.system.run = run
    answer = ready("update", "--company", "boatyard", "--version", "v2.0.0", "--timeout", "1")
    assert answer["error"] == "rollback_failed" and "left as it is" in answer["detail"]
    assert answer["version"] == "v2.0.0"
    assert (d / "data" / "hub.sqlite").read_text() == "db v2"           # untouched while it may still run


@pytest.mark.parametrize("old_up", ["fails", "unhealthy"])
def test_a_rollback_says_when_the_old_release_does_not_come_back(ready, old_up):
    ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("db v1")
    ready.system.health["boatyard.tico.team"] = 503        # neither release answers
    inner = ready.system.run

    def run(argv, **kw):
        if old_up == "fails" and "up" in argv and "--project-name" in argv \
                and envfile.parse((d / ".env").read_text())["TICO_TAG"] == "v1.2.3":
            return Result(1, "", "no space left on device")
        return inner(argv, **kw)
    ready.system.run = run
    answer = ready("update", "--company", "boatyard", "--version", "v2.0.0", "--timeout", "1")
    assert answer["error"] == "rollback_failed" and answer["version"] == "v1.2.3"
    assert ("starting it failed: no space left" if old_up == "fails" else "does not answer") in answer["detail"]
    assert (d / "data" / "hub.sqlite").read_text() == "db v1"


def test_add_with_another_release_points_to_update(ready):
    ready(*add_args(ready))
    n = len(ready.system.calls)
    args = add_args(ready)
    args[args.index("--version") + 1] = "v1.3.0"
    answer = ready(*args)
    assert answer["error"] == "use_update" and "cell update --company boatyard --version v1.3.0" in answer["detail"]
    assert envfile.parse((ready.root / "companies" / "boatyard" / ".env").read_text())["TICO_TAG"] == "v1.2.3"
    assert not any("up" in c for c in ready.system.calls[n:])


@pytest.mark.parametrize("step", ["stop", "snapshot"])
def test_a_failed_stop_or_snapshot_leaves_the_old_release_running(ready, monkeypatch, step):
    ready(*add_args(ready))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("db v1")
    env_before = (d / ".env").read_text()
    if step == "stop":
        ready.system.fail["stop server"] = Result(1, "", "daemon busy")
    else:
        def full(*a, **k):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(cell.shutil, "copy2", full)
    answer = ready("update", "--company", "boatyard", "--version", "v2.0.0")
    assert answer["error"] == f"{step}_failed" and "still on v1.2.3" in answer["detail"]
    assert answer["version"] == "v1.2.3" and (d / ".env").read_text() == env_before
    assert record(ready)["version"] == "v1.2.3" and "pending" not in record(ready)
    assert ready.system.calls[-1][-3:] == ["up", "-d", "--remove-orphans"]
    assert not (d / "snapshots").exists() or not any((d / "snapshots").iterdir())


@pytest.mark.parametrize("argv", [["stop"], ["start"], ["update", "--version", "v1.3.0"]])
def test_commands_read_the_record_once_they_hold_its_lock(ready, argv):
    ready(*add_args(ready))
    fd = os.open(ready.root / "locks" / "company-boatyard.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    real = ready.system.sleep

    def sleep(seconds):                 # while it waits for the lock, another command removes the company
        nonlocal fd
        if fd is not None:
            path = ready.root / "companies" / "boatyard" / "company.json"
            path.write_text(json.dumps({**json.loads(path.read_text()), "state": "removed"}))
            os.close(fd)
            fd = None
        real(seconds)
    ready.system.sleep = sleep
    answer = ready(argv[0], "--company", "boatyard", *argv[1:])
    assert answer["error"] == "not_found" and record(ready)["state"] == "removed"
