"""TICO_REHEARSAL=1: a server on a copy of real data runs its migrations and nothing else. It starts no timers,
writes nothing to the backup location and sends nothing out, so restoring production data to try a move is safe."""
import stat
import textwrap
from types import SimpleNamespace

import pytest

from backend import census, releases, replication, support
from backend.blobs import Blobs
from backend.config import Settings
from backend.store import Problem
from backend.tests.test_onboarding import environment, signed_in  # noqa: F401
from backend.tests.test_replication import ENTRYPOINT, entrypoint, prepare  # noqa: F401


def stub_uvicorn(tmp_path):
    """A `uvicorn` that records what the server would start with instead of serving."""
    stub = tmp_path / "bin" / "uvicorn"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(textwrap.dedent("""\
        #!/bin/sh
        env | grep -E '^(TICO_[A-Z_]*|AWS_[A-Z_]*REGION)=' | sort > "$STUB_ENV"
        echo "uvicorn $@" >> "$STUB_LOG"
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return tmp_path / "server.env"


def serve(tmp_path, **extra):
    """`docker/entrypoint.sh server` with fake uvicorn and litestream. Returns (result, env the server got, litestream log)."""
    env_file = stub_uvicorn(tmp_path)
    base = {"TICO_COMPANY_NAME": "Acme", "TICO_OWNER_EMAIL": "owner@example.com", "TICO_AUTH_PROXY": "none",
            "TICO_LOCAL_OWNER_TOKEN_FILE": str(tmp_path / "token"), "STUB_ENV": str(env_file),
            "STUB_DB": str(tmp_path / "no-replica"), "STUB_FAIL": "", **extra}
    result = entrypoint(tmp_path, command="server", env=base)
    seen = dict(line.split("=", 1) for line in env_file.read_text().splitlines()) if env_file.exists() else {}
    log = (tmp_path / "stub.log").read_text() if (tmp_path / "stub.log").exists() else ""
    return result, seen, log


def test_no_sign_in_setup_runs_on_this_machine_and_a_domain_needs_one(tmp_path):
    result, seen, _ = serve(tmp_path, TICO_AUTH_PROXY="", TICO_BACKUP="off")
    assert result.returncode == 0, result.stderr
    assert seen["TICO_PUBLIC_URL"] == "http://127.0.0.1:8765" and seen["TICO_LOCAL_OWNER_TOKEN_FILE"]
    assert seen["TICO_TEAM_NAME"] == seen["TICO_COMPANY_NAME"] == "Acme"
    result, seen, _ = serve(tmp_path, TICO_TEAM_NAME="New team", TICO_AUTH_PROXY="", TICO_BACKUP="off")
    assert result.returncode == 0 and seen["TICO_TEAM_NAME"] == seen["TICO_COMPANY_NAME"] == "New team"
    result, seen, _ = serve(tmp_path, TICO_AUTH_PROXY="", TICO_BACKUP="off", TICO_PORT="8877")
    assert result.returncode == 0 and seen["TICO_PUBLIC_URL"] == "http://127.0.0.1:8877"
    result, seen, _ = serve(tmp_path, TICO_AUTH_PROXY="", TICO_BACKUP="off", TICO_PORT="8877", TICO_PUBLIC_URL="http://localhost:8877")
    assert result.returncode == 0 and seen["TICO_PUBLIC_URL"] == "http://localhost:8877"
    result, _, _ = serve(tmp_path, TICO_AUTH_PROXY="", TICO_DOMAIN="tico.acme.example", TICO_BACKUP="off")
    assert result.returncode != 0 and "needs sign-in" in result.stderr


def test_an_explicit_scheduler_off_is_kept_and_unset_means_on(tmp_path):
    result, seen, _ = serve(tmp_path, TICO_BACKUP="off", TICO_SCHEDULER="0")
    assert result.returncode == 0, result.stderr
    assert seen["TICO_SCHEDULER"] == "0"
    result, seen, _ = serve(tmp_path, TICO_BACKUP="off")
    assert result.returncode == 0, result.stderr
    assert seen["TICO_SCHEDULER"] == "1"


def test_an_empty_aws_region_is_unset_so_the_sdk_keeps_its_default(tmp_path):
    result, seen, _ = serve(tmp_path, TICO_BACKUP="off", AWS_REGION="us-west-2", AWS_DEFAULT_REGION="")
    assert result.returncode == 0, result.stderr
    assert seen["AWS_REGION"] == "us-west-2" and "AWS_DEFAULT_REGION" not in seen


def test_rehearsal_turns_off_the_scheduler_backups_and_everything_outbound(tmp_path):
    prepared, _ = prepare(tmp_path, TICO_BACKUP="off")           # the copy of the data: a database already on the volume
    assert prepared.returncode == 0, prepared.stderr
    (tmp_path / "stub.log").unlink(missing_ok=True)
    result, seen, log = serve(tmp_path, TICO_REHEARSAL="1", TICO_SCHEDULER="1", TICO_UPDATER_URL="http://updater:8080",
                              TICO_BACKUP_URL="s3://production-bucket/tico", TICO_UPDATE_CHECK="on",
                              TICO_SENTRY_DSN="https://x@o1.ingest.sentry.io/1")
    assert result.returncode == 0, result.stderr
    assert (seen["TICO_SCHEDULER"], seen["TICO_REHEARSAL"], seen["TICO_UPDATE_CHECK"]) == ("0", "1", "off")
    assert (seen["TICO_TELEMETRY"], seen["TICO_SUPPORT"], seen["TICO_BACKUP_MODE"]) == ("off", "off", "rehearsal")
    assert "TICO_UPDATER_URL" not in seen and "TICO_SENTRY_DSN" not in seen
    assert "replicate" not in log and "REHEARSAL: backups are off" in result.stdout      # no Litestream, and the log says so
    assert "uvicorn backend.app:create_app" in log


def test_a_rehearsal_never_writes_to_the_backup_location(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    result, _, log = serve(tmp_path, TICO_REHEARSAL="yes")        # a new volume, the tico-backups volume mounted
    assert result.returncode == 0, result.stderr
    assert not (backups / replication.MARKER).exists()
    assert "replicate" not in log


def test_the_rehearsal_reaches_the_replication_helpers():
    on = {"TICO_REHEARSAL": "1", "TICO_BACKUP_MODE": "remote", "TICO_BACKUP_URL": "s3://production-bucket/tico"}
    assert replication.status(on) == {"mode": "rehearsal", "last_replicated_at": None, "target_kind": "none", "warning": ""}
    assert replication.no_mirror(on) and replication.no_mirror({"TICO_BACKUP_MODE": "rehearsal"})
    assert not replication.no_mirror({"TICO_BACKUP_MODE": "remote"})
    assert replication.loop(on) is None                        # returns at once: no mirror is even opened


def test_the_server_reads_the_rehearsal_from_its_environment(monkeypatch, tmp_path):
    for name, value in {"TICO_DB": str(tmp_path / "hub.db"), "TICO_REHEARSAL": "1", "TICO_SCHEDULER": "1",
                        "TICO_SLACK_GATEWAY_ENABLED": "1", "TICO_SENTRY_DSN": "https://x@o1.ingest.sentry.io/1",
                        "TICO_POSTHOG_KEY": "phc_abc", "TYPESAFE_API_KEY": "fake-key", "TICO_TYPESAFE_SECRET_ARN": "fake-arn"}.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.rehearsal and settings.environment()["rehearsal"] is True and not settings.typesafe_api_key
    assert not settings.scheduler_enabled and not settings.slack_gateway_enabled
    assert (settings.sentry_dsn, settings.posthog_key) == ("", "")
    monkeypatch.delenv("TICO_REHEARSAL")
    monkeypatch.delenv("TICO_TYPESAFE_SECRET_ARN")
    settings = Settings.from_env()
    assert not settings.rehearsal and settings.scheduler_enabled and settings.environment()["rehearsal"] is False


def test_the_config_says_rehearsal_and_the_health_page_does_not_warn_about_backups(environment):
    api = environment(rehearsal=True)
    assert api.get("/api/v2/config", headers=signed_in()).json()["rehearsal"] is True
    assert api.get("/api/v2/config", headers=signed_in()).json()["backup"]["mode"] in (None, "rehearsal")
    assert environment().get("/api/v2/config", headers=signed_in()).json()["rehearsal"] is False


def test_a_rehearsal_sends_no_count_no_release_check_and_no_directory_read(environment, monkeypatch):
    api = environment(rehearsal=True)
    assert api.app.state.census.off_reason() == "TICO_REHEARSAL"
    monkeypatch.setenv("TICO_REHEARSAL", "1")
    monkeypatch.delenv("TICO_UPDATE_CHECK")
    assert not releases.Checker.enabled() and releases._updater() == ("", "")
    monkeypatch.setenv("TICO_UPDATER_URL", "http://updater:8080")
    assert releases._updater()[0] == ""
    assert census.env_off() == "TICO_REHEARSAL"
    with pytest.raises(Problem) as refused:
        api.app.state.directory.fetch("google")
    assert refused.value.code == "rehearsal"


def test_a_rehearsal_has_no_contact_support_and_says_why(environment, monkeypatch):
    api = environment(rehearsal=True)
    assert support.off_reason(api.app.state.store.settings) == "rehearsal"
    assert api.get("/api/v2/support/compose", headers=signed_in()).status_code == 409
    assert api.get("/api/v2/support/tickets", headers=signed_in()).json() == {"enabled": False, "tickets": [], "unread": 0}
    monkeypatch.setenv("TICO_REHEARSAL", "1")                    # the switch itself, whatever the settings say
    assert support.off_reason(environment().app.state.store.settings) == "rehearsal"


def test_a_rehearsal_adds_nothing_to_the_companys_bucket(tmp_path):
    blobs = Blobs(SimpleNamespace(blob_bucket="production-bucket", blob_dir=tmp_path, db_path=tmp_path / "hub.db", rehearsal=True),
                  s3=SimpleNamespace(put_object=lambda **kw: pytest.fail("wrote to the bucket")))
    with pytest.raises(Problem) as refused:
        blobs.put(b"file")
    assert refused.value.code == "rehearsal"
