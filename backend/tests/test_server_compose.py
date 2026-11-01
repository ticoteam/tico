"""The server compose file pins its updater to the release, and an update leaves the updater running."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]

STORAGE_KEYS = {"TICO_BLOB_BUCKET", "TICO_BLOB_REGION", "TICO_BLOB_ENDPOINT", "TICO_UPLOAD_MAX_BYTES",
                "TICO_BLOB_ACCESS_KEY_ID", "TICO_BLOB_SECRET_ACCESS_KEY"}
SERVER_ONLY_KEYS = STORAGE_KEYS | {"TICO_INBOUND_KEY", "TICO_INBOUND_URL"}


def test_server_forwards_private_settings_only_to_server_in_checkout_and_release_bundle():
    import io
    import tarfile
    import yaml
    from scripts.build_install_bundle import build_bundle

    with tarfile.open(fileobj=io.BytesIO(build_bundle(ROOT, "v0.3.9")), mode="r:gz") as bundle:
        bundled_compose = bundle.extractfile("compose.yaml").read()
        assert bundled_compose == (ROOT / "compose.yaml").read_bytes()
        assert bundle.extractfile(".env.example").read().count(b"TICO_BLOB_BUCKET=") == 1
    for content in ((ROOT / "compose.yaml").read_text(), bundled_compose):
        services = yaml.safe_load(content)["services"]
        environment = services["server"]["environment"]
        assert SERVER_ONLY_KEYS <= set(environment)
        assert all(environment[key] is None for key in SERVER_ONLY_KEYS)
        assert {"LITESTREAM_ACCESS_KEY_ID", "LITESTREAM_SECRET_ACCESS_KEY"} <= set(environment)
        assert not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}.intersection(environment)
        assert "CLOUDFLARE_TUNNEL_TOKEN" not in environment
        for name, service in services.items():
            if name != "server":
                assert not SERVER_ONLY_KEYS.intersection(service.get("environment", {}))
    for service in yaml.safe_load((ROOT / "docker/runner.compose.yaml").read_text())["services"].values():
        assert not SERVER_ONLY_KEYS.intersection(service.get("environment", {}))


def test_server_gets_the_support_switches_set_in_env():
    import yaml
    environment = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["server"]["environment"]
    assert {"TICO_SUPPORT", "TICO_SUPPORT_ACCESS"} <= set(environment)


def test_a_server_update_recreates_only_the_server_never_the_updater(monkeypatch, tmp_path):
    for key, value in {"TICO_UPDATER_MODE": "", "TICO_PROJECT_DIR": str(tmp_path), "TICO_COMPOSE_FILE": "", "TICO_UPDATER_BUNDLE": "never"}.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("tico_updater_server_pin", ROOT / "docker/updater.py")
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    calls = []
    monkeypatch.setattr(updater, "compose", lambda *a, tag=None, **k: calls.append(a) or "")
    docker_calls = []
    def docker_run(argv, **kwargs):
        docker_calls.append(tuple(argv))
        if argv[:3] == ["docker", "image", "ls"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["docker", "inspect"]:
            raise OSError("the fixture has no updater container")
        raise AssertionError("unexpected Docker mutation")
    monkeypatch.setattr(updater, "docker_run", docker_run)
    monkeypatch.setattr(updater, "running_image", lambda: ("sha256:old", "v0.1.0"))
    monkeypatch.setattr(updater, "healthy", lambda seconds: True)
    monkeypatch.setattr(updater, "pinned_services", lambda version: [])
    monkeypatch.setattr(updater, "check_switched", lambda version, release: None)
    (tmp_path / ".env").write_text("TICO_URL=x\n")
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy"
    # Discovering an existing front door reads config; all mutations still touch only the server here.
    assert ("config", "--services") in calls
    assert all(a == ("config", "--services") or (a[0] in {"pull", "exec", "up"} and "server" in a)
               for a in calls) and not any("updater" in a for a in calls)
    assert docker_calls and all(a[:3] == ("docker", "image", "ls") or a[:2] == ("docker", "inspect")
                                for a in docker_calls)
    assert "TICO_TAG=v0.2.0" in (tmp_path / ".env").read_text()


def test_the_slack_gateway_gets_every_sign_in_setting_the_server_gets():
    import yaml
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    auth = {"TICO_AUTH_PROXY", "TICO_ACCESS_ISSUER", "TICO_ACCESS_AUDIENCE", "TICO_OIDC_ISSUER", "TICO_OIDC_CLIENT_ID",
            "TICO_OIDC_CLIENT_SECRET", "TICO_OIDC_ALLOWED_DOMAINS"}
    assert auth <= set(services["server"]["environment"])
    assert auth <= set(services["slack"]["environment"])
    for name in ("server", "slack"):
        assert all(services[name]["environment"][key] is None for key in ("TICO_ACCESS_ISSUER", "TICO_ACCESS_AUDIENCE")), \
            "Cloudflare issuer and audience pass through the operator's environment"



def test_the_server_and_the_slack_gateway_get_the_hosted_mark_as_set_in_env():
    # Compose passes settings only through each service's list, so without these entries a hosted Tico's server
    # reads TICO_HOSTED as unset and its sign-in offers the terminal again (ui/model-login.js). Both load Settings.
    import yaml
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    for name in ("server", "slack"):
        assert "TICO_HOSTED" in services[name]["environment"], name
        assert services[name]["environment"]["TICO_HOSTED"] is None, f"{name} takes it from .env as set, no default"

def test_the_server_and_the_slack_gateway_get_the_decision_model_keys_but_not_other_services_secrets():
    import yaml
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    keys = {"TYPESAFE_API_KEY", "TICO_TYPESAFE_SECRET_ARN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            "XAI_API_KEY", "OPENROUTER_API_KEY"}
    for name in ("server", "slack"):
        env = set(services[name]["environment"])
        assert keys <= env and "CLOUDFLARE_TUNNEL_TOKEN" not in env


def test_front_doors_have_fixed_edge_addresses_the_server_trusts_and_nothing_wider():
    import re
    import yaml
    from ipaddress import ip_address, ip_network
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = compose["services"]
    default = lambda value: re.fullmatch(r"\$\{\w+:-(.*)\}", value).group(1)
    ipam = compose["networks"]["edge"]["ipam"]["config"][0]
    subnet, dynamic = ip_network(default(ipam["subnet"])), ip_network(default(ipam["ip_range"]))
    doors = {}
    for name in ("caddy", "cloudflared"):
        assert list(services[name]["networks"]) == ["edge"]       # reached only over the edge network
        doors[name] = ip_address(default(services[name]["networks"]["edge"]["ipv4_address"]))
        assert doors[name] in subnet and doors[name] not in dynamic
    assert "edge" in services["server"]["networks"] and "default" in services["server"]["networks"]
    # Only the server and the tunnel's hold draw from the dynamic range; everything else on the edge network is fixed.
    edge = {name for name, service in services.items() if "edge" in service.get("networks", ())}
    assert edge == {"server", "caddy", "cloudflared", "hold"} and services["hold"]["networks"] == ["edge"]
    trusted = services["server"]["environment"]["TICO_TRUSTED_PROXIES"]
    resolved = re.sub(r"\$\{\w+:-([^}$]*)\}", r"\1", trusted[len("${TICO_TRUSTED_PROXIES:-"):-1])
    assert resolved.split(",") == ["127.0.0.1/32", "::1/128", f"{doors['caddy']}/32", f"{doors['cloudflared']}/32", str(dynamic)]
    assert "${TICO_EDGE_DYNAMIC_RANGE:-" in trusted   # a moved edge network moves the trusted range with it


def test_the_server_gets_every_setting_that_turns_a_team_rule_default_on():
    # A rule's env default (team_rules.ENV_DEFAULTS) only works if compose forwards it: the server sees just the
    # keys listed under its environment, and an update replaces compose.yaml but never .env.
    import yaml
    from backend import team_rules
    environment = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["server"]["environment"]
    assert set(team_rules.ENV_DEFAULTS.values()) <= set(environment)
    assert all(environment[key] is None for key in team_rules.ENV_DEFAULTS.values())


def test_the_server_gets_the_visual_settings():
    # Without them forwarded, setting the visual origin in .env or SSM does nothing and production stays on the
    # fallback (Dev 245, Dev 313). The retired same-address opt-in is not passed on.
    import yaml
    environment = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["server"]["environment"]
    for key in ("TICO_VISUAL_ORIGIN", "TICO_VISUAL_SECRET"):
        assert key in environment and environment[key] is None, key
    assert "TICO_VISUAL_SAME_ORIGIN" not in environment


def test_the_server_reads_a_starter_plan_file_from_a_read_only_folder_setup_fills():
    import yaml
    server = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["server"]
    assert server["environment"]["TICO_COMPANY_FILE"] is None and server["environment"]["TICO_COMPANY_FILE_B64"] is None
    from setup import contract, settings
    (mount,) = [v for v in server["volumes"] if v.endswith(f":{contract.COMPANY_FILE_MOUNT}:ro")]

    def source(env):
        # Compose's ${VAR:-default}, as the updater runs it: --project-directory /project, the install's .env.
        import re
        return re.sub(r"\$\{(\w+):-([^}]*)\}", lambda m: env.get(m.group(1)) or m.group(2), mount).split(":")[0]
    aws = settings.Settings(target="aws", domain="tico.acme.example", company="Acme", owner_email="o@acme.example")
    assert source(aws.to_env()) == f"{contract.REMOTE_DIR}/{contract.COMPANY_FILE_DIR}"   # the host folder pull-env.sh fills
    assert source({}) == "tico-company-file" and "tico-company-file" in yaml.safe_load((ROOT / "compose.yaml").read_text())["volumes"]


def test_a_runner_sharing_the_host_yields_cpu_and_memory_to_the_server_and_its_front_doors():
    import yaml
    server = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    runner = yaml.safe_load((ROOT / "docker/runner.compose.yaml").read_text())["services"]["runner"]
    for name in ("server", "caddy", "cloudflared", "hold"):
        assert server[name]["cpu_shares"] >= 4 * runner["cpu_shares"]
        assert server[name].get("oom_score_adj", 0) >= 0     # a negative score is refused by rootless Docker
    assert runner["oom_score_adj"] > 0
    assert runner["mem_limit"] == runner["memswap_limit"] == "${TICO_RUNNER_MEMORY:-0}"   # the cap, with no swap beyond it


def hold_caddyfile(services, **env):
    """The hold's Caddyfile as Compose hands it to the container: ${VAR:-default} filled from `env`."""
    import re
    text = services["hold"]["environment"]["TICO_HOLD_CADDYFILE"]
    return re.sub(r"\$\{(\w+):-([^}]*)\}", lambda m: env.get(m.group(1)) or m.group(2), text)


def test_the_tunnel_goes_through_a_hold_that_is_up_through_every_server_restart():
    import yaml
    from backend import caddy_assets
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    hold, tunnel, caddy = services["hold"], services["cloudflared"], services["caddy"]
    assert hold["profiles"] == ["cloudflared"] and hold["image"] == caddy["image"]   # one pinned image, already vetted
    # Nothing that changes with a release: no server dependency, no Tico image, no bundle file, no published port.
    assert "depends_on" not in hold and "volumes" not in hold and "ports" not in hold and "${" not in hold["image"]
    assert hold["read_only"] and hold["cap_drop"] == ["ALL"] and hold["cap_add"] == ["NET_BIND_SERVICE"]
    assert "$$TICO_HOLD_CADDYFILE" in hold["entrypoint"][-1]   # Compose leaves $$ as one $ for the shell
    # cloudflared starts after it (and so is recreated after it on the update that adds it), on the file that names it.
    assert tunnel["depends_on"]["hold"] == {"condition": "service_started"}
    assert tunnel["depends_on"]["server"] == {"condition": "service_healthy"}
    config = hold_caddyfile(services)
    assert "http://:8080 {" in config and "reverse_proxy server:8765 {" in config and "admin off" in config
    assert f"lb_try_duration {caddy_assets.HOLD_FOR}" in config and f"lb_try_interval {caddy_assets.HOLD_EVERY}" in config
    assert "header Retry-After 5" in config and f'respond "{caddy_assets.HOLD_BODY}" 503' in config
    assert "flush_interval" not in config and "retry_match" not in config   # Caddy's own streaming and GET-only rules
    # It believes forwarded addresses only from cloudflared, wherever .env moves it.
    assert "trusted_proxies static 172.30.254.3/32" in config
    assert "trusted_proxies static 10.9.8.3/32" in hold_caddyfile(services, TICO_CLOUDFLARED_ADDRESS="10.9.8.3")


@pytest.mark.slow
def test_the_hold_holds_a_write_through_a_restart_streams_and_keeps_the_visitor_address(tmp_path):
    """The compose hold's own Caddyfile under the pinned Caddy, pointed at a stand-in server on loopback."""
    import http.client
    import os
    import socket
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import yaml
    from backend import caddy_assets
    binary = os.environ.get("TICO_TEST_CADDY", "")
    if not binary or not Path(binary).is_file():
        pytest.skip("set TICO_TEST_CADDY to the pinned official Caddy 2.11.4 binary")
    calls, more = [], threading.Event()
    class Server(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(("GET", {k.lower(): v for k, v in self.headers.items()}, b""))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: one\n\n")
            self.wfile.flush()
            more.wait(5)
            self.wfile.write(b"data: two\n\n")
        def do_POST(self):
            calls.append(("POST", {}, self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
    def start(address=("127.0.0.1", 0)):
        server = ThreadingHTTPServer(address, Server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    def stop(server):
        server.shutdown()
        server.server_close()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = start()
    config = hold_caddyfile(yaml.safe_load((ROOT / "compose.yaml").read_text())["services"])
    for old, new in (("http://:8080 {", f"http://:{port} {{\n  bind 127.0.0.1"), ("server:8765", "127.0.0.1:%d" % server.server_port),
                     ("172.30.254.3/32", "127.0.0.1/32"), (f"lb_try_duration {caddy_assets.HOLD_FOR}", "lb_try_duration 1s")):
        assert config.count(old) == 1, old
        config = config.replace(old, new)
    (tmp_path / "Caddyfile").write_text(config)
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_DATA_HOME": str(tmp_path / "data")}
    proc = subprocess.Popen([binary, "run", "--config", str(tmp_path / "Caddyfile"), "--adapter", "caddyfile"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def connect():
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        until = time.monotonic() + 5
        while True:
            try:
                connection.connect()
                return connection
            except OSError:
                if time.monotonic() > until:
                    raise
                time.sleep(.05)
    try:
        # The visitor's address and scheme, as cloudflared forwards them, reach the server; it streams as it goes.
        connection = connect()
        connection.request("GET", "/events", headers={"Host": "tico.example.com", "X-Forwarded-For": "203.0.113.7",
                                                      "X-Forwarded-Proto": "https", "Cf-Connecting-Ip": "203.0.113.7"})
        response = connection.getresponse()
        assert response.status == 200 and response.readline() == b"data: one\n"   # before the server sends the rest
        more.set()
        assert response.read() == b"\ndata: two\n\n"
        connection.close()
        headers = calls[-1][1]
        assert headers["host"] == "tico.example.com" and headers["x-forwarded-proto"] == "https"
        assert headers["x-forwarded-for"] == "203.0.113.7, 127.0.0.1" and headers["cf-connecting-ip"] == "203.0.113.7"
        # A restart: a write sent while nothing listens waits and is answered by the server that comes back.
        address = server.server_address
        stop(server)
        answer = {}
        def post():
            connection = connect()
            connection.request("POST", "/api/v2/tasks", body=b"held", headers={"Host": "tico.example.com"})
            response = connection.getresponse()
            answer.update(status=response.status, body=response.read())
        sender = threading.Thread(target=post)
        sender.start()
        time.sleep(.4)
        server = start(address)
        sender.join(5)
        assert answer == {"status": 200, "body": b"ok"} and calls[-1] == ("POST", {}, b"held")
        # Down for longer than the hold: a plain 503 with Retry-After, never a 502.
        stop(server)
        server = None
        connection = connect()
        connection.request("GET", "/", headers={"Host": "tico.example.com"})
        response = connection.getresponse()
        assert (response.status, response.getheader("Retry-After"), response.read().decode()) == (503, "5", caddy_assets.HOLD_BODY)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        if server:
            stop(server)
