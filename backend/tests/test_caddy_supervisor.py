"""Actual pinned Caddy lifecycle, isolated HTTP listeners and no live install/ACME/DB."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

import pytest

pytestmark = pytest.mark.slow
SCRIPT = Path(__file__).resolve().parents[2] / "docker/caddy-supervisor.sh"
APP, ASSET, NEXT_ASSET = "acme.example", "acme.files.example", "other.files.example"


def until(check, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Caddy lifecycle condition did not settle")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class Runtime:
    def __init__(self, root, binary):
        self.root, self.config, self.state = root, root / "caddy.json", root / "state"
        self.public_port, self.ready_port = port(), port()
        while self.ready_port == self.public_port:
            self.ready_port = port()
        self.env = {**os.environ, "TICO_CADDY_CONFIG": str(self.config), "TICO_CADDY_STATE_DIR": str(self.state),
                    "TICO_CADDY_BIN": binary, "TICO_CADDY_READY_PORT": str(self.ready_port),
                    "TICO_CADDY_READY_TICKS": "2", "TICO_CADDY_STOP_TICKS": "2",
                    "TICO_CADDY_VALIDATE_TICKS": "3", "XDG_DATA_HOME": str(root / "data"),
                    "XDG_CONFIG_HOME": str(root / "config")}
        if not shutil.which("wget"):
            # Darwin has no BusyBox/wget here. This adapter makes a real HTTP request, never a fake ACK.
            curl = shutil.which("curl")
            if not curl:
                pytest.skip("wget or curl is required for the real local readiness probe")
            tools = root / "tools"
            tools.mkdir()
            adapter = tools / "wget"
            # The product invocation has six arguments; keep the adapter shape strict.
            adapter.write_text('#!/bin/sh\n[ "$#" -eq 6 ] || exit 64\n'
                               '[ "$1 $2 $3 $4 $5" = "-q -T 1 -O -" ] || exit 64\n'
                               'exec ' + curl + ' --noproxy "*" -fsS --max-time 1 --max-redirs 0 "$6"\n')
            adapter.chmod(0o700)
            self.env["PATH"] = str(tools) + os.pathsep + self.env["PATH"]
        self.process = None
        self.log = (root / "supervisor.log").open("wb")

    def configuration(self, asset=ASSET, *, label="one", ready_path="/__tico_caddy_ready"):
        routes = [{"match": [{"host": [APP]}], "handle": [{"handler": "static_response", "body": label}]}]
        if asset:
            routes.append({"match": [{"host": [asset]}], "handle": [{"handler": "static_response", "body": "asset " + label}]})
        routes.append({"handle": [{"handler": "static_response", "status_code": 404}]})
        value = {"admin": {"disabled": True}, "apps": {"http": {"servers": {"fixture": {
            "listen": ["127.0.0.1:" + str(self.public_port)], "automatic_https": {"disable": True}, "routes": routes}}}}}
        generation = hashlib.sha256(encoded(value)).hexdigest()
        value["apps"]["http"]["servers"]["tico_ready"] = {
            "listen": ["127.0.0.1:" + str(self.ready_port)], "automatic_https": {"disable": True}, "routes": [
                {"match": [{"path": [ready_path], "method": ["GET"]}],
                 "handle": [{"handler": "static_response", "body": "tico-caddy-generation:" + generation}]},
                {"handle": [{"handler": "static_response", "status_code": 404}]}]}
        return value, generation

    def replace(self, value):
        raw = encoded(value) + b"\n" if isinstance(value, dict) else value
        temporary = self.root / "config.new"
        temporary.write_bytes(raw)
        temporary.replace(self.config)
        return hashlib.sha256(raw).hexdigest()

    def start(self):
        self.process = subprocess.Popen(["/bin/sh", str(SCRIPT)], env=self.env, stdout=self.log, stderr=self.log,
                                        start_new_session=True)

    def proof(self):
        try:
            return json.loads((self.state / "applied").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def applied(self, digest, generation):
        def observed():
            proof = self.proof()
            return proof if proof and proof["file_sha256"] == digest and proof["generation"] == generation else None
        proof = until(observed)
        assert proof["supervisor_pid"] == self.process.pid
        assert self.health() == 0
        return proof

    def failure(self, phase):
        def observed():
            try:
                row = json.loads((self.state / "last-failure").read_text())
                return row if row["phase"] == phase and not self.proof() else None
            except (FileNotFoundError, json.JSONDecodeError):
                return None
        return until(observed)

    def health(self):
        return subprocess.run(["/bin/sh", str(SCRIPT), "health"], env=self.env,
                              capture_output=True, timeout=3).returncode

    def request(self, host):
        connection = http.client.HTTPConnection("127.0.0.1", self.public_port, timeout=0.3)
        try:
            connection.request("GET", "/", headers={"Host": host})
            reply = connection.getresponse()
            return reply.status, reply.read()
        finally:
            connection.close()

    def closed(self):
        try:
            self.request(APP)
        except (OSError, http.client.HTTPException):
            return True
        return False

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # Only this fixture's deliberately isolated process group, never a name/PID search.
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
        self.log.close()


@pytest.fixture
def runtime(tmp_path):
    binary = os.environ.get("TICO_TEST_CADDY") or shutil.which("caddy")
    if not binary:
        pytest.skip("set TICO_TEST_CADDY to the pinned Caddy executable")
    version = subprocess.run([binary, "version"], check=True, capture_output=True, text=True).stdout
    assert version.startswith("v2.11.4 "), version
    instance = Runtime(tmp_path, str(Path(binary).resolve()))
    try:
        yield instance
    finally:
        instance.close()


def test_generation_change_disable_invalid_and_missing_stop_old_routes(runtime):
    value, generation = runtime.configuration()
    runtime.start()  # Missing initial producer file must never invent a listener/proof.
    runtime.failure("missing_config")
    runtime.applied(runtime.replace(value), generation)
    assert runtime.request(ASSET) == (200, b"asset one")
    changed, generation = runtime.configuration(NEXT_ASSET, label="two")
    runtime.applied(runtime.replace(changed), generation)
    assert runtime.request(ASSET)[0] == 404
    assert runtime.request(NEXT_ASSET) == (200, b"asset two")
    app_only, app_generation = runtime.configuration(None)
    runtime.applied(runtime.replace(app_only), app_generation)
    assert runtime.request(APP)[0] == 200 and runtime.request(NEXT_ASSET)[0] == 404
    enabled, generation = runtime.configuration()
    runtime.applied(runtime.replace(enabled), generation)
    secret_url = b"https://acme.files.example/visuals/fixture-secret.signature"
    runtime.replace(encoded(enabled) + b"\n" + secret_url)
    failure = runtime.failure("validate")
    until(runtime.closed)
    assert failure["rc"] != 0 and len(failure["output_sha256"]) == 64
    assert runtime.health() != 0
    runtime.applied(runtime.replace(app_only), app_generation)
    runtime.config.unlink()
    runtime.failure("missing_config")
    until(runtime.closed)
    assert runtime.health() != 0
    runtime.log.flush()
    assert secret_url not in (runtime.root / "supervisor.log").read_bytes()


def test_exact_readiness_and_child_liveness_are_required_for_applied_proof(runtime):
    wrong, _ = runtime.configuration(ready_path="/not-the-ready-path")
    runtime.replace(wrong)
    runtime.start()
    runtime.failure("readiness")
    until(runtime.closed)
    assert runtime.health() != 0
    good, generation = runtime.configuration(label="recovered")
    proof = runtime.applied(runtime.replace(good), generation)
    os.kill(proof["pid"], signal.SIGKILL)  # The child from this fixture's private applied receipt only.
    assert runtime.process.wait(timeout=5) == 1
    assert not runtime.proof() and runtime.health() != 0 and runtime.closed()


def test_term_reaps_its_child_clears_proof_and_leaves_unrelated_process(runtime):
    value, generation = runtime.configuration()
    runtime.replace(value)
    runtime.start()
    runtime.applied(hashlib.sha256(runtime.config.read_bytes()).hexdigest(), generation)
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        runtime.process.terminate()
        assert runtime.process.wait(timeout=6) == 0
        assert not runtime.proof() and runtime.closed()
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)
