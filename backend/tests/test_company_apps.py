"""Private company configuration must not escape through logs or release artifacts."""
import json

import pytest

from scripts.company_apps import Invalid, key, main, matrix, parse

ENTRY = {"slug": "acme", "id": "12345678-1234-4234-8234-123456789abc", "app_name": "Acme Tico",
         "url": "https://tico.example.com", "icon_url": "/api/v2/team/icon",
         "deploy_role_arn": "arn:aws:iam::123456789012:role/publisher", "bucket": "acme-files"}


@pytest.fixture(autouse=True)
def no_icon_retry_waits(monkeypatch):
    from scripts import company_apps
    monkeypatch.setattr(company_apps.time, "sleep", lambda seconds: None)


def test_private_matrix_and_all_dry_runs_expose_only_hashes(monkeypatch, capsys):
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert matrix(parse(json.dumps([ENTRY]))) == {"include": [{"company": key(ENTRY)}]}
    for action in ("matrix", "configure", "build", "pack", "unpack", "publish"):
        assert main([action, "--dry-run", "--company", key(ENTRY)]) == 0
        out = capsys.readouterr()
        assert key(ENTRY) in out.out
        assert all(v not in out.out + out.err for v in ENTRY.values())


@pytest.mark.parametrize("raw", [json.dumps([{**ENTRY, "prefix": "../escape"}])])
def test_invalid_matrix_has_no_private_error_values(raw, monkeypatch, capsys):
    with pytest.raises(Invalid):
        parse(raw)
    monkeypatch.setenv("TICO_COMPANY_APPS", raw)
    assert main(["configure", "--company", key(ENTRY)]) == 1
    assert main(["matrix", "--dry-run"]) == 1
    out = capsys.readouterr()
    assert all(v not in out.out + out.err for v in ENTRY.values())


def test_loopback_urls_and_matrix_contains_only_hashes(monkeypatch, capsys):
    local = {**ENTRY, "url": "http://localhost:8000", "runner_url": "http://127.0.0.1:8000"}
    assert parse(json.dumps([local])) == [local]
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert main(["matrix"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [json.dumps(matrix([ENTRY]), separators=(",", ":"))]


def test_configuration_bakes_company_identity_and_runner_feed(tmp_path, monkeypatch, capsys):
    from scripts import company_apps
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    env_file = tmp_path / "env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.3.9")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    entry = {**ENTRY, "runner_url": "https://runner.example.com", "tray_label": "acm1"}
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([entry]))
    class Body:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            return b"\x89PNG\r\n\x1a\nlogo"
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://tico.example.com/api/v2/team/icon"
            return Body()
    monkeypatch.setattr(company_apps, "build_opener", lambda *args: Opener())
    assert main(["configure", "--company", key(entry)]) == 0
    conf = json.loads((tmp_path / "app/company-config.json").read_text())
    assert conf["version"] == "0.3.9"
    assert conf["identifier"] == "team.tico.env." + ENTRY["id"]
    assert conf["productName"] == ENTRY["app_name"]
    assert conf["plugins"]["updater"]["endpoints"] == ["https://runner.example.com/download/latest.json"]
    assert "pubkey" not in conf["plugins"]["updater"]  # inherits the generic app's signing key
    assert "TICO_HUB_URL=https://tico.example.com" in env_file.read_text()
    # An entry from before the menu-bar item was removed may still carry tray_label; it is ignored.
    assert "TRAY_LABEL" not in env_file.read_text()
    output = capsys.readouterr()
    assert all("::add-mask::" + value in output.out.splitlines() for value in entry.values())
    public = "\n".join(line for line in output.out.splitlines() if not line.startswith("::add-mask::"))
    assert all(value not in public + output.err for value in entry.values())
    def fail(*args):
        raise RuntimeError(ENTRY["app_name"])
    monkeypatch.setattr(company_apps, "build_opener", fail)
    assert main(["configure", "--company", key(entry)]) == 1
    assert ENTRY["app_name"] not in capsys.readouterr().err


def test_invalid_company_isolated_and_publishing_does_not_fetch_icon(tmp_path, monkeypatch, capsys):
    from scripts import company_apps
    other = {**ENTRY, "slug": "other", "id": "87654321-1234-4234-8234-123456789abc", "url": "http://example.com"}
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY, other, {}]))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setattr(company_apps, "build_opener", lambda *args: pytest.fail("Publisher must not fetch icons"))
    assert main(["matrix"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == matrix([ENTRY, other])
    assert all(value not in output.out + output.err for value in ENTRY.values())
    assert main(["configure", "--publishing", "--company", key(other)]) == 1
    assert main(["configure", "--publishing", "--company", key(ENTRY)]) == 0
    assert "TRAY_LABEL" not in (tmp_path / "env").read_text()


def test_icon_download_recovers_from_restart_and_interrupted_body(monkeypatch, capsys):
    from http.client import IncompleteRead
    from urllib.error import HTTPError, URLError
    from scripts import company_apps

    icon_url = ENTRY["url"] + ENTRY["icon_url"]
    icon = b"\x89PNG\r\n\x1a\nlogo"
    responses = iter([HTTPError(icon_url, 408, "private request timeout", {}, None),
                      HTTPError(icon_url, 429, "private rate limit", {}, None),
                      HTTPError(icon_url, 503, "private server unavailable", {}, None),
                      URLError(icon_url), TimeoutError(icon_url), ConnectionError(icon_url),
                      IncompleteRead(icon_url.encode(), 100), icon])
    delays = []

    class Body:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            assert size == 1024 * 1024 + 1
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            return value

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == icon_url
            assert timeout == 30
            return Body()

    monkeypatch.setattr(company_apps, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(company_apps.time, "sleep", delays.append)
    assert company_apps.download_icon(icon_url) == icon
    assert delays == [30, 60, 90, 120, 120, 120, 120]
    output = capsys.readouterr()
    assert icon_url not in output.out + output.err
    assert "private server unavailable" not in output.out + output.err


@pytest.mark.parametrize("failure,diagnostic", [("network", "network request failed"), ("http", "HTTP 503")])
def test_icon_outage_is_bounded_and_keeps_private_errors_out_of_logs(
        failure, diagnostic, tmp_path, monkeypatch, capsys):
    from urllib.error import HTTPError, URLError
    from scripts import company_apps

    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.5.131")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    attempts, delays = [], []

    class Opener:
        def open(self, request, timeout):
            attempts.append(request.full_url)
            if failure == "http":
                raise HTTPError(request.full_url, 503, "private server unavailable", {}, None)
            raise URLError(ENTRY["url"])

    monkeypatch.setattr(company_apps, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(company_apps.time, "sleep", delays.append)
    assert main(["configure", "--company", key(ENTRY)]) == 1
    assert len(attempts) == 8
    assert delays == [30, 60, 90, 120, 120, 120, 120]
    assert not (tmp_path / "app/company-icon.png").exists()
    assert not (tmp_path / "app/company-config.json").exists()
    output = capsys.readouterr()
    assert "icon download (TICO_COMPANY_APPS.icon_url)" in output.err
    assert diagnostic in output.err
    assert "private server unavailable" not in output.out + output.err
    assert all(value not in output.out + output.err for value in ENTRY.values())


def test_icon_permission_error_fails_without_retry(monkeypatch):
    from urllib.error import HTTPError
    from scripts import company_apps

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(company_apps, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(company_apps.time, "sleep", lambda *args: pytest.fail("Permanent errors must not retry"))
    with pytest.raises(HTTPError):
        company_apps.download_icon(ENTRY["url"] + ENTRY["icon_url"])


@pytest.mark.parametrize("failure,diagnostic", [
    ("http", "HTTP 404"),
    ("dns", "DNS lookup failed"),
    ("tls", "TLS certificate verification failed"),
    ("timeout", "request timed out"),
    ("redirect", "URLs require HTTPS"),
    ("unknown", "private exception details are suppressed"),
])
def test_icon_failure_reports_safe_category_without_private_response(
        failure, diagnostic, tmp_path, monkeypatch, capsys):
    import socket
    import ssl
    from urllib.error import HTTPError, URLError
    from scripts import company_apps

    monkeypatch.chdir(tmp_path)
    (tmp_path / "app").mkdir()
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.5.131")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    private = "private-response-and-credentials\n::error::injected"

    class Opener:
        def open(self, request, timeout):
            if failure == "redirect":
                return company_apps.SafeRedirect().redirect_request(
                    request, None, 302, private, {}, "http://private.acme.example/icon")
            errors = {
                "http": HTTPError(request.full_url, 404, private, {"Private": private}, None),
                "dns": URLError(socket.gaierror(-2, private)),
                "tls": URLError(ssl.SSLCertVerificationError(1, private)),
                "timeout": TimeoutError(private),
                "unknown": RuntimeError(private),
            }
            raise errors[failure]

    monkeypatch.setattr(company_apps, "build_opener", lambda *args: Opener())
    assert main(["configure", "--company", key(ENTRY)]) == 1
    output = capsys.readouterr()
    assert "icon download (TICO_COMPANY_APPS.icon_url)" in output.err
    assert diagnostic in output.err
    assert private not in output.err and "injected" not in output.err
    assert all(value not in output.out + output.err for value in ENTRY.values())
    assert not (tmp_path / "app/company-config.json").exists()


def test_missing_configuration_item_is_named_without_its_values(monkeypatch, capsys):
    entry = {**ENTRY, "icon_url": ""}
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([entry]))
    assert main(["configure", "--company", key(entry)]) == 1
    output = capsys.readouterr()
    assert "configuration failed" in output.err and "TICO_COMPANY_APPS.icon_url" in output.err
    assert all(value not in output.out + output.err for value in ENTRY.values())


def test_missing_signing_key_stops_before_private_build(monkeypatch, capsys):
    from scripts import company_apps
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.delenv("TAURI_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(company_apps.subprocess, "run", lambda *a, **kw: pytest.fail("must not build"))
    assert main(["build", "--company", key(ENTRY)]) == 1
    output = capsys.readouterr()
    assert "updater signing configuration" in output.err and "Missing TAURI_SIGNING_PRIVATE_KEY" in output.err
    assert all(value not in output.out + output.err for value in ENTRY.values())


@pytest.mark.parametrize("failing_command", ["icon", "build"])
def test_private_build_failures_identify_command_without_output(failing_command, monkeypatch, capsys):
    import subprocess
    from scripts import company_apps
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.setenv("TAURI_SIGNING_PRIVATE_KEY", "private-signing-key")
    commands = []

    def run(command, **kwargs):
        commands.append(command[2])
        assert kwargs["stdout"] is kwargs["stderr"]
        if command[2] == failing_command:
            raise subprocess.CalledProcessError(7, [ENTRY["app_name"]], output=ENTRY["url"])

    monkeypatch.setattr(company_apps.subprocess, "run", run)
    assert main(["build", "--company", key(ENTRY), "--target", "x86_64-unknown-linux-gnu",
                 "--bundles", "appimage,deb"]) == 1
    output = capsys.readouterr()
    assert "cargo tauri " + failing_command in output.err and "code 7" in output.err
    assert commands == (["icon"] if failing_command == "icon" else ["icon", "build"])
    assert all(value not in output.out + output.err for value in ENTRY.values())
    assert "private-signing-key" not in output.out + output.err


def test_publish_validation_does_not_expose_private_filenames(monkeypatch, capsys):
    from scripts import app_release
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.5.131")

    def fail(flags):
        assert "--quiet" in flags
        raise SystemExit(ENTRY["app_name"] + ".app.tar.gz has no signature")

    monkeypatch.setattr(app_release, "main", fail)
    assert main(["publish", "--company", key(ENTRY)]) == 1
    output = capsys.readouterr()
    assert "publish validation failed" in output.err
    assert all(value not in output.out + output.err for value in ENTRY.values())


def test_local_check_keeps_company_identity(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    if sys.platform != "darwin":
        pytest.skip("Local app.sh uses macOS plutil")
    root = Path(__file__).resolve().parents[2]
    env_dir = tmp_path / "environments" / "acme"
    env_dir.mkdir(parents=True)
    (env_dir / "environment.json").write_text(json.dumps({**ENTRY, "tray_label": "A1", "server": "remote"}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "captured.json"
    cargo = bin_dir / "cargo"
    cargo.write_text("#!/usr/bin/env python3\nimport json, os\nfrom pathlib import Path\n"
                     "Path(os.environ['CAPTURE']).write_text(json.dumps({k:v for k,v in os.environ.items() if k.startswith('TICO_')}))\n")
    cargo.chmod(0o755)
    # Inherited BASH_ENV can restore the turn's PATH and run real cargo instead of the stub.
    env = {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", os.defpath),
           "HOME": str(tmp_path / "home"), "CAPTURE": str(capture),
           "TICO_ENVIRONMENTS_DIR": str(env_dir.parent)}
    for args, slug in [(["--env", "acme"], "acme"), ([], "")]:
        result = subprocess.run(["bash", str(root / "scripts/app.sh"), *args, "check"], capture_output=True, text=True, check=True, env=env)
        values = json.loads(capture.read_text())
        assert "TICO_TRAY_LABEL" not in values
        assert values["TICO_ENV_SLUG"] == slug
        assert values["TICO_APP_NAME"] == (ENTRY["app_name"] if slug else "Tico")
        if slug:
            assert values["TICO_HUB_URL"] == ENTRY["url"] + "/"
        assert ENTRY["app_name"] not in result.stdout + result.stderr
        assert "A1" not in result.stdout + result.stderr


def test_public_workflow_and_company_ciphertext_artifact_boundary():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[2]
    public = (root / ".github/workflows/app.yml").read_text()
    assert "vars.TICO_" not in public and "TICO_COMPANY_APPS" not in public
    workflow = yaml.safe_load((root / ".github/workflows/company-app.yml").read_text())
    upload = next(step for step in workflow["jobs"]["build"]["steps"] if "actions/upload-artifact@" in step.get("uses", ""))
    assert upload["with"]["path"] == "out/bundles.enc"
    release = yaml.safe_load((root / ".github/workflows/release.yml").read_text())
    # The server release waits only for its validation run (whose binaries it attaches) and the quick Mac-binary
    # decision, never for desktop or Mac builds.
    assert release["jobs"]["release"].get("needs") == ["validated", "runner-changes"]
    assert "release" in release["jobs"]["macos-assets"]["needs"]
    assert "release" in release["jobs"]["desktop-assets"]["needs"]
    assert release["jobs"]["companies"]["strategy"]["fail-fast"] is False


def test_optional_release_branches_start_after_core_publication():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[2]
    jobs = yaml.safe_load((root / ".github/workflows/release.yml").read_text())["jobs"]

    def ancestors(job):
        needs = jobs[job].get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        return set(needs).union(*(ancestors(parent) for parent in needs))

    core = {"validated", "runner-changes", "release"}
    assert ancestors("release") == core - {"release"}
    for job in jobs.keys() - core:
        assert "release" in ancestors(job), f"{job} competes with core publication for job slots"
