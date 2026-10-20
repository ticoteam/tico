"""App aliases cannot widen the cookie-free visual or runner front doors."""
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = "app.acme.example"
RUNNER = "runner.acme.example"
VISUAL = "visuals.acme-files.example"


def write_config(directory, aliases, **extra):
    env = {key:value for key,value in os.environ.items() if not key.startswith("TICO_")}
    env.update(TICO_TUNNEL_DIR=str(directory), TICO_DOMAIN=APP, TICO_DOMAIN_ALIASES=aliases,
               TICO_RUNNER_URL="https://"+RUNNER, TICO_VISUAL_ORIGIN="https://"+VISUAL)
    return subprocess.run(["bash", str(ROOT/"docker/entrypoint.sh"), "tunnel-config"],
                          capture_output=True, text=True, env={**env, **extra})


def test_aliases_survive_restart_and_removal_without_widening_restricted_routes(tmp_path):
    aliases = " Old.Acme.Example,old.acme.example,APP.ACME.EXAMPLE,legacy.acme.example "
    expected = [
        {"hostname":APP,"service":"http://server:8765"},
        {"hostname":"old.acme.example","service":"http://server:8765"},
        {"hostname":"legacy.acme.example","service":"http://server:8765"},
        {"hostname":RUNNER,"path":"^/(?:api/v2|download)(?:/.*)?$","service":"http://server:8765"},
        {"hostname":VISUAL,"path":"^/visuals/","service":"http://server:8765"},
        {"service":"http_status:404"},
    ]
    config = tmp_path/"cloudflared.yml"
    for _ in range(2):
        done = write_config(tmp_path, aliases)
        assert done.returncode == 0, done.stderr
        assert yaml.safe_load(config.read_text()) == {"ingress":expected}
        assert config.stat().st_mode & 0o777 == 0o644 and not list(tmp_path.glob("*.new"))
    assert write_config(tmp_path, "old.acme.example").returncode == 0
    assert yaml.safe_load(config.read_text())["ingress"] == expected[:2]+expected[3:]
    assert write_config(tmp_path, "").returncode == 0
    assert yaml.safe_load(config.read_text())["ingress"] == expected[:1]+expected[3:]
    # A valid canonical runner URL intentionally has no separate restricted-host rule.
    assert write_config(tmp_path, "old.acme.example", TICO_RUNNER_URL="https://"+APP+"/").returncode == 0
    assert yaml.safe_load(config.read_text())["ingress"] == expected[:2]+expected[4:]
    # Only the server receives the optional setting; no credential or cookie reaches the connector with it.
    services = yaml.safe_load((ROOT/"compose.yaml").read_text())["services"]
    assert "TICO_DOMAIN_ALIASES" in services["server"]["environment"]
    assert "TICO_DOMAIN_ALIASES" not in services["cloudflared"]["environment"]


def test_bad_aliases_leave_the_previous_routes_intact_and_never_log_the_value(tmp_path):
    assert write_config(tmp_path, "old.acme.example").returncode == 0
    config = tmp_path/"cloudflared.yml"
    before = config.read_bytes()
    bad_values = ["*.private.example", "https://secret@private.example", "private.example\n  - service: http://other",
                  "private.example,,other.example", "private.example,", "-private.example", "private..example",
                  "a"*64+".example", "127.0.0.1", "\u212Aelvin.example"]
    for aliases in bad_values:
        done = write_config(tmp_path, aliases)
        assert done.returncode != 0 and "TICO_DOMAIN_ALIASES" in done.stderr
        assert aliases not in done.stderr and not done.stdout
        assert config.read_bytes() == before and not list(tmp_path.glob("*.new"))


def test_aliases_cannot_promote_runner_or_visual_hosts_even_when_visual_setting_is_invalid(tmp_path):
    assert write_config(tmp_path, "old.acme.example").returncode == 0
    config = tmp_path/"cloudflared.yml"
    before = config.read_bytes()
    for aliases, visual in [(RUNNER,"https://"+VISUAL), (VISUAL.upper(),"https://"+VISUAL),
                            (VISUAL,"https://"+VISUAL+"/mistake"), (VISUAL,"http://"+VISUAL)]:
        done = write_config(tmp_path, aliases, TICO_VISUAL_ORIGIN=visual)
        assert done.returncode != 0 and "TICO_DOMAIN_ALIASES" in done.stderr
        assert config.read_bytes() == before and not list(tmp_path.glob("*.new"))
    # These lose their host in the legacy helper; aliases must not turn that loss into a full-app front door.
    for runner in ["https://"+RUNNER+"?mistake=1", "https://"+RUNNER+".", "https://secret@"+RUNNER]:
        done = write_config(tmp_path, RUNNER, TICO_RUNNER_URL=runner)
        assert done.returncode != 0 and "TICO_DOMAIN_ALIASES" in done.stderr
        assert runner not in done.stderr and not done.stdout
        assert config.read_bytes() == before and not list(tmp_path.glob("*.new"))
    legacy = tmp_path/"legacy"
    legacy.mkdir()
    assert write_config(legacy, "", TICO_RUNNER_URL="https://"+RUNNER+"?mistake=1").returncode == 0
    assert yaml.safe_load((legacy/"cloudflared.yml").read_text())["ingress"] == [
        {"hostname":APP,"service":"http://server:8765"},
        {"hostname":VISUAL,"path":"^/visuals/","service":"http://server:8765"},
        {"service":"http_status:404"},
    ]
