"""Private company configuration must not escape through logs or release artifacts."""
import json

import pytest

from scripts.company_apps import Invalid, key, main, matrix, parse
from scripts.company_app_publisher import policies

ENTRY = {"slug": "acme", "id": "12345678-1234-4234-8234-123456789abc", "app_name": "Acme Tico",
         "url": "https://tico.example.com", "icon_url": "/api/v2/team/icon",
         "deploy_role_arn": "arn:aws:iam::123456789012:role/publisher", "bucket": "acme-files"}


def test_private_matrix_and_all_dry_runs_expose_only_hashes(monkeypatch, capsys):
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([ENTRY]))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert matrix(parse(json.dumps([ENTRY]))) == {"include": [{"company": key(ENTRY)}]}
    for action in ("matrix", "configure", "build", "pack", "unpack", "publish"):
        assert main([action, "--dry-run", "--company", key(ENTRY)]) == 0
        out = capsys.readouterr()
        assert key(ENTRY) in out.out
        assert all(v not in out.out + out.err for v in ENTRY.values())


@pytest.mark.parametrize("raw", ["{", "{}", "[{}]", json.dumps([{**ENTRY, "url": "http://example.com"}]),
    json.dumps([{**ENTRY, "runner_url": "http://example.com"}]),
    json.dumps([{**ENTRY, "icon_url": "http://example.com/icon.png"}]),
    json.dumps([{**ENTRY, "app_name": "bad\nname"}]), json.dumps([{**ENTRY, "prefix": "../escape"}]),
    json.dumps([ENTRY, ENTRY])])
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


def test_publisher_policies_are_tag_and_prefix_scoped():
    provider, trust, access = policies("123456789012", "acme-files", "team",
        subject="repo:ticoteam/tico:ref:refs/tags/v*")
    condition = trust["Statement"][0]["Condition"]
    assert condition["StringLike"]["token.actions.githubusercontent.com:sub"] == "repo:ticoteam/tico:ref:refs/tags/v*"
    assert condition["StringEquals"]["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    assert access["Statement"][0]["Resource"] == "arn:aws:s3:::acme-files/team/releases/app/*"
    assert access["Statement"][1]["Condition"] == {"StringLike": {"s3:prefix": "team/releases/app/*"}}
    assert provider.endswith(":oidc-provider/token.actions.githubusercontent.com")


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
    assert "TICO_TRAY_LABEL=acm1\n" in env_file.read_text()
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
    assert "TICO_TRAY_LABEL=\n" in (tmp_path / "env").read_text()


@pytest.mark.parametrize("label", ["PrivateLongLabel", "A B", "é", "A\nB", "\u202eAB", 12])
def test_invalid_tray_labels_never_escape_errors(label, monkeypatch, capsys):
    monkeypatch.setenv("TICO_COMPANY_APPS", json.dumps([{**ENTRY, "tray_label": label}]))
    assert main(["configure", "--company", key(ENTRY)]) == 1
    out = capsys.readouterr()
    assert str(label) not in out.out + out.err


def test_local_check_keeps_company_identity_and_clears_inherited_label(tmp_path, monkeypatch):
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
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CAPTURE", str(capture))
    monkeypatch.setenv("TICO_ENVIRONMENTS_DIR", str(env_dir.parent))
    monkeypatch.setenv("TICO_TRAY_LABEL", "LEAK")
    monkeypatch.delenv("TICO_ENV", raising=False)
    for args, label, slug in [(["--env", "acme"], "A1", "acme"), ([], "", "")]:
        result = subprocess.run(["bash", str(root / "scripts/app.sh"), *args, "check"], capture_output=True, text=True, check=True)
        values = json.loads(capture.read_text())
        assert values["TICO_TRAY_LABEL"] == label
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
    assert release["jobs"]["release"]["needs"] == "desktop"
    assert release["jobs"]["companies"]["strategy"]["fail-fast"] is False
