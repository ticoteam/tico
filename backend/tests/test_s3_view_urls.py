"""TICO_S3_VIEW_URLS: read from the environment, validated, and handed to signed-in clients in /api/v2/config."""
import logging

from backend.config import Settings
from backend.onboarding import config_view
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401

SETTING = "acme-files=https://d1234example.cloudfront.net/, Not_A_Bucket=https://x.example, media.acme=http://cdn.acme.example"


def test_the_server_reads_the_setting_and_drops_bad_entries(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("TICO_S3_VIEW_URLS", SETTING)
    with caplog.at_level(logging.WARNING, logger="tico.config"):
        settings = Settings.from_env()
    assert settings.s3_view_urls == {"acme-files": "https://d1234example.cloudfront.net"}
    assert len([r for r in caplog.records if "TICO_S3_VIEW_URLS" in r.getMessage()]) == 2
    monkeypatch.delenv("TICO_S3_VIEW_URLS")
    assert Settings.from_env().s3_view_urls == {}


def test_signed_in_callers_read_the_mapping_and_nobody_else(environment):
    api = environment(s3_view_urls=SETTING)
    expected = {"acme-files": "https://d1234example.cloudfront.net"}
    assert api.get("/api/v2/config", headers=signed_in()).json()["s3_view_urls"] == expected
    assert api.get("/api/v2/config", headers=as_person(api, "riley")).json()["s3_view_urls"] == expected
    assert api.get("/api/me", headers=signed_in()).json()["config"]["s3_view_urls"] == expected
    assert api.get("/api/v2/config").status_code in (401, 403)
    with api.app.state.store.read() as c:
        assert "s3_view_urls" not in config_view(c, api.app.state.store.settings, None)


def test_without_the_setting_the_mapping_is_empty(environment):
    assert environment().get("/api/v2/config", headers=signed_in()).json()["s3_view_urls"] == {}
