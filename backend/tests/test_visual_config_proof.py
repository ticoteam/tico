"""Private asset proof observes existing identity/key/material without initializing."""
import hashlib
import importlib
import os
from pathlib import Path
import sqlite3
import ssl
import sys
import time

import pytest

from backend import caddy_assets, visual_config as runtime, visuals
from setup import visual_contract as wire

ID = "12345678-1234-4234-8234-123456789abc"
KEY = b"existing-fictional-visual-secret-32-bytes"


def request(enabled=True):
    return {"operation_id": "operation_a", "revision": 1, "account_id": ID, "company_id": ID,
            "workspace_id": ID, "preview_id": "preview_1", "attempt_id": "attempt_1",
            "address": "pv-abcdefgh.preview.acme.example", "public_hostname": "acme.tico.dev", "enabled": enabled,
            "lease_id": ID, "hostname": "acme.ticofiles.com", "receipt": "a" * 32,
            "instance_id": "i-123456789abcdef01", "install_id": ID, "challenge": "b" * 64}


def material(now=None):
    return {"install_id": ID, "token": visuals.mint(KEY, "human:fictional", "file1", 1, now=now),
            "sha256": hashlib.sha256(b"artifact response").hexdigest()}


def test_on_actual_probe_contract_refusals_and_inert_credentials():
    calls, proof = [], material()
    def exchange(host, path, method="GET", headers=None, **kwargs):
        calls.append((host, path, method, headers))
        return (200, proof["sha256"]) if path == "/visuals/" + proof["token"] else (404, "")
    assert runtime.probe(request(), proof, {"TICO_VISUAL_SECRET": KEY.decode()}, exchange=exchange)
    assert len(calls) == 6
    assert all(x[0] == "acme.ticofiles.com" for x in calls)
    assert calls[1][3]["Cookie"] == "tico_visual_probe=inert"
    assert calls[1][3]["Authorization"] == "Bearer inert-tico-visual-probe"
    assert calls[2][2] == "HEAD"
    assert calls[3][1] != calls[0][1]
    with pytest.raises(wire.Pending, match="signed_probe_pending"):
        runtime.probe(request(), proof, {"TICO_VISUAL_SECRET": KEY.decode()}, exchange=lambda *args, **kw: (404, ""))


def test_off_requires_current_valid_token_and_live_app_not_expired_false_positive():
    calls = []
    def refused(host, path, **kwargs):
        calls.append((host, path))
        return (200, "") if host == "acme.tico.dev" else (404, "")
    assert runtime.probe(request(False), material(), {"TICO_VISUAL_SECRET": KEY.decode()}, exchange=refused)
    assert calls[0] == ("acme.tico.dev", "/healthz")
    calls.clear()
    for wrong in (material(now=time.time() - 20000), material() | {"install_id": "foreign"},
                  material() | {"token": material()["token"] + "x"}):
        with pytest.raises(wire.Pending):
            runtime.probe(request(False), wrong, {"TICO_VISUAL_SECRET": KEY.decode()}, exchange=refused)
    assert not calls
    with pytest.raises(wire.Pending, match="retirement_probe_pending"):
        runtime.probe(request(False), material(), {"TICO_VISUAL_SECRET": KEY.decode()}, exchange=lambda *args, **kw: (200, ""))


def test_cookie_response_and_wrong_content_are_never_success():
    class Response:
        status = 200
        def getheader(self, name):
            return "session=secret" if name == "Set-Cookie" else None
    class Connection:
        def __init__(self, *args): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    with pytest.raises(wire.Pending, match="probe_response_invalid"):
        runtime.exchange("acme.ticofiles.com", "/visuals/fictional", deadline=time.monotonic()+5, connection=Connection)
    with pytest.raises(wire.Pending, match="signed_probe_pending"):
        runtime.probe(request(), material(), {"TICO_VISUAL_SECRET": KEY.decode()}, exchange=lambda *args, **kw: (200, "wrong"))


def test_existing_key_only_no_mint_and_no_new_census_or_database(tmp_path, monkeypatch):
    database = tmp_path / "hub.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        runtime.census(database)
    assert not database.exists()
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE registry_metadata(key TEXT,value TEXT)")
        conn.execute("INSERT INTO registry_metadata VALUES('usage-count',?)", (wire.encoded({"install_id": ID}).decode(),))
    before = database.read_bytes()
    assert runtime.census(database) == ID
    assert database.read_bytes() == before
    monkeypatch.setattr(visuals, "mint", lambda *args, **kwargs: pytest.fail("proof must not mint"))
    probe = {"install_id": ID, "token": "validshape." + "a" * 43, "sha256": "a" * 64}
    with pytest.raises(FileNotFoundError):
        runtime.probe(request(), probe, {}, database=database)
    assert not (tmp_path / "visual-secret").exists()
    (tmp_path / "visual-secret").write_bytes(KEY)
    (tmp_path / "visual-secret").chmod(0o644)
    with pytest.raises(wire.Pending, match="private_material_invalid"):
        runtime.probe(request(), probe, {}, database=database)


def test_actual_generated_bytes_and_supervisor_are_bound_to_observed_process(tmp_path, monkeypatch):
    root, generated = tmp_path / "source", tmp_path / "generated"
    (root / "docker").mkdir(parents=True)
    generated.mkdir()
    (root / "docker/caddy-supervisor.sh").write_bytes(b"approved supervisor")
    (generated / "supervisor.sh").write_bytes(b"approved supervisor")
    env = {"TICO_DOMAIN": "acme.tico.dev", **wire.configuration(request())}
    caddy_assets.prepare(env | {"TICO_CADDY_DIR": str(generated)})
    monkeypatch.setattr(runtime, "capabilities", lambda root: {"revision": "a" * 40, "runtime_files": {}})
    monkeypatch.setattr(runtime, "census", lambda path: ID)
    observed = runtime.observe(root=root, environ=env, directory=generated)
    assert observed["configuration"] == wire.configuration(request())
    assert observed["file_sha256"] == hashlib.sha256((generated / "caddy.json").read_bytes()).hexdigest()
    # Matching request fields and a plausible generation cannot hide a changed
    # running policy, including removed credential/cookie filters.
    altered = (generated / "caddy.json").read_bytes().replace(b'"Set-Cookie"', b'"Other"')
    (generated / "caddy.json").write_bytes(altered)
    with pytest.raises(wire.Pending, match="generated_configuration_mismatch"):
        runtime.observe(root=root, environ=env, directory=generated)


def test_discovery_verifies_actual_code_without_backend_initialization_or_census(tmp_path, monkeypatch):
    from backend.code_hash import digest
    for name in wire.RUNTIME_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fictional source\n")
    manifest = {"commit": "a" * 40, "code": digest(tmp_path)}
    (tmp_path / "release-manifest.json").write_bytes(wire.encoded(manifest))
    monkeypatch.setattr(runtime, "census", lambda *args: pytest.fail("discovery read census"))
    assert runtime.capabilities(tmp_path)["revision"] == "a" * 40
    (tmp_path / "backend/visual_config.py").write_bytes(b"modified")
    with pytest.raises(wire.Pending, match="runtime_source_unsupported"):
        runtime.capabilities(tmp_path)


def test_only_actual_server_process_environment_is_observed(tmp_path):
    for pid, command in (("100", b"python\0-m\0backend.server\0"), ("101", b"python\0-m\0backend.visual_config\0")):
        p = tmp_path / pid
        p.mkdir()
        (p / "cmdline").write_bytes(command)
        (p / "environ").write_bytes(b"TICO_DB=/data/hub.sqlite\0TICO_CADDY_VISUALS=1\0")
    assert runtime.server_environment(tmp_path)["TICO_CADDY_VISUALS"] == "1"
    (tmp_path / "100/cmdline").write_bytes(b"python\0-m\0backend.census\0")
    with pytest.raises(wire.Pending, match="running_server_unknown"):
        runtime.server_environment(tmp_path)
