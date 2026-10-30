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
        self.hosts = {}                    # host -> slug, for a host that is not <slug>.<anything>

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
        slug = self.hosts.get(host) or host.split(".")[0]
        if self.ps.get(slug, ("",))[0] != "running":
            return 502
        return self.health.get(host, 200)

    def port_free(self, port):
        return port not in self.busy_ports

    def chown(self, path, uid, gid):
        self.chowns.append((str(path), uid, gid))

    def open_archive(self, path):
        return cell.System.open_archive(self, path)

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


def test_the_router_keeps_net_bind_service_so_caddy_can_exec(host):
    router = cell.edge_compose(host.root, tunnel=True).split("  tunnel:")[0]
    assert "cap_add: [NET_BIND_SERVICE]" in router and "cap_drop: [ALL]" in router
    assert "no-new-privileges:true" in router and "read_only: true" in router


def test_init_gives_the_tunnel_directory_to_cloudflared(host):
    host("init", "--tunnel-token-file", secret_file(host.tmp, "token", TOKEN), "--json")
    d = host.root / "tunnel"
    assert (str(d), 65532, 65532) in host.system.chowns
    assert (str(d / "cloudflared.yml"), 65532, 65532) in host.system.chowns
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_rerunning_init_sets_the_tunnel_owner_again(host):
    token = secret_file(host.tmp, "token", TOKEN)
    host("init", "--tunnel-token-file", token, "--json")
    host.system.chowns.clear()
    answer = host("init", "--json")
    assert answer["ok"] and answer["changed"] == []
    assert (str(host.root / "tunnel"), 65532, 65532) in host.system.chowns


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


# --- moving an existing install in: own domain, aliases, carried settings, its data -------------------------------

def move_args(host, *extra, slug="ticoteam", domain="hub.tico.team"):
    host.system.hosts[domain] = slug
    args = add_args(host, slug)
    args[args.index("--domain") + 1] = domain
    return [*args[:-1], "--own-domain", *extra, "--json"]


def test_an_own_domain_needs_the_flag(ready):
    args = move_args(ready)
    without = [a for a in args if a != "--own-domain"]
    assert ready(*without)["error"] == "bad_domain"
    assert not (ready.root / "companies" / "ticoteam").exists()
    answer = ready(*args)
    assert answer["ok"] and answer["url"] == "https://hub.tico.team"
    env = envfile.parse((ready.root / "companies" / "ticoteam" / ".env").read_text())
    assert env["TICO_DOMAIN"] == "hub.tico.team"
    assert "http://hub.tico.team:8080 {\n\treverse_proxy 127.0.0.1:20000" in (ready.root / "router" / "Caddyfile").read_text()


def test_an_own_domain_company_can_be_hosted_own(ready):
    answer = ready(*move_args(ready, "--hosted", "own", slug="tidy", domain="tidy.rocks"))
    assert answer["ok"] and answer["url"] == "https://tidy.rocks"
    env = envfile.parse((ready.root / "companies" / "tidy" / ".env").read_text())
    assert env["TICO_HOSTED"] == "own" and env["TICO_DOMAIN"] == "tidy.rocks"
    assert env["TICO_AUTH_PROXY"] == "oidc" and len(env["TICO_OWNER_LINK_HASH"]) == 64


@pytest.mark.parametrize("bad", ["tico.team", "10.0.0.1", "localhost", "*.tico.team", "hub_x.example.com",
                                 "hub-.example.com", "hub..example.com", "cell-health.invalid", "https://hub.example.com"])
def test_own_domains_and_aliases_are_plain_hostnames(ready, bad):
    args = move_args(ready)
    args[args.index("--domain") + 1] = bad
    assert ready(*args)["error"] == "bad_domain"
    assert ready(*move_args(ready, "--alias", bad))["error"] == "bad_domain"
    assert not (ready.root / "companies" / "ticoteam").exists()


def test_alias_rules(ready):
    assert ready(*move_args(ready, "--alias", "hub.tico.team"))["error"] == "bad_domain"
    assert ready(*move_args(ready, "--alias", "runner.tico.team", "--alias", "runner.tico.team"))["error"] == "bad_domain"
    six = [x for i in range(6) for x in ("--alias", f"a{i}.example.com")]
    assert ready(*move_args(ready, *six))["error"] == "bad_args"
    assert not (ready.root / "companies" / "ticoteam").exists()


def test_no_host_belongs_to_two_companies(ready):
    ready(*add_args(ready, "boatyard"))
    answer = ready(*move_args(ready, "--alias", "boatyard.tico.team"))
    assert answer["error"] == "domain_taken" and "boatyard" in answer["detail"]
    assert ready(*move_args(ready, "--alias", "runner.tico.team"))["ok"]
    # Another company's own domain or new name cannot take an alias of ticoteam's.
    assert ready(*move_args(ready, slug="other", domain="runner.tico.team"))["error"] == "domain_taken"
    assert ready(*add_args(ready, "runner2"))["ok"]
    # A removed company's hosts are free again.
    ready("remove", "--company", "ticoteam", "--keep-data")
    assert ready(*move_args(ready, slug="other", domain="runner.tico.team"))["ok"]


def test_aliases_are_kept_replaced_and_shown(ready):
    first = move_args(ready, "--alias", "runner.tico.team", "--alias", "hub.ticofiles.com")
    assert ready(*first)["ok"]
    assert record(ready, "ticoteam")["aliases"] == ["runner.tico.team", "hub.ticofiles.com"]
    caddy = (ready.root / "router" / "Caddyfile").read_text()
    assert ("http://hub.tico.team:8080, http://runner.tico.team:8080, http://hub.ticofiles.com:8080 {\n"
            "\treverse_proxy 127.0.0.1:20000") in caddy
    again = ready(*move_args(ready))                                # left out: kept
    assert again["changed"] is False and record(ready, "ticoteam")["aliases"] == ["runner.tico.team", "hub.ticofiles.com"]
    assert ready("list")["companies"][0]["aliases"] == ["runner.tico.team", "hub.ticofiles.com"]
    assert ready("status", "--company", "ticoteam")["aliases"] == ["runner.tico.team", "hub.ticofiles.com"]
    ready(*move_args(ready, "--alias", "runner.tico.team"))         # given: replaces
    assert record(ready, "ticoteam")["aliases"] == ["runner.tico.team"]
    assert "hub.ticofiles.com" not in (ready.root / "router" / "Caddyfile").read_text()
    ready(*move_args(ready, "--alias", ""))                         # "" alone: none
    assert "aliases" not in record(ready, "ticoteam")
    assert ready("status", "--company", "ticoteam")["aliases"] == []


# the tunnel

def test_tunnel_config_lists_hosts_the_wildcard_misses_first():
    companies = [{"domain": "hub.tico.team", "aliases": ["runner.tico.team", "hub.ticofiles.com"]},
                 {"domain": "tidy.rocks", "aliases": ["a.b.tico.team"]}, {"domain": "boatyard.tico.team"}]
    hosts = cell.tunnel_hosts("tico.team", companies)
    assert hosts == ["a.b.tico.team", "hub.ticofiles.com", "tidy.rocks"]
    text = cell.tunnel_config("tico.team", hosts)
    assert text.index('"hub.ticofiles.com"') < text.index('"tidy.rocks"') < text.index('"*.tico.team"') \
        < text.index("http_status:404")
    assert text.count("service: http://127.0.0.1:8080") == 4
    # Without such hosts: the file every cell already has, so upgrading restarts nothing.
    assert cell.tunnel_config("tico.team") == (
        "# Written by `python3 -m setup cell init`: every company's name to the router; anything else a 404.\n"
        "ingress:\n  - hostname: \"*.tico.team\"\n    service: http://127.0.0.1:8080\n  - service: http_status:404\n")


@pytest.fixture
def tunneled(host):
    assert host("init", "--tunnel-token-file", secret_file(host.tmp, "token", TOKEN), "--json")["ok"]
    return host


def restarts(host, since):
    return sum(1 for c in host.system.calls[since:] if c[-2:] == ["restart", "tunnel"] and cell.EDGE_PROJECT in c)


def test_the_tunnel_restarts_only_when_its_routes_change(tunneled):
    routes = tunneled.root / "tunnel" / "cloudflared.yml"
    plain = routes.read_text()
    n = len(tunneled.system.calls)
    tunneled(*add_args(tunneled, "boatyard"))                       # under the wildcard: nothing to add
    assert restarts(tunneled, n) == 0 and routes.read_text() == plain
    n = len(tunneled.system.calls)
    tunneled.system.chowns.clear()
    assert tunneled(*move_args(tunneled, "--alias", "runner.tico.team", "--alias", "hub.ticofiles.com"))["changed"]
    text = routes.read_text()
    assert '"hub.ticofiles.com"' in text and '"runner.tico.team"' not in text and '"hub.tico.team"' not in text
    assert text.index('"hub.ticofiles.com"') < text.index('"*.tico.team"')
    assert restarts(tunneled, n) == 1 and (str(routes), 65532, 65532) in tunneled.system.chowns
    n = len(tunneled.system.calls)
    assert tunneled(*move_args(tunneled))["changed"] is False       # the same again
    assert tunneled("init", "--json")["changed"] == []               # init writes the same routes
    assert restarts(tunneled, n) == 0 and routes.read_text() == text
    tunneled("remove", "--company", "ticoteam")
    assert routes.read_text() == plain and restarts(tunneled, n) == 1


def test_a_tunnel_that_fails_to_restart_gets_its_old_routes_back(tunneled):
    routes = tunneled.root / "tunnel" / "cloudflared.yml"
    plain = routes.read_text()
    tunneled.system.fail["restart tunnel"] = Result(1, "", "Error response from daemon")
    answer = tunneled(*move_args(tunneled, "--alias", "hub.ticofiles.com"))
    assert answer["error"] == "docker_failed" and "tunnel" in answer["detail"]
    assert routes.read_text() == plain
    tunneled.system.fail.clear()
    n = len(tunneled.system.calls)
    assert tunneled(*move_args(tunneled))["ok"] and restarts(tunneled, n) == 1     # the next run tries again
    assert '"hub.ticofiles.com"' in routes.read_text()


def test_without_a_tunnel_the_routes_are_written_but_nothing_restarts(ready):
    ready(*move_args(ready, slug="tidy", domain="tidy.rocks"))
    assert '"tidy.rocks"' in (ready.root / "tunnel" / "cloudflared.yml").read_text()
    assert restarts(ready, 0) == 0


# carried settings

CARRIED = ("# from hub.tico.team's .env\n"
           "TICO_RUNNER_URL=https://runner.tico.team\nTICO_VISUAL_ORIGIN=https://hub.ticofiles.com\n"
           "TICO_BLOB_BUCKET=tico-team-blobs\nTICO_BLOB_REGION=us-west-2\nTICO_INBOUND_KEY=inbound-" + "k" * 30 + "\n"
           "TYPESAFE_API_KEY=ts-" + "y" * 30 + "\nTICO_STALE_REVIEW=48\nTICO_SUPPORT_ACCESS=on\n"
           "TICO_MIGRATE_TO_AUTOMATIONS=1\nTICO_OIDC_ALLOWED_DOMAINS=tico.team\n"
           # the cell's own, or a single-company install's: never kept
           "TICO_DOMAIN=old.example.com\nTICO_OIDC_LABEL=Google\nTICO_TAG=v0.0.1\nTICO_PORT=8765\nCOMPOSE_PROFILES=caddy\n"
           "CLOUDFLARE_TUNNEL_TOKEN=" + "c" * 40 + "\nTICO_CADDY_VISUALS=1\nTICO_TUNNEL_X=1\n"
           "TICO_EXPECTED_AWS_ACCOUNT=123456789012\nTICO_OWNER_LINK_HASH=" + "0" * 64 + "\n")
IGNORED = ["CLOUDFLARE_TUNNEL_TOKEN", "COMPOSE_PROFILES", "TICO_CADDY_VISUALS", "TICO_DOMAIN",
           "TICO_EXPECTED_AWS_ACCOUNT", "TICO_OIDC_LABEL", "TICO_OWNER_LINK_HASH", "TICO_PORT", "TICO_TAG", "TICO_TUNNEL_X"]


def test_carried_settings_are_kept_and_the_cells_own_win(ready):
    answer = ready(*move_args(ready, "--env-file", secret_file(ready.tmp, "old.env", CARRIED)))
    assert answer["ok"] and answer["ignored"] == IGNORED and answer["unused"] == ["TICO_MIGRATE_TO_AUTOMATIONS"]
    assert "k" * 30 not in json.dumps(answer) and "123456789012" not in json.dumps(answer)
    d = ready.root / "companies" / "ticoteam"
    assert stat.S_IMODE((d / "extra.env").stat().st_mode) == 0o600
    extra = envfile.parse((d / "extra.env").read_text())
    assert set(extra).isdisjoint(IGNORED) and extra["TICO_RUNNER_URL"] == "https://runner.tico.team"
    env = envfile.parse((d / ".env").read_text())
    assert env["TICO_RUNNER_URL"] == "https://runner.tico.team" and env["TICO_BLOB_BUCKET"] == "tico-team-blobs"
    assert env["TYPESAFE_API_KEY"] == "ts-" + "y" * 30 and env["TICO_OIDC_ALLOWED_DOMAINS"] == "tico.team"
    assert env["TICO_DOMAIN"] == "hub.tico.team" and env["TICO_OIDC_LABEL"] == "Tico account"
    assert env["TICO_TAG"] == "v1.2.3" and env["TICO_PORT"] == "20000" and env["TICO_OWNER_LINK_HASH"] != "0" * 64
    for key in ("COMPOSE_PROFILES", "CLOUDFLARE_TUNNEL_TOKEN", "TICO_CADDY_VISUALS", "TICO_EXPECTED_AWS_ACCOUNT"):
        assert key not in env
    # Left out again: kept, nothing changes. Then `update` keeps them too.
    files = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}
    again = ready(*move_args(ready))
    assert again["changed"] is False and "ignored" not in again
    assert {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()} == files
    assert ready("update", "--company", "ticoteam", "--version", "v1.3.0")["ok"]
    env = envfile.parse((d / ".env").read_text())
    assert env["TICO_TAG"] == "v1.3.0" and env["TICO_BLOB_REGION"] == "us-west-2" and env["TICO_STALE_REVIEW"] == "48"


def test_a_new_env_file_replaces_the_carried_settings(ready):
    ready(*move_args(ready, "--env-file", secret_file(ready.tmp, "old.env", CARRIED)))
    answer = ready(*move_args(ready, "--env-file", secret_file(ready.tmp, "new.env", "TICO_STALE_REVIEW=24")))
    assert answer["changed"] is True and answer["ignored"] == []
    env = envfile.parse((ready.root / "companies" / "ticoteam" / ".env").read_text())
    assert env["TICO_STALE_REVIEW"] == "24" and "TICO_BLOB_BUCKET" not in env and "TICO_RUNNER_URL" not in env


@pytest.mark.parametrize("text", ["lower_case=1", "TICO_X", "1TICO=1", "TICO X=1", "TICO_X=a\x07b",
                                  "K" + "X" * 64 + "=1"])
def test_bad_env_files_are_refused_without_their_values(ready, text):
    answer = ready(*move_args(ready, "--env-file", secret_file(ready.tmp, "bad.env", "TICO_OK=1\n" + text)))
    assert answer["error"] == "secret_file" and "--env-file" in answer["detail"] and "a\x07b" not in answer["detail"]
    assert not (ready.root / "companies" / "ticoteam").exists()


def test_an_env_file_others_can_read_is_refused(ready):
    answer = ready(*move_args(ready, "--env-file", secret_file(ready.tmp, "loose.env", "TICO_STALE_REVIEW=1", 0o644)))
    assert answer["error"] == "secret_file"


def test_carried_values_are_scrubbed_from_answers(ready):
    ready(*move_args(ready, "--env-file", secret_file(ready.tmp, "old.env", CARRIED)))
    ready.system.health["hub.tico.team"] = 503
    ready.system.fail["logs"] = Result(0, "server: bad TYPESAFE_API_KEY=ts-" + "y" * 30 + "\n")
    answer = ready("update", "--company", "ticoteam", "--version", "v1.3.0", "--timeout", "1")
    assert answer["ok"] is False and "y" * 30 not in json.dumps(answer) and "********" in answer["detail"]


# importing its data

import tarfile  # noqa: E402


def data_tar(tmp_path, name="data.tar.gz", extra=None, mode=0o600, hub=True):
    """An install's /data as `tar -C /data -czf ... .` makes it, plus `extra` (TarInfo, bytes) members."""
    import io as _io
    path = tmp_path / name
    files = {"hub.sqlite": b"db", "hub.sqlite-wal": b"wal", "credential.key": b"k" * 32, "github-app.key": b"pem",
             "tico-session-secret": b"s", "visual-secret": b"v", "registry/bots/a.yaml": b"name: a\n",
             "blobs/ab/abcdef": b"x" * 100}
    if not hub:
        files.pop("hub.sqlite")
    with tarfile.open(path, "w:gz" if name.endswith("gz") else "w") as tar:
        root = tarfile.TarInfo("./")
        root.type, root.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(root)
        for dname in ("registry", "registry/bots", "blobs", "blobs/ab"):
            info = tarfile.TarInfo(f"./{dname}")
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(info)
        for fname, body in files.items():
            info = tarfile.TarInfo(f"./{fname}")
            info.size, info.mode, info.uid = len(body), 0o600, 10001
            tar.addfile(info, _io.BytesIO(body))
        for info, body in extra or []:
            tar.addfile(info, _io.BytesIO(body) if body is not None else None)
    os.chmod(path, mode)
    return str(path), sum(len(b) for b in files.values())


def member(name, kind=tarfile.REGTYPE, linkname="", body=b""):
    info = tarfile.TarInfo(name)
    info.type, info.linkname, info.size = kind, linkname, len(body) if kind == tarfile.REGTYPE else 0
    return info, (body if kind == tarfile.REGTYPE else None)


def test_import_fills_a_new_companys_data_before_its_first_start(ready):
    archive, size = data_tar(ready.tmp, extra=[member("./registry/b.yaml", tarfile.LNKTYPE, "./registry/bots/a.yaml")])
    d = ready.root / "companies" / "ticoteam"
    seen, real = [], ready.system.run

    def run(argv, **kw):
        if "up" in argv and "--project-name" in argv:
            seen.append((d / "data" / "hub.sqlite").read_bytes())
        return real(argv, **kw)
    ready.system.run = run
    answer = ready(*move_args(ready, "--import-data", archive))
    assert answer["ok"] and answer["imported"] is True and answer["imported_bytes"] == size and answer["changed"]
    assert seen == [b"db"]                                          # in place before the server first started
    assert (d / "data" / "registry" / "bots" / "a.yaml").read_text() == "name: a\n"
    assert (d / "data" / "registry" / "b.yaml").read_text() == "name: a\n"
    assert not (d / cell.IMPORT_STAGING).exists() and stat.S_IMODE((d / "data").stat().st_mode) == 0o700
    # Owned while still in the staging folder: the rename keeps the owner.
    owned = {p.replace(cell.IMPORT_STAGING, "data") for p, uid, gid in ready.system.chowns if uid == gid == 10001}
    for p in (d / "data").rglob("*"):
        assert str(p) in owned
    assert str(d / "data") in owned
    # Imported once: the same command again is refused and touches nothing; without it the company converges.
    assert ready(*move_args(ready, "--import-data", archive))["error"] == "data_exists"
    assert ready(*move_args(ready))["changed"] is False


def test_a_plain_tar_imports_too(ready):
    archive, size = data_tar(ready.tmp, name="data.tar")
    assert ready(*move_args(ready, "--import-data", archive))["imported_bytes"] == size


def test_import_refuses_a_company_that_has_data(ready):
    ready(*add_args(ready, "boatyard"))
    d = ready.root / "companies" / "boatyard"
    (d / "data" / "hub.sqlite").write_text("live db")
    archive, _ = data_tar(ready.tmp)
    assert ready(*add_args(ready, "boatyard", "--import-data", archive))["error"] == "data_exists"
    assert (d / "data" / "hub.sqlite").read_text() == "live db" and not (d / cell.IMPORT_STAGING).exists()


def test_import_into_a_kept_but_empty_data_folder(ready):
    ready(*add_args(ready, "boatyard"))
    ready("remove", "--company", "boatyard", "--keep-data")
    archive, _ = data_tar(ready.tmp)
    answer = ready(*add_args(ready, "boatyard", "--import-data", archive))
    assert answer["imported"] is True
    assert (ready.root / "companies" / "boatyard" / "data" / "hub.sqlite").read_bytes() == b"db"


@pytest.mark.parametrize("bad", [
    member("/etc/cron.d/x", body=b"x"), member("./../escape", body=b"x"), member("./a/../../escape", body=b"x"),
    member("./link", tarfile.SYMTYPE, "../../etc/passwd"), member("./link", tarfile.SYMTYPE, "hub.sqlite"),
    member("./hard", tarfile.LNKTYPE, "/etc/passwd"), member("./hard", tarfile.LNKTYPE, "../outside"),
    member("./hard", tarfile.LNKTYPE, "./not-in-the-archive"),
    member("./tty", tarfile.CHRTYPE), member("./disk", tarfile.BLKTYPE), member("./pipe", tarfile.FIFOTYPE)])
def test_a_bad_archive_is_refused_whole_and_leaves_nothing(ready, bad):
    archive, _ = data_tar(ready.tmp, extra=[bad])
    answer = ready(*move_args(ready, "--import-data", archive))
    assert answer["error"] == "bad_archive"
    assert not (ready.root / "companies" / "ticoteam").exists()
    assert not (ready.tmp / "escape").exists() and not (ready.root / "companies" / "escape").exists()


def test_an_archive_of_the_folder_not_its_contents_is_refused(ready):
    archive, _ = data_tar(ready.tmp, hub=False, extra=[member("./data/hub.sqlite", body=b"db")])
    assert ready(*move_args(ready, "--import-data", archive))["error"] == "bad_archive"


def test_not_an_archive_or_not_private(ready):
    junk = ready.tmp / "junk.tar.gz"
    junk.write_bytes(b"not a tar at all" * 100)
    os.chmod(junk, 0o600)
    assert ready(*move_args(ready, "--import-data", str(junk)))["error"] == "bad_archive"
    loose, _ = data_tar(ready.tmp, name="loose.tar.gz", mode=0o644)
    assert ready(*move_args(ready, "--import-data", loose))["error"] == "secret_file"
    link = ready.tmp / "link.tar.gz"
    link.symlink_to(data_tar(ready.tmp)[0])
    assert ready(*move_args(ready, "--import-data", str(link)))["error"] == "bad_archive"
    assert ready(*move_args(ready, "--import-data", str(ready.tmp / "missing.tar")))["error"] == "bad_archive"
    assert not (ready.root / "companies" / "ticoteam").exists()


def test_an_import_whose_add_fails_later_leaves_nothing(ready):
    ready(*add_args(ready, "boatyard"))
    archive, _ = data_tar(ready.tmp)
    answer = ready(*move_args(ready, "--alias", "boatyard.tico.team", "--import-data", archive))
    assert answer["error"] == "domain_taken"
    assert not (ready.root / "companies" / "ticoteam").exists()


def test_a_whole_move_is_idempotent(tunneled):
    archive, _ = data_tar(tunneled.tmp)
    env_file = secret_file(tunneled.tmp, "old.env", CARRIED)
    aliases = ["--alias", "runner.tico.team", "--alias", "hub.ticofiles.com"]
    first = tunneled(*move_args(tunneled, *aliases, "--env-file", env_file, "--import-data", archive,
                                "--company-name", "Tico Team", "--signin-label", "Google"))
    assert first["ok"] and first["imported"] is True
    d = tunneled.root / "companies" / "ticoteam"
    files = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}
    n = len(tunneled.system.calls)
    second = tunneled(*move_args(tunneled, *aliases, "--env-file", env_file, "--company-name", "Tico Team",
                                 "--signin-label", "Google"))
    assert second["changed"] is False and second["ignored"] == first["ignored"]
    assert {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()} == files
    assert restarts(tunneled, n) == 0
    env = envfile.parse((d / ".env").read_text())
    assert env["TICO_TEAM_NAME"] == "Tico Team" and env["TICO_OIDC_LABEL"] == "Google"
