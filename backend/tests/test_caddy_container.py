"""Opt-in Linux-container proof of the shipped producer and pinned Caddy supervisor.

Only HTTP fixture listeners are published, on random loopback ports. The backend
is synthetic: these tests do not prove signed-file authorization, DNS, ACME or a
full old updater's first adoption/rollback. Every Docker resource belongs to this
fixture; no host Docker socket is mounted in a container.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import pytest
import yaml


pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]
APP, VISUAL = "acme.example", "files.acme.example"
TOKEN = "fixture." + "a" * 43
SIGNED_PATH = "/visuals/" + TOKEN
ETAG = '"fictional-etag"'
READY = "/__tico_caddy_ready"

BACKEND = r'''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib, json, threading
count = 0
lock = threading.Lock()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self):
        global count
        if self.path == '/__qa_count':
            body = json.dumps({'count': count}).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        with lock:
            count += 1
        auth = {k.lower(): v for k, v in self.headers.items() if k.lower() in
                ('cookie', 'authorization', 'proxy-authorization') or
                k.lower().startswith(('cf-access-', 'x-amzn-oidc-', 'x-tico-'))}
        conditional = self.headers.get('If-None-Match') == '"fictional-etag"'
        ranged = self.headers.get('Range') == 'bytes=2-4'
        body = b'' if conditional else b'234' if ranged else b'0123456789'
        self.send_response(304 if conditional else 206 if ranged else 200)
        for key, value in {
            'Content-Length': str(len(body)), 'Set-Cookie': 'synthetic=fixture',
            'Content-Security-Policy': "sandbox; default-src 'none'",
            'Cache-Control': 'private, no-cache', 'Referrer-Policy': 'no-referrer',
            'Accept-Ranges': 'bytes', 'ETag': '"fictional-etag"',
            'X-QA-Auth-Names': ','.join(sorted(auth)),
            'X-QA-Auth-Hash': hashlib.sha256(json.dumps(auth, sort_keys=True).encode()).hexdigest(),
            'X-QA-Method': self.command, 'X-QA-If-Range': self.headers.get('If-Range', '')
        }.items(): self.send_header(key, value)
        if ranged: self.send_header('Content-Range', 'bytes 2-4/10')
        self.end_headers()
        if self.command != 'HEAD': self.wfile.write(body)
    do_GET = do_HEAD = do_POST = do_OPTIONS = reply
ThreadingHTTPServer(('0.0.0.0', 8765), Handler).serve_forever()
'''

# The producer's production file is never observed by the HTTP-only consumer.
# Keep every handler, matcher, log filter and private readiness marker unchanged.
HTTP_FIXTURE = r'''
import hashlib, json, os
from pathlib import Path
directory = Path('/caddy-generated')
raw = (directory / 'caddy.json').read_bytes()
config = json.loads(raw)
before = json.loads(raw)
web = config['apps']['http']['servers'].get('tico')
if web:
    assert web['listen'] == [':443'] and web['tls_connection_policies'] == [{}]
    web['listen'] = [':8080']
    web['automatic_https'] = {'disable': True}
    web.pop('tls_connection_policies')
    assert web['routes'] == before['apps']['http']['servers']['tico']['routes']
assert config['logging'] == before['logging']
assert config['apps']['http']['servers']['tico_ready'] == before['apps']['http']['servers']['tico_ready']
content = json.dumps(config, sort_keys=True, separators=(',', ':')).encode()
temporary = directory / '.fixture-new'
with temporary.open('xb') as stream:
    os.fchmod(stream.fileno(), 0o644)
    stream.write(content); stream.flush(); os.fsync(stream.fileno())
os.replace(temporary, directory / 'fixture.json')
marker = config['apps']['http']['servers']['tico_ready']['routes'][0]['handle'][0]['body']
stats = {}
for name in ('', 'caddy.json', 'supervisor.sh', 'fixture.json'):
    stat = (directory / name).stat()
    stats[name or 'directory'] = [stat.st_uid, stat.st_gid, stat.st_mode & 0o777]
print(json.dumps({'producer_sha256': hashlib.sha256(raw).hexdigest(),
                  'fixture_sha256': hashlib.sha256(content).hexdigest(),
                  'generation': marker.removeprefix('tico-caddy-generation:'),
                  'stats': stats, 'configuration': config}))
'''


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


class Containers:
    def __init__(self, image, output):
        self.image = image
        self.prefix = "tico-caddy-qa-" + uuid.uuid4().hex[:12]
        self.output = output / self.prefix
        self.owned, self.volumes, self.logs, self.steps = {}, [], {}, []
        self.network = self.caddy = self.backend = None
        self.passed = False
        self.output.mkdir(parents=True)
        self.vendor = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["caddy"]["image"]
        assert re.fullmatch(r"caddy:2-alpine@sha256:[0-9a-f]{64}", self.vendor)

    def redact(self, value):
        return value.replace(SIGNED_PATH, "<synthetic-path>").replace(TOKEN, "<synthetic-token>").replace("a" * 43, "<synthetic-mac>")

    def docker(self, *args, check=True, timeout=90):
        try:
            result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError("Docker fixture operation timed out: " + args[0]) from None
        if check and result.returncode:
            self.logs["docker-failure.log"] = self.redact(result.stdout + result.stderr)
            raise AssertionError("Docker fixture operation failed: %s, rc=%s" % (args[0], result.returncode))
        return result

    def create(self, image, flags, command):
        name = self.prefix + "-" + uuid.uuid4().hex[:8]
        self.owned[name] = name  # exact fixture-owned name also covers an uncertain create response
        cid = self.docker("create", "--name", name, "--label", "tico.caddy.qa=" + self.prefix,
                          *flags, image, *command).stdout.strip()
        assert re.fullmatch(r"[0-9a-f]{64}", cid)
        self.owned[name] = cid
        return cid

    def once(self, entrypoint, command, flags=()):
        cid = self.create(self.image, ["--network", "none", "--read-only", "--tmpfs", "/tmp",
                                      "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                                      "--no-healthcheck", "--entrypoint", entrypoint, *flags], command)
        self.docker("start", cid)
        code = self.docker("wait", cid, timeout=45).stdout.strip()
        log = self.docker("logs", cid, check=False)
        self.logs[cid[:12] + ".log"] = self.redact(log.stdout + log.stderr)
        assert code == "0", "one-shot fixture producer failed; see retained logs"
        self.docker("rm", cid)
        self.owned = {name: value for name, value in self.owned.items() if value != cid}
        return log.stdout

    def volume_python(self, code):
        return self.once("python", ["-c", code], ["--mount", "type=volume,src=" + self.generated + ",dst=/caddy-generated"])

    def setup(self):
        image = json.loads(self.docker("image", "inspect", self.image).stdout)[0]
        assert image["Config"]["User"] == "10001:10001"
        assert image["Os"] == "linux"
        if os.environ.get("TICO_CADDY_EXPECTED_ARCH"):
            assert image["Architecture"] == os.environ["TICO_CADDY_EXPECTED_ARCH"]
        if os.environ.get("GITHUB_SHA"):
            assert image["Config"]["Labels"]["org.opencontainers.image.revision"] == os.environ["GITHUB_SHA"]
        manifest = json.loads(self.once("python", ["-c", "from pathlib import Path; print(Path('/opt/tico/release-manifest.json').read_text())"]))
        if os.environ.get("GITHUB_SHA"):
            assert manifest["commit"] == os.environ["GITHUB_SHA"] and manifest["repository"] == os.environ["GITHUB_REPOSITORY"]
        self.steps.append({"image_id": image["Id"], "architecture": image["Architecture"],
                           "os": image["Os"], "source": manifest["commit"], "repository": manifest["repository"]})
        self.network = self.prefix
        # Match the shipped edge's ordinary bridge: internal-only Docker networks do not publish host ports.
        # This synthetic HTTP fixture disables ACME and binds its sole host port explicitly to loopback.
        self.network = self.docker("network", "create", "--driver", "bridge", "--label", "tico.caddy.qa=" + self.prefix, self.prefix).stdout.strip()
        for suffix in ("generated", "data", "config"):
            name = self.prefix + "-" + suffix
            self.volumes.append(name)
            assert self.docker("volume", "create", "--label", "tico.caddy.qa=" + self.prefix, name).stdout.strip() == name
        self.generated, self.data, self.config = self.volumes
        self.backend = self.create(self.image, ["--network", self.network, "--network-alias", "server",
                    "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                    "--no-healthcheck", "--entrypoint", "python"], ["-u", "-c", BACKEND])
        self.docker("start", self.backend)
        self.wait(lambda: self.count() == 0, "synthetic backend startup")

    def count(self):
        result = self.docker("exec", self.backend, "python", "-c",
            "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/__qa_count', timeout=1).read().decode())", check=False)
        return json.loads(result.stdout)["count"] if result.returncode == 0 else None

    def backend_count(self):
        count = self.count()
        assert type(count) is int, "synthetic backend counter is unavailable"
        return count

    def produce(self, flag=None, origin="https://" + VISUAL, app=APP):
        flags = ["--mount", "type=volume,src=" + self.generated + ",dst=/caddy-generated",
                 "--env", "TICO_DOMAIN=" + app, "--env", "TICO_VISUAL_ORIGIN=" + origin]
        if flag is not None:
            flags += ["--env", "TICO_CADDY_VISUALS=" + flag]
        self.once("tico-entrypoint", ["caddy-config"], flags)
        metadata = json.loads(self.volume_python(HTTP_FIXTURE))
        assert metadata["stats"]["directory"] == [10001, 10001, 0o755]
        assert all(metadata["stats"][name] == [10001, 10001, 0o644]
                   for name in ("caddy.json", "supervisor.sh", "fixture.json"))
        configuration = metadata.pop("configuration")
        (self.output / (metadata["fixture_sha256"] + ".json")).write_text(json.dumps(configuration, indent=2) + "\n")
        self.steps.append({"producer": metadata})
        return metadata

    def start_caddy(self, *, legacy=False):
        flags = ["--network", self.network, "--read-only", "--tmpfs", "/tmp", "--init",
                 "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges:true",
                 "--mount", "type=volume,src=" + self.data + ",dst=/data",
                 "--mount", "type=volume,src=" + self.config + ",dst=/config",
                 "--publish", "127.0.0.1::8080"]
        if legacy:
            flags += ["--entrypoint", "/usr/bin/caddy"]
            command = ["reverse-proxy", "--from", "http://" + APP + ":8080", "--to", "server:8765"]
        else:
            flags += ["--mount", "type=volume,src=" + self.generated + ",dst=/generated,readonly",
                      "--env", "TICO_CADDY_CONFIG=/generated/fixture.json", "--entrypoint", "/bin/sh",
                      "--health-cmd", "/bin/sh /generated/supervisor.sh health", "--health-interval", "1s",
                      "--health-timeout", "3s", "--health-start-period", "1s", "--health-retries", "1"]
            command = ["/generated/supervisor.sh"]
        self.caddy = self.create(self.vendor, flags, command)
        self.docker("start", self.caddy)
        info = self.inspect()
        self.steps.append({"started_container": self.caddy, "state": info["State"],
                           "cap_drop": info["HostConfig"]["CapDrop"], "cap_add": info["HostConfig"]["CapAdd"],
                           "requested_ports": info["HostConfig"]["PortBindings"],
                           "published_ports": info["NetworkSettings"]["Ports"]})
        vendor = json.loads(self.docker("image", "inspect", self.vendor).stdout)[0]
        assert vendor["Os"] == "linux"
        if os.environ.get("TICO_CADDY_EXPECTED_ARCH"):
            assert vendor["Architecture"] == os.environ["TICO_CADDY_EXPECTED_ARCH"]
        bindings = info["NetworkSettings"]["Ports"].get("8080/tcp")
        assert bindings, "fixture loopback port was not published; see retained startup state"
        assert {port for port, rows in info["NetworkSettings"]["Ports"].items() if rows} == {"8080/tcp"}
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        self.port = int(bindings[0]["HostPort"])
        if not legacy:
            mount = next(row for row in info["Mounts"] if row["Destination"] == "/generated")
            assert mount["RW"] is False
            denied = self.docker("exec", self.caddy, "/bin/sh", "-c", "echo synthetic >/generated/forbidden", check=False).returncode
            assert denied != 0
            self.steps.append({"generated_read_only": True, "write_attempt_rc": denied})
        self.steps.append({"container": self.caddy, "legacy_command": legacy,
                           "pinned_vendor": self.vendor, "vendor_image_id": info["Image"],
                           "vendor_architecture": vendor["Architecture"]})

    def inspect(self):
        return json.loads(self.docker("inspect", self.caddy).stdout)[0]

    def request(self, host, path="/", method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request(method, path, headers={"Host": host, **(headers or {})})
            response = connection.getresponse()
            return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
        finally:
            connection.close()

    def expect(self, host, path, status, method="GET", headers=None):
        result = self.request(host, path, method, headers)
        self.steps.append({"host": host, "target_sha256": digest(path), "method": method, "status": result[0]})
        if result[0] != status:
            raise AssertionError("routing expectation failed: target sha256=%s status=%s" % (digest(path), result[0]))
        return result

    def wait(self, condition, description, seconds=35):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if condition():
                    return
            except (OSError, http.client.HTTPException, ValueError):
                pass
            time.sleep(.2)
        raise AssertionError("fixture transition timed out: " + description)

    def applied(self):
        result = self.docker("exec", self.caddy, "cat", "/tmp/tico-caddy/applied", check=False)
        return json.loads(result.stdout) if result.returncode == 0 else None

    def ready(self, metadata):
        def confirmed():
            proof = self.applied()
            return (proof and proof["file_sha256"] == metadata["fixture_sha256"]
                    and proof["generation"] == metadata["generation"] and proof["pid"] > 1
                    and proof["supervisor_pid"] > 0
                    and self.docker("exec", self.caddy, "/bin/sh", "/generated/supervisor.sh", "health", check=False).returncode == 0
                    and self.inspect()["State"]["Health"]["Status"] == "healthy")
        self.wait(confirmed, "exact frozen generation and owned-child health")
        proof = self.applied()
        self.steps.append({"applied": proof})
        return proof

    def unavailable(self, old_pid):
        def stopped():
            return (not self.applied() and self.inspect()["State"]["Running"]
                    and self.docker("exec", self.caddy, "/bin/sh", "/generated/supervisor.sh", "health", check=False).returncode != 0
                    and self.docker("exec", self.caddy, "/bin/sh", "-c", '! kill -0 "$1" 2>/dev/null', "qa", str(old_pid), check=False).returncode == 0)
        self.wait(stopped, "proof cleared and prior child reaped")
        try:
            self.request(VISUAL, SIGNED_PATH)
        except (OSError, http.client.HTTPException):
            self.steps.append({"unavailable": True})
        else:
            raise AssertionError("invalid/missing configuration retained a public listener")

    def remove_caddy(self):
        self.capture(self.caddy)
        self.docker("rm", "-f", self.caddy)
        self.owned = {name: value for name, value in self.owned.items() if value != self.caddy}

    def capture(self, cid):
        result = self.docker("logs", cid, check=False)
        self.logs[cid[:12] + ".log"] = self.redact(result.stdout + result.stderr)
        if cid == self.caddy:
            for name in ("applied", "last-failure"):
                result = self.docker("exec", cid, "cat", "/tmp/tico-caddy/" + name, check=False)
                if result.returncode == 0:
                    try:
                        value = json.loads(result.stdout)
                    except ValueError:
                        value = {"unparseable_sha256": digest(result.stdout)}
                    self.steps.append({"supervisor_state": name, "value": value})

    def owned_target(self, kind, target):
        args = ("inspect", "--type", "container", target) if kind == "container" else (kind, "inspect", target)
        result = self.docker(*args, check=False)
        assert result.returncode == 0, "fixture cleanup cannot resolve owned " + kind
        row, = json.loads(result.stdout)
        labels = row.get("Config", {}).get("Labels", {}) if kind == "container" else row.get("Labels", {})
        assert labels.get("tico.caddy.qa") == self.prefix, "fixture cleanup refused an unowned " + kind
        return row["Name"] if kind == "volume" else row["Id"]

    def finish(self):
        cleanup_errors = []
        for candidate in list(self.owned.values()):
            try:
                cid = self.owned_target("container", candidate)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
                continue  # Never remove an unresolved or differently labelled resource.
            try:
                self.capture(cid)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
            try:
                self.docker("rm", "-f", cid)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        for volume in reversed(self.volumes):
            try:
                owned = self.owned_target("volume", volume)
                self.docker("volume", "rm", owned)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        if self.network:
            try:
                owned = self.owned_target("network", self.network)
                self.docker("network", "rm", owned)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        for name, value in self.logs.items():
            (self.output / name).write_text(value)
        (self.output / "receipt.json").write_text(json.dumps({"passed": self.passed and not cleanup_errors,
            "steps": self.steps, "cleanup_errors": cleanup_errors,
            "limits": "Synthetic HTTP container mechanics; no ACME, production DNS, actual signed authorization or full historical updater execution."}, indent=2) + "\n")
        hashes = {path.name: digest(path.read_bytes()) for path in self.output.iterdir() if path.is_file()}
        (self.output / "files-sha256.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
        assert not cleanup_errors, "fixture-owned resource cleanup failed; see receipt"


@pytest.fixture
def containers(tmp_path, request):
    image = os.environ.get("TICO_CADDY_TEST_IMAGE", "")
    if not image:
        if os.environ.get("TICO_CADDY_CONTAINER_QA") == "1":
            pytest.fail("explicit container acceptance requires TICO_CADDY_TEST_IMAGE")
        pytest.skip("set TICO_CADDY_TEST_IMAGE to an already built server image; never starts a local daemon")
    output = Path(os.environ.get("TICO_CADDY_TEST_OUTPUT", str(tmp_path))) / request.node.name
    qa = Containers(image, output)
    try:
        qa.setup()
        yield qa
    finally:
        qa.finish()


def test_shipped_linux_producer_and_pinned_caddy_enforce_visual_boundary(containers):
    qa = containers
    first = qa.produce()   # Default OFF with a nonempty external visual origin.
    qa.start_caddy()
    qa.ready(first)
    before = qa.backend_count()
    qa.expect(VISUAL, SIGNED_PATH, 404)
    assert qa.backend_count() == before
    metadata = qa.produce("1")
    qa.ready(metadata)
    app_auth = {"Cookie": "synthetic=app", "Authorization": "Bearer fictional"}
    _, headers, _ = qa.expect(APP, "/api/v2/config", 200, headers=app_auth)
    assert headers["x-qa-auth-hash"] == digest(json.dumps({key.lower(): value for key, value in app_auth.items()}, sort_keys=True))
    assert headers["set-cookie"] == "synthetic=fixture"
    auth = {**app_auth, "Proxy-Authorization": "Basic fictional", "Cf-Access-Jwt-Assertion": "fictional",
            "Cf-Access-Authenticated-User-Email": "bot@acme.example", "X-Amzn-Oidc-Data": "fictional",
            "X-Amzn-Oidc-Identity": "fictional", "X-Tico-Assistant-Action": "fictional", "X-Tico-Other": "fictional"}
    for method, extra, status, body in (("GET", {}, 200, b"0123456789"), ("HEAD", {}, 200, b""),
            ("GET", {"Range": "bytes=2-4", "If-Range": ETAG}, 206, b"234"),
            ("GET", {"If-None-Match": ETAG}, 304, b"")):
        _, headers, actual = qa.expect(VISUAL, SIGNED_PATH, status, method, {**auth, **extra})
        assert actual == body and headers["x-qa-auth-names"] == "" and "set-cookie" not in headers
        assert headers["content-security-policy"] == "sandbox; default-src 'none'"
        assert headers["cache-control"] == "private, no-cache" and headers["referrer-policy"] == "no-referrer"
        assert headers["etag"] == ETAG and headers["x-qa-method"] == method
        if status == 206:
            assert headers["content-range"] == "bytes 2-4/10" and headers["x-qa-if-range"] == ETAG
    before = qa.backend_count()
    for path in ("/api/v2/config", "/auth/login", SIGNED_PATH + "?x=1", SIGNED_PATH + "?",
                 "/visuals/x/../" + TOKEN, "/visuals/%2e%2e/" + TOKEN,
                 "/visuals//" + TOKEN, SIGNED_PATH.replace(".", "%2e"), READY):
        qa.expect(VISUAL, path, 404)
    for method in ("POST", "OPTIONS"):
        qa.expect(VISUAL, SIGNED_PATH, 404, method)
    qa.expect("unknown.acme.example", SIGNED_PATH, 404)
    assert qa.backend_count() == before
    qa.docker("stop", qa.backend)
    qa.expect(VISUAL, SIGNED_PATH, 502, headers={"Referer": "https://" + VISUAL + SIGNED_PATH})
    def error_logged():
        log = qa.docker("logs", qa.caddy)
        return '"logger":"http.log.error"' in log.stdout + log.stderr
    qa.wait(error_logged, "actual error log without request credentials", seconds=5)
    log = qa.docker("logs", qa.caddy)
    if TOKEN in log.stdout + log.stderr:
        raise AssertionError("Caddy error log exposed the synthetic signed path")
    qa.passed = True


def test_linux_generation_removal_failure_recovery_and_legacy_command_rollback(containers):
    qa = containers
    metadata = qa.produce("1")
    qa.start_caddy()
    proof = qa.ready(metadata)
    cid = qa.caddy
    for flag, origin in (("0", "https://" + VISUAL), ("1", "https://" + VISUAL + "/invalid"), ("1", "")):
        metadata = qa.produce(flag, origin)
        removed = qa.ready(metadata)
        assert qa.caddy == cid and removed["pid"] != proof["pid"]
        qa.expect(APP, "/healthz", 200)
        before = qa.backend_count()
        qa.expect(VISUAL, SIGNED_PATH, 404)
        assert qa.backend_count() == before
        proof = qa.ready(qa.produce("1"))
    qa.volume_python("import json,os; from pathlib import Path; p=Path('/caddy-generated/fixture.json'); "
        "c=json.loads(p.read_bytes()); c['apps']['http']['servers']['tico']['routes'][0]['handle']=[{'handler':'fictional_invalid_handler'}]; "
        "t=p.with_name('.invalid-new'); t.write_text(json.dumps(c)); os.replace(t,p)")
    qa.unavailable(proof["pid"])
    proof = qa.ready(qa.produce("1"))
    qa.volume_python("from pathlib import Path; Path('/caddy-generated/fixture.json').unlink()")
    qa.unavailable(proof["pid"])
    metadata = qa.produce("1")
    qa.ready(metadata)
    marker = uuid.uuid4().hex
    qa.docker("exec", qa.caddy, "/bin/sh", "-c", 'printf %s "$1" >/data/qa-marker; printf %s "$1" >/config/qa-marker', "qa", marker)
    qa.remove_caddy()
    qa.start_caddy()
    qa.ready(metadata)
    assert qa.caddy != cid
    assert qa.docker("exec", qa.caddy, "cat", "/data/qa-marker", "/config/qa-marker").stdout == marker * 2
    qa.remove_caddy()
    # The stale enabled file remains in the named volume; the old direct app-only command cannot serve it.
    qa.start_caddy(legacy=True)
    qa.wait(lambda: qa.request(APP, "/healthz")[0] == 200, "restored legacy direct proxy")
    legacy = qa.inspect()
    assert legacy["Config"]["Entrypoint"] == ["/usr/bin/caddy"]
    assert legacy["Config"]["Cmd"] == ["reverse-proxy", "--from", "http://" + APP + ":8080", "--to", "server:8765"]
    assert all(mount["Destination"] != "/generated" for mount in legacy["Mounts"])
    assert legacy["State"].get("Health") is None and qa.applied() is None
    before = qa.backend_count()
    # The vendor CLI has no fallback handler: an unmatched host gets an empty
    # 200, never the stale generated visual site or a forwarded backend read.
    _, headers, body = qa.expect(VISUAL, SIGNED_PATH, 200)
    assert body == b"" and "x-qa-method" not in headers and "set-cookie" not in headers
    assert qa.backend_count() == before
    assert qa.docker("exec", qa.caddy, "cat", "/data/qa-marker", "/config/qa-marker").stdout == marker * 2
    qa.steps.append({"certificate_volume_markers_preserved": True, "marker_sha256": digest(marker)})
    qa.steps.append({"legacy_running": True, "entrypoint": legacy["Config"]["Entrypoint"],
                     "command": legacy["Config"]["Cmd"], "applied_generation": False,
                     "asset_host_not_forwarded": True, "asset_response_bytes": len(body)})
    qa.passed = True
