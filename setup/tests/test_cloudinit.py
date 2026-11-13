import subprocess

import pytest
import yaml

from setup import cloudinit, envfile
from setup.settings import Settings

TEMPLATES = ["tico-server.yaml", "tico-runner.yaml"]


def load(name):
    return yaml.safe_load((cloudinit.TEMPLATE_DIR / name).read_text())


def files(doc):
    return {f["path"]: f for f in doc["write_files"]}


def script(doc, path):
    return files(doc)[path]["content"]


def settings(**kw):
    base = dict(
        domain="tico.example.com",
        company="Acme Inc",
        owner_email="me@example.com",
        auth="google",
        client_id="cid",
        client_secret="S3CRET$x",
        front_door="caddy",
    )
    return Settings(**{**base, **kw})


@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_are_cloud_config_with_private_inputs_and_a_bootstrap(name):
    text = (cloudinit.TEMPLATE_DIR / name).read_text()
    assert text.startswith("#cloud-config\n")
    doc = yaml.safe_load(text)
    f = files(doc)
    inputs = next(p for p in f if p.startswith("/etc/tico/") and p.endswith(".env"))
    assert f[inputs]["permissions"] == "0600"
    boot = next(p for p in f if "bootstrap" in p)
    assert (
        f[boot]["permissions"] == "0700"
        and f[boot]["content"].startswith("#!/bin/bash\n")
        and "\nset -euo pipefail\n" in f[boot]["content"]
    )
    assert doc["runcmd"] == [[boot]]
    assert cloudinit.BEGIN in f[inputs]["content"] and cloudinit.END in f[inputs]["content"]


def test_server_render_puts_the_env_in_the_inputs_block_and_stays_valid_yaml():
    s = settings()
    text = cloudinit.server_user_data(s.to_env(), version="v1.2.3", server_ip="203.0.113.7", allow_ssh=True)
    doc = yaml.safe_load(text)
    env = envfile.parse(files(doc)["/etc/tico/tico.env"]["content"])
    assert (
        env["TICO_CLOUD_VERSION"] == "v1.2.3"
        and env["TICO_CLOUD_SERVER_IP"] == "203.0.113.7"
        and env["TICO_CLOUD_ALLOW_SSH"] == "1"
    )
    assert env["TICO_DOMAIN"] == "tico.example.com" and env["COMPOSE_PROFILES"] == "caddy,updater"
    assert (
        env["TICO_TEAM_NAME"] == env["TICO_COMPANY_NAME"] == "Acme Inc" and env["TICO_OIDC_CLIENT_SECRET"] == "S3CRET$x"
    )
    assert "TICO_TAG" not in env  # the bootstrap pins it to the version
    assert "you@example.com" not in text  # the template's example values are all replaced
    assert files(doc)["/usr/local/sbin/tico-bootstrap"]["content"] == script(
        load("tico-server.yaml"), "/usr/local/sbin/tico-bootstrap"
    )


def test_a_cloud_runner_offers_its_screen_unless_its_input_says_0():
    def inputs(**kw):
        text = cloudinit.runner_user_data(
            url="https://tico.example.com", code="c0de", label="cloud", version="v1.2.3", **kw
        )
        return files(yaml.safe_load(text))["/etc/tico/runner.env"]["content"]

    assert "\nTICO_SCREEN=1\n" in inputs() and "\nTICO_SCREEN=0\n" in inputs(screen=False)
    line = next(
        l
        for l in script(load("tico-runner.yaml"), "/usr/local/sbin/tico-runner-bootstrap").splitlines()
        if l.strip().startswith("screen=")
    )
    for value, flag in (("1", ""), ("", ""), ("0", "--no-screen")):
        run = subprocess.run(
            ["bash", "-c", f'val() {{ echo "{value}"; }}; {line.strip()}; echo "${{screen[@]}}"'],
            capture_output=True,
            text=True,
        )
        assert run.stdout.strip() == flag
