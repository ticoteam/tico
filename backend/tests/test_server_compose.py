"""The server compose file pins its updater to the release, and an update leaves the updater running."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STORAGE_KEYS = {"TICO_BLOB_BUCKET", "TICO_BLOB_REGION", "TICO_BLOB_ENDPOINT", "TICO_UPLOAD_MAX_BYTES",
                "TICO_BLOB_ACCESS_KEY_ID", "TICO_BLOB_SECRET_ACCESS_KEY"}


def test_server_forwards_storage_without_operator_aws_settings_in_checkout_and_release_bundle():
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
        assert STORAGE_KEYS <= set(environment)
        assert all(environment[key] is None for key in STORAGE_KEYS)
        assert {"LITESTREAM_ACCESS_KEY_ID", "LITESTREAM_SECRET_ACCESS_KEY"} <= set(environment)
        assert not {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}.intersection(environment)
        assert "CLOUDFLARE_TUNNEL_TOKEN" not in environment
        for name, service in services.items():
            if name != "server":
                assert not STORAGE_KEYS.intersection(service.get("environment", {}))


def test_a_server_update_recreates_only_the_server_never_the_updater(monkeypatch, tmp_path):
    for key, value in {"TICO_UPDATER_MODE": "", "TICO_PROJECT_DIR": str(tmp_path), "TICO_COMPOSE_FILE": "", "TICO_UPDATER_BUNDLE": "never"}.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("tico_updater_server_pin", ROOT / "docker/updater.py")
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    calls = []
    monkeypatch.setattr(updater, "compose", lambda *a, tag=None, **k: calls.append(a) or "")
    monkeypatch.setattr(updater, "running_image", lambda: ("sha256:old", "v0.1.0"))
    monkeypatch.setattr(updater, "healthy", lambda seconds: True)
    monkeypatch.setattr(updater, "pinned_services", lambda version: [])
    monkeypatch.setattr(updater, "check_switched", lambda version, release: None)
    (tmp_path / ".env").write_text("TICO_URL=x\n")
    updater.update("v0.2.0")
    assert updater.status["state"] == "healthy"
    # the snapshot (`exec server python ...`) and the switch (`up ... server`) touch the server, never the updater service
    assert all("server" in a for a in calls) and not any("updater" in a for a in calls)
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


def test_the_server_and_the_slack_gateway_get_the_decision_model_keys_but_not_other_services_secrets():
    import yaml
    services = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]
    keys = {"TYPESAFE_API_KEY", "TICO_TYPESAFE_SECRET_ARN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            "XAI_API_KEY", "OPENROUTER_API_KEY"}
    for name in ("server", "slack"):
        env = set(services[name]["environment"])
        assert keys <= env and "CLOUDFLARE_TUNNEL_TOKEN" not in env
