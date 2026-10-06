"""The systemd user units `scripts/tico install` writes for a Linux checkout runner."""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from runner import systemd_units as su

ROOT = Path(__file__).resolve().parents[2]
KW = dict(python="/srv/tico/runtime/runner-venv/bin/python", config="/home/ana/.config/tico/runner.json", root="/srv/tico",
          log="/home/ana/.config/tico/logs/tico-bot.log", path="/usr/local/bin:/usr/bin", home="/home/ana", user="ana")


def test_the_runner_unit_restarts_always_and_says_who_supervises_it():
    text = su.render("bot", **KW)
    assert 'ExecStart="/srv/tico/runtime/runner-venv/bin/python" "-m" "runner" "--config" "/home/ana/.config/tico/runner.json" "run"' in text
    assert "Restart=always" in text and "RestartSec=10" in text and "StartLimitIntervalSec=0" in text
    assert "WorkingDirectory=/srv/tico" in text and "WantedBy=default.target" in text
    assert 'Environment="TICO_SUPERVISED=1"' in text and 'Environment="TICO_SYSTEMD_UNIT=tico-bot.service"' in text
    assert "StandardOutput=append:/home/ana/.config/tico/logs/tico-bot.log" in text


def test_each_helper_runs_its_own_command_and_an_environment_has_its_own_names():
    for kind in ("connectors", "close-calls", "importers"):
        text = su.render(kind, **KW)
        assert f'"--config" "/home/ana/.config/tico/runner.json" "{kind}"' in text
        assert f"TICO_SYSTEMD_UNIT=tico-{kind}.service" in text
    acme = su.render("bot", env_slug="acme", **KW)
    assert "TICO_SYSTEMD_UNIT=tico-acme-bot.service" in acme and "Description=Tico runner (acme)" in acme
    assert su.helper_units("tico-acme-bot.service") == {k: f"tico-acme-{k}.service" for k in ("connectors", "close-calls", "importers")}
    assert su.helper_units("tico-bot.service")["importers"] == "tico-importers.service"
    assert su.helper_units("something.service") == {}


def test_a_path_with_spaces_or_percent_signs_stays_one_word_and_no_variable_expands():
    text = su.render("bot", **{**KW, "python": "/opt/my tico/py", "config": "/cfg/100%/$HOME/r.json"})
    assert '"/opt/my tico/py"' in text and '"/cfg/100%%/$$HOME/r.json"' in text


def test_the_local_server_reads_its_settings_from_the_environments_file():
    text = su.render("api", python="/py", config="", root="/srv/tico", log="/l", path="/usr/bin", env_slug="acme",
                     env_file="/home/ana/.config/tico/environments/acme/server.env", port="8123")
    assert "EnvironmentFile=/home/ana/.config/tico/environments/acme/server.env" in text
    assert '"uvicorn" "backend.app:create_app" "--factory"' in text and '"--port" "8123"' in text
    with pytest.raises(ValueError):
        su.render("processing", **KW)


def test_a_unit_file_is_private_and_lands_where_systemd_looks(tmp_path):
    path = su.write(tmp_path / "systemd" / "user", "bot", su.render("bot", **KW))
    assert path.name == "tico-bot.service" and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert su.is_installed("tico-bot.service", tmp_path / "systemd" / "user")
    assert su.user_unit_dir({"XDG_CONFIG_HOME": "/x"}) == Path("/x/systemd/user")
    assert su.user_unit_dir({"TICO_SYSTEMD_USER_DIR": "/y"}) == Path("/y")


# -- scripts/tico on Linux, with systemctl and loginctl faked ----------------------------------------------

def fake_linux(tmp_path, linger="no"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "systemctl").write_text(
        f'#!/bin/sh\necho "systemctl $*" >> {log}\n'
        'case "$*" in *"show-environment"*) echo HOME=/x;; esac\n'
        'case "$*" in *"show tico-bot.service"*) printf "ActiveState=active\\nSubState=running\\nMainPID=77\\nExecMainStatus=0\\n";; esac\nexit 0\n')
    (bin_dir / "loginctl").write_text(f'#!/bin/sh\necho "loginctl $*" >> {log}\necho {linger}\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    config = tmp_path / "runner.json"
    config.write_text('{"runner_id": "r1", "url": "https://hub.example"}')
    config.chmod(0o600)
    env = {**os.environ, "TICO_OS": "Linux", "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path / "home"),
           "TICO_RUNNER_PYTHON": sys.executable, "TICO_RUNNER_CONFIG": str(config), "TICO_SYSTEMD_USER_DIR": str(tmp_path / "units"),
           "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "USER": "ana"}
    env.pop("TICO_ENV", None)
    (tmp_path / "home").mkdir()
    return env, log


def tico(env, *args):
    return subprocess.run([str(ROOT / "scripts" / "tico"), *args], capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.slow
def test_install_on_linux_writes_units_starts_them_and_explains_linger(tmp_path):
    env, log = fake_linux(tmp_path, linger="no")
    done = tico(env, "install")
    assert done.returncode == 0, done.stderr
    for kind in su.KINDS:
        unit = tmp_path / "units" / f"tico-{kind}.service"
        assert unit.is_file() and "Restart=always" in unit.read_text()
        assert f"systemctl --user restart tico-{kind}.service" in log.read_text()
        assert f"installed tico-{kind}.service" in done.stdout
    assert "systemctl --user daemon-reload" in log.read_text()
    assert "loginctl enable-linger ana" in done.stdout                   # linger is off: say so


@pytest.mark.slow
def test_install_on_linux_is_quiet_about_linger_when_it_is_on_and_handles_one_job(tmp_path):
    env, log = fake_linux(tmp_path, linger="yes")
    done = tico(env, "install", "connectors")
    assert done.returncode == 0 and "enable-linger" not in done.stdout
    assert [p.name for p in (tmp_path / "units").iterdir()] == ["tico-connectors.service"]
    gone = tico(env, "uninstall", "connectors")
    assert gone.returncode == 0 and not list((tmp_path / "units").iterdir())
    assert "systemctl --user disable --now tico-connectors.service" in log.read_text()


@pytest.mark.slow
def test_status_and_restart_on_linux_read_systemd(tmp_path):
    env, log = fake_linux(tmp_path)
    installed = tico(env, "install", "bot")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    restarted = tico(env, "restart", "bot")
    assert restarted.returncode == 0 and "restarted tico-bot.service" in restarted.stdout
    status = tico(env, "status")                                          # the cloud is unreachable here; the job lines come first
    assert "bot: running (pid 77)" in status.stdout and "connectors: not installed" in status.stdout


def test_install_refuses_a_public_runner_registration(tmp_path):
    env, log = fake_linux(tmp_path)
    Path(env['TICO_RUNNER_CONFIG']).chmod(0o644)
    done = tico(env, 'install', 'bot')
    assert done.returncode == 1 and 'runner config must have mode 600' in done.stdout
    assert not (tmp_path / 'units').exists()
    assert 'restart' not in log.read_text()
