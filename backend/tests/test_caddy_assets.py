"""The optional asset front door must never become another app/authentication route."""
import copy
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time

import pytest

from backend import caddy_assets as assets
from backend.config import ROOT


APP, VISUAL, RUNNER = "acme.example", "files.acme.example", "runner.acme.example"
TOKEN = "c3ludGhldGljLWZpbGU" + "." + "a" * 43
SIGNED_PATH = "/visuals/" + TOKEN
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


def hosts(config):
    return [host for server in config["apps"]["http"]["servers"].values()
            for route in server["routes"] for match in route.get("match", ()) for host in match.get("host", ())]


def test_external_origin_does_not_enable_local_serving_and_marker_is_private_and_deterministic():
    config = assets.render(APP, "https://" + VISUAL, RUNNER)
    assert config == assets.render(APP, "https://cdn.external.example/unchanged", RUNNER)
    assert hosts(config) == [APP] and config["admin"] == {"disabled": True}
    public = config["apps"]["http"]["servers"]["tico"]
    assert public["routes"][0]["handle"][0] == {
        "handler": "reverse_proxy", "upstreams": [{"dial": "server:8765"}]}
    assert "logs" not in public and public["routes"][-1]["handle"][0]["status_code"] == 404
    base = copy.deepcopy(config)
    ready = base["apps"]["http"]["servers"].pop(assets.READY_SERVER)
    digest = hashlib.sha256(json.dumps(base, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    marker = assets.MARKER_PREFIX + digest
    assert ready["listen"] == ["127.0.0.1:20203"] and ready["automatic_https"] == {"disable": True}
    assert ready["routes"][0]["handle"][0]["body"] == marker
    assert json.dumps(config).count(marker) == 1
    # Existing primary-domain handling permits local/IP app front doors; the
    # stricter asset-host rule must not disable those app-only installations.
    for domain in ("localhost", "127.0.0.1", "10.0.0.12"):
        assert hosts(assets.render(domain)) == [domain]


def test_literal_hosts_cannot_inject_config_or_share_the_visual_security_boundary():
    for app in ("*.acme.example", "https://acme.example", "acme.example:443", "acme.example\nother"):
        with pytest.raises(ValueError, match="hostname"):
            assets.render(app, "https://" + VISUAL, serve_visuals=True)
    with pytest.raises(ValueError, match="collision"):
        assets.render(APP, runner_hostname=APP)
    for origin in ("https://" + APP, "https://" + RUNNER, "https://user:private@" + VISUAL,
                   "https://@" + VISUAL, "https://" + VISUAL + "/api", "https://" + VISUAL + "?",
                   "https://" + VISUAL + ":8443", "https://" + VISUAL + ".", "https://127.0.0.1"):
        assert hosts(assets.render(APP, origin, RUNNER, serve_visuals=True)) == [APP]
    assert hosts(assets.render(APP, "https://Files.Acme.Example:443/\r", RUNNER, serve_visuals=True)) == [APP, VISUAL]


def test_prepare_atomically_removes_invalid_disabled_and_absent_app_sites(tmp_path, caplog):
    env = {"TICO_CADDY_DIR": str(tmp_path), "TICO_DOMAIN": APP, "TICO_CADDY_VISUALS": "1",
           "TICO_VISUAL_ORIGIN": "https://" + VISUAL, "TICO_RUNNER_URL": "https://" + RUNNER}
    result = assets.prepare(env)
    path = tmp_path / "caddy.json"
    original_inode = path.stat().st_ino
    assert result["written"] and result["serve_visuals"] and hosts(json.loads(path.read_bytes())) == [APP, VISUAL]
    assert path.stat().st_mode & 0o777 == 0o644
    secret = "synthetic-private-recipient"
    result = assets.prepare({**env, "TICO_VISUAL_ORIGIN": "https://" + secret + "@" + VISUAL})
    assert not result["serve_visuals"] and result["reason"] == "visual_origin_not_https_origin"
    assert path.stat().st_ino != original_inode and hosts(json.loads(path.read_bytes())) == [APP]
    assert secret not in caplog.text and "disabled" in caplog.text
    assert not assets.prepare({**env, "TICO_CADDY_VISUALS": "true"})["serve_visuals"]
    assets.prepare({**env, "TICO_CADDY_VISUALS": "0", "TICO_DOMAIN": ""})
    assert set(json.loads(path.read_bytes())["apps"]["http"]["servers"]) == {assets.READY_SERVER}
    assert not list(tmp_path.glob(".caddy-*"))


def test_absent_optional_mount_is_inert_but_explicit_serving_needs_required_configuration(tmp_path):
    missing = tmp_path / "unmounted"
    assert not assets.prepare({"TICO_CADDY_DIR": str(missing)})["written"]
    assert not missing.exists()
    with pytest.raises(ValueError, match="directory_missing"):
        assets.prepare({"TICO_CADDY_DIR": str(missing), "TICO_CADDY_VISUALS": "1", "TICO_DOMAIN": APP})
    with pytest.raises(ValueError, match="app_hostname_required"):
        assets.prepare({"TICO_CADDY_DIR": str(tmp_path), "TICO_CADDY_VISUALS": "1"})
    assert not list(tmp_path.iterdir())


def test_unwritable_unmounted_directory_is_inert_while_off_but_refuses_explicit_serving(tmp_path):
    # An older compose mounts no volume, so the image's directory sits under the read-only root.
    directory = tmp_path / "caddy-generated"
    directory.mkdir()
    directory.chmod(0o555)
    try:
        assert not os.access(directory, os.W_OK)
        for env in ({"TICO_CADDY_DIR": str(directory)},
                    {"TICO_CADDY_DIR": str(directory), "TICO_DOMAIN": APP, "TICO_CADDY_VISUALS": "0"}):
            assert assets.prepare(env) == {"written": False, "path": None, "generation": None,
                                           "serve_visuals": False, "reason": None}
        with pytest.raises(RuntimeError, match="^caddy_config_write_failed$"):
            assets.prepare({"TICO_CADDY_DIR": str(directory), "TICO_DOMAIN": APP, "TICO_CADDY_VISUALS": "1",
                            "TICO_VISUAL_ORIGIN": "https://" + VISUAL})
        assert not list(directory.iterdir())
        # The entrypoint runs under set -euo pipefail, so its exit status decides whether the server starts.
        run = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
               "PYTHONPATH": str(ROOT), "TICO_CADDY_DIR": str(directory), "TICO_DOMAIN": APP}
        run.pop("TICO_CADDY_VISUALS", None)
        assert subprocess.run(["bash", str(ENTRYPOINT), "caddy-config"], env=run, capture_output=True,
                              timeout=60).returncode == 0
        refused = subprocess.run(["bash", str(ENTRYPOINT), "caddy-config"], env={**run, "TICO_CADDY_VISUALS": "1"},
                                 capture_output=True, text=True, timeout=60)
        assert refused.returncode != 0 and "caddy_config_write_failed" in refused.stderr
        assert not list(directory.iterdir())
    finally:
        directory.chmod(0o755)
    result = assets.prepare({"TICO_CADDY_DIR": str(directory), "TICO_DOMAIN": APP})
    assert result["written"] and result["path"] == str(directory / "caddy.json")
    assert hosts(json.loads((directory / "caddy.json").read_bytes())) == [APP]
    # An old generation that serves visuals cannot be retired from an unwritable directory, so off must refuse.
    assets.prepare({"TICO_CADDY_DIR": str(directory), "TICO_DOMAIN": APP, "TICO_CADDY_VISUALS": "1",
                    "TICO_VISUAL_ORIGIN": "https://" + VISUAL})
    old = (directory / "caddy.json").read_bytes()
    assert hosts(json.loads(old)) == [APP, VISUAL]
    directory.chmod(0o555)
    try:
        with pytest.raises(RuntimeError, match="^caddy_config_write_failed$"):
            assets.prepare({"TICO_CADDY_DIR": str(directory), "TICO_DOMAIN": APP})
        assert subprocess.run(["bash", str(ENTRYPOINT), "caddy-config"], env=run, capture_output=True,
                              timeout=60).returncode != 0
        assert (directory / "caddy.json").read_bytes() == old
    finally:
        directory.chmod(0o755)


def test_runner_collision_validation_preserves_canonical_runner_and_refuses_ambiguous_optional_url(tmp_path, caplog):
    env = {"TICO_CADDY_DIR": str(tmp_path), "TICO_DOMAIN": APP, "TICO_CADDY_VISUALS": "1",
           "TICO_VISUAL_ORIGIN": "https://" + VISUAL}
    assert assets.prepare({**env, "TICO_RUNNER_URL": "https://" + APP})["serve_visuals"]
    for runner_url in ("http://127.0.0.1:8765", "http://[::1]:8765"):
        assert assets.prepare({**env, "TICO_RUNNER_URL": runner_url})["serve_visuals"]
        assert hosts(json.loads((tmp_path / "caddy.json").read_bytes())) == [APP, VISUAL]
    assert not assets.prepare({**env, "TICO_RUNNER_URL": "https://" + VISUAL})["serve_visuals"]
    marker = "synthetic-private-runner"
    result = assets.prepare({**env, "TICO_RUNNER_URL": "https://" + marker + "@" + RUNNER})
    assert result["reason"] == "runner_url_invalid" and not result["serve_visuals"]
    assert marker not in caplog.text
    assert hosts(json.loads((tmp_path / "caddy.json").read_bytes())) == [APP]


def test_failed_atomic_replacement_keeps_complete_old_file_and_cleans_temporary(tmp_path, monkeypatch):
    env = {"TICO_CADDY_DIR": str(tmp_path), "TICO_DOMAIN": APP}
    assets.prepare(env)
    path = tmp_path / "caddy.json"
    old = path.read_bytes()
    def fail(*args):
        raise OSError("synthetic-private-storage-detail")
    monkeypatch.setattr(assets.os, "replace", fail)
    with pytest.raises(RuntimeError, match="^caddy_config_write_failed$") as error:
        assets.prepare({**env, "TICO_CADDY_VISUALS": "1", "TICO_VISUAL_ORIGIN": "https://" + VISUAL})
    assert "synthetic-private" not in str(error.value)
    assert path.read_bytes() == old and not list(tmp_path.glob(".caddy-*"))


@pytest.mark.slow
def test_pinned_caddy_routes_exact_signed_reads_strips_auth_and_does_not_log_tokens(tmp_path):
    """Loopback HTTP mechanics only; production DNS/TLS and signed authorization are separate."""
    binary = os.environ.get("TICO_TEST_CADDY", "")
    if not binary or not Path(binary).is_file():
        pytest.skip("set TICO_TEST_CADDY to the pinned official Caddy 2.11.4 binary")
    version = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=5)
    assert version.returncode == 0 and re.match(r"v2[.]11[.]4(?:\s|$)", version.stdout)
    calls = []
    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self):
            calls.append((self.command, self.path, {key.lower(): value for key, value in self.headers.items()}))
            ranged = self.headers.get("Range") == "bytes=2-4"
            body = b"234" if ranged else b"0123456789"
            self.send_response(206 if ranged else 200)
            for key, value in {"Set-Cookie": "synthetic=not-for-asset", "Content-Length": str(len(body)),
                               "Cache-Control": "private, no-cache", "Content-Security-Policy": "sandbox; default-src 'none'",
                               "Accept-Ranges": "bytes", "ETag": '"synthetic-etag"'}.items():
                self.send_header(key, value)
            if ranged:
                self.send_header("Content-Range", "bytes 2-4/10")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        do_GET = do_HEAD = do_POST = do_OPTIONS = reply

    def free_port():
        with socket.socket() as handle:
            handle.bind(("127.0.0.1", 0))
            return handle.getsockname()[1]

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    port, ready_port = free_port(), free_port()
    config = assets.render(APP, "https://" + VISUAL, RUNNER, serve_visuals=True)
    web = config["apps"]["http"]["servers"]["tico"]
    web.update(listen=["127.0.0.1:" + str(port)], automatic_https={"disable": True})
    web.pop("tls_connection_policies")  # This fixture never contacts ACME or binds public TLS.
    ready = config["apps"]["http"]["servers"][assets.READY_SERVER]
    ready["listen"] = ["127.0.0.1:" + str(ready_port)]
    marker = ready["routes"][0]["handle"][0]["body"]
    for route in web["routes"]:
        for handler in route["handle"]:
            if handler["handler"] == "reverse_proxy":
                handler["upstreams"] = [{"dial": "127.0.0.1:" + str(backend.server_port)}]
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "XDG_DATA_HOME": str(tmp_path / "data")}
    path, logs = tmp_path / "caddy.json", tmp_path / "caddy.log"
    path.write_text(json.dumps(config))
    proc, stopped = None, False
    def request(host, path="/", method="GET", headers=None, endpoint=port):
        connection = http.client.HTTPConnection("127.0.0.1", endpoint, timeout=2)
        try:
            connection.request(method, path, headers={"Host": host, **(headers or {})})
            response = connection.getresponse()
            return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
        finally:
            connection.close()
    def wait_for(check):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            try:
                if check():
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.025)
        raise AssertionError("Caddy fixture transition did not complete")
    try:
        validation = subprocess.run([binary, "validate", "--config", str(path)], env=env,
                                    capture_output=True, text=True, timeout=15)
        (tmp_path / "validate.log").write_text(validation.stdout + validation.stderr)
        assert validation.returncode == 0, validation.stderr
        with logs.open("w") as output:
            proc = subprocess.Popen([binary, "run", "--config", str(path)], env=env, stdout=output, stderr=subprocess.STDOUT)
            wait_for(lambda: request("127.0.0.1", assets.READY_PATH, endpoint=ready_port)[2] == marker.encode())
            for suffix, method in (("?", "GET"), ("", "HEAD"), ("", "POST")):
                assert request("127.0.0.1", assets.READY_PATH + suffix, method, endpoint=ready_port)[0] == 404
            status, headers, body = request(APP, "/api/v2/config", headers={"Cookie": "synthetic=app", "Authorization": "Bearer app"})
            assert status == 200 and calls[-1][2]["cookie"] == "synthetic=app" and calls[-1][2]["authorization"] == "Bearer app"
            auth = {"Cookie": "synthetic=asset", "Authorization": "Bearer synthetic", "Proxy-Authorization": "Basic synthetic",
                    "Cf-Access-Jwt-Assertion": "synthetic", "X-Amzn-Oidc-Data": "synthetic", "X-Tico-Assistant-Action": "synthetic",
                    "Range": "bytes=2-4", "If-Range": '"synthetic-etag"'}
            status, headers, body = request(VISUAL, SIGNED_PATH, headers=auth)
            assert status == 206 and body == b"234" and headers["content-range"] == "bytes 2-4/10"
            assert all(key.lower() not in calls[-1][2] for key in auth if key not in ("Range", "If-Range"))
            assert calls[-1][2]["range"] == "bytes=2-4" and calls[-1][2]["if-range"] == '"synthetic-etag"'
            assert "set-cookie" not in headers and headers["cache-control"] == "private, no-cache"
            assert headers["content-security-policy"] == "sandbox; default-src 'none'" and headers["etag"] == '"synthetic-etag"'
            assert request(VISUAL, SIGNED_PATH, "HEAD")[2] == b"" and calls[-1][0] == "HEAD"
            before = len(calls)
            bad = ("/api/v2/config", "/auth/login", "/visuals/x/../" + TOKEN,
                   "/visuals/%2e%2e/" + TOKEN, SIGNED_PATH + "?", SIGNED_PATH + "?x=1",
                   SIGNED_PATH.replace(".", "%2e"), SIGNED_PATH + "/extra", "/visuals//" + TOKEN,
                   SIGNED_PATH[:-1], SIGNED_PATH + "x", SIGNED_PATH + "%2f..%2fapi")
            for raw_path in bad:
                assert request(VISUAL, raw_path)[0] == 404
            for method in ("POST", "OPTIONS"):
                assert request(VISUAL, SIGNED_PATH, method)[0] == 404
            assert request("unrelated.example", SIGNED_PATH)[0] == 404
            assert request(VISUAL, assets.READY_PATH)[0] == 404 and len(calls) == before
            backend.shutdown()
            backend.server_close()
            stopped = True
            # Failed proxy diagnostics used to include request.uri and Referer.
            # Require an actual error log while proving both token carriers gone.
            assert request(VISUAL, SIGNED_PATH, headers={"Referer": "https://" + VISUAL + SIGNED_PATH})[0] == 502
            wait_for(lambda: '"logger":"http.log.error"' in logs.read_text())
            error_lines = [json.loads(line) for line in logs.read_text().splitlines() if '"logger":"http.log.error"' in line]
            assert error_lines and all("request" not in line for line in error_lines)
            assert TOKEN not in logs.read_text() and SIGNED_PATH not in logs.read_text()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if not stopped:
            backend.shutdown()
            backend.server_close()
        thread.join(timeout=2)
