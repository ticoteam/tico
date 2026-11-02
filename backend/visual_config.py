"""Private, read-only observations for the local visual configuration operator.

This module never initializes the application, allocates a census identity,
creates a signing key, or accepts a caller-selected network destination.
"""
from __future__ import annotations

import hashlib
import http.client
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import stat
import sys
import time
from contextlib import closing

from setup import visual_contract as wire

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 32 * 1024 * 1024


def read_file(path, limit, *, private=False, uid=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > limit
                or (private and (info.st_mode & 0o077 or info.st_uid != (os.geteuid() if uid is None else uid)))):
            raise wire.Pending("private_material_invalid")
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise wire.Pending("private_material_invalid")
        return raw
    finally:
        os.close(fd)


def capabilities(root=ROOT):
    # The image's existing release manifest covers all application code, while
    # the operator additionally checks the exact setup and supervisor sources.
    from .code_hash import digest
    manifest = wire.decode(read_file(root / "release-manifest.json", 4096))
    revision = manifest.get("commit")
    if not wire.matches(wire.REVISION, revision) or manifest.get("code") != digest(root):
        raise wire.Pending("runtime_source_unsupported")
    return {"revision": revision,
            "runtime_files": {name: hashlib.sha256(read_file(root / name, 2 * 1024 * 1024)).hexdigest()
                              for name in wire.RUNTIME_FILES}}


def server_environment(proc=Path("/proc")):
    candidates = []
    for path in proc.iterdir():
        if not path.name.isdecimal():
            continue
        try:
            command = (path / "cmdline").read_bytes().split(b"\0")
            # Only the actual ASGI server, not this observation process or the
            # backup supervisor, supplies the running settings.
            if not any(command[index:index + 2] == [b"-m", b"backend.server"] for index in range(len(command))):
                continue
            values = dict(item.split(b"=", 1) for item in (path / "environ").read_bytes().split(b"\0") if b"=" in item)
            if values.get(b"TICO_DB") == b"/data/hub.sqlite":
                candidates.append({key.decode(): value.decode() for key, value in values.items()})
        except (OSError, ValueError, UnicodeError):
            continue
    if len(candidates) != 1:
        raise wire.Pending("running_server_unknown")
    return candidates[0]


def census(database=Path("/data/hub.sqlite")):
    # SQLite must fail on absence; the ordinary backend constructor would create
    # a database and census helpers may allocate an identity.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute("SELECT substr(value,1,8193) FROM registry_metadata WHERE key='usage-count' LIMIT 1").fetchone()
        value = wire.decode(row[0].encode(), 8192) if row and isinstance(row[0], str) else {}
    identity = value.get("install_id")
    if not wire.uuid4(identity):
        raise wire.Pending("census_identity_missing")
    return identity


def observe(*, root=ROOT, environ=None, database=Path("/data/hub.sqlite"), directory=Path("/caddy-generated")):
    from . import caddy_assets
    env = server_environment() if environ is None else environ
    app = env.get("TICO_DOMAIN", "")
    enabled = env.get("TICO_CADDY_VISUALS") == "1"
    runner = caddy_assets._runner_host(env.get("TICO_RUNNER_URL", ""), app) if enabled else ""
    config, generation, serving, reason = caddy_assets._configuration(
        app, env.get("TICO_VISUAL_ORIGIN", ""), runner, enabled)
    actual = read_file(directory / "caddy.json", 262144)
    if reason or serving != enabled or actual != caddy_assets._encoded(config) + b"\n":
        raise wire.Pending("generated_configuration_mismatch")
    if read_file(directory / "supervisor.sh", 65536) != read_file(root / "docker/caddy-supervisor.sh", 65536):
        raise wire.Pending("supervisor_source_mismatch")
    return capabilities(root) | {"install_id": census(database), "public_hostname": app,
        "configuration": {key: env.get(key, "") for key in wire.MANAGED},
        # Container hostname changes during recreation; all configured settings,
        # including auth and signing secrets, must retain their actual values.
        # Only the digest crosses this private local observation boundary.
        "unrelated_digest": wire.digest({key: value for key, value in env.items() if key not in (*wire.MANAGED, "HOSTNAME")}),
        "generation": generation, "file_sha256": hashlib.sha256(actual).hexdigest()}


class CaddyTLS(http.client.HTTPSConnection):
    """The existing private Caddy service, with the asset's real certificate/SNI."""
    def __init__(self, host, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())

    def connect(self):
        connection = socket.create_connection(("caddy", 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except BaseException:
            connection.close()
            raise


def exchange(host, path, method="GET", headers=None, *, deadline, connection=CaddyTLS):
    left = deadline - time.monotonic()
    if left <= 0:
        raise wire.Pending("probe_timeout")
    client = connection(host, min(2, left))
    try:
        client.request(method, path, headers={"Accept-Encoding": "identity", "Connection": "close", **(headers or {})})
        response = client.getresponse()
        if response.getheader("Set-Cookie") is not None or response.getheader("Content-Encoding") not in (None, "identity"):
            raise wire.Pending("probe_response_invalid")
        size, digest = 0, hashlib.sha256()
        while True:
            if time.monotonic() >= deadline:
                raise wire.Pending("probe_timeout")
            chunk = response.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BODY:
                raise wire.Pending("probe_response_invalid")
            digest.update(chunk)
        return response.status, digest.hexdigest()
    finally:
        client.close()


def probe(request, material, environ, *, database=Path("/data/hub.sqlite"), exchange=exchange):
    from . import visuals
    if (not isinstance(material, dict) or set(material) != {"token", "sha256", "install_id"}
            or material["install_id"] != request["install_id"] or not wire.matches(wire.HEX, material["sha256"])
            or not wire.matches(wire.TOKEN, material["token"]) or len(material["token"]) > 2048):
        raise wire.Pending("probe_material_invalid")
    key = environ.get("TICO_VISUAL_SECRET", "").strip().encode()
    if not key:
        key = read_file(database.parent / visuals.SECRET_FILE, 4096, private=True).strip()
    if len(key) < 32:
        raise wire.Pending("probe_material_invalid")
    # Use the existing visual token implementation. Never create/rotate a key,
    # mint a token for an arbitrary reader, or treat an invalid token as OFF proof.
    token = material["token"]
    try:
        found = visuals.read(key, token)
        body = wire.decode(visuals._unb64(token.partition(".")[0]), 2048)
        if not found or type(body[-1]) is not int or body[-1] < time.time() + 30:
            raise ValueError()
    except (ValueError, TypeError, IndexError):
        raise wire.Pending("probe_material_expired") from None
    host, deadline = request["hostname"], time.monotonic() + 8
    path = "/visuals/" + token
    markers = {"Cookie": "tico_visual_probe=inert", "Authorization": "Bearer inert-tico-visual-probe",
               "Proxy-Authorization": "inert", "Cf-Access-Jwt-Assertion": "inert",
               "X-Amzn-Oidc-Data": "inert", "X-Tico-Visual-Probe": "inert"}
    def refused(path):
        try:
            return exchange(host, path, headers=markers, deadline=deadline)[0] == 404
        except ssl.SSLCertVerificationError:
            raise wire.Pending("probe_tls_unverified") from None
        except ssl.SSLError as error:
            # Caddy may retire the hostname at the TLS layer. Only a peer's
            # explicit handshake/name refusal counts; timeout, bad certificates
            # and a stopped/unreachable front door do not prove retirement.
            if not request["enabled"] and error.reason in ("TLSV1_ALERT_INTERNAL_ERROR", "SSLV3_ALERT_HANDSHAKE_FAILURE",
                                                           "TLSV1_UNRECOGNIZED_NAME"):
                return True
            raise
    if request["enabled"]:
        if exchange(host, path, deadline=deadline) != (200, material["sha256"]):
            raise wire.Pending("signed_probe_pending")
        if exchange(host, path, headers=markers, deadline=deadline) != (200, material["sha256"]):
            raise wire.Pending("credential_probe_pending")
        if exchange(host, path, method="HEAD", headers=markers, deadline=deadline)[0] != 200:
            raise wire.Pending("signed_probe_pending")
        body, mac = token.split(".")
        bogus = body + "." + ("A" if mac[0] != "A" else "B") + mac[1:]
        if exchange(host, "/visuals/" + bogus, deadline=deadline)[0] != 404:
            raise wire.Pending("unsigned_probe_pending")
    else:
        if exchange(request["public_hostname"], "/healthz", deadline=deadline)[0] != 200 or not refused(path):
            raise wire.Pending("retirement_probe_pending")
    for path in ("/auth/login", "/api/v2/config"):
        if not refused(path):
            raise wire.Pending("auth_route_probe_pending")
    return True


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["capabilities"]:
            result = capabilities()
        elif args == ["observe"]:
            result = observe()
        elif args == ["probe"]:
            value = wire.decode(sys.stdin.buffer.read(8193), 8192)
            if not isinstance(value, dict) or set(value) != {"request", "material"}:
                raise wire.Pending("probe_material_invalid")
            request = wire.validate(value["request"])
            environment = server_environment()
            before = observe(environ=environment)
            if (before["install_id"] != request["install_id"] or before["public_hostname"] != request["public_hostname"]
                    or before["configuration"] != wire.configuration(request)):
                raise wire.Pending("running_configuration_mismatch")
            probe(request, value["material"], environment)
            if observe() != before:
                raise wire.Pending("runtime_changed")
            result = before
        else:
            raise wire.Pending("operation_unsupported")
        sys.stdout.buffer.write(wire.encoded(result) + b"\n")
        return 0
    except Exception:
        # No token, key, URL, body, provider output or backend exception crosses
        # this private observation boundary, even on malformed probe material.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
