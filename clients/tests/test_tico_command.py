"""`tico` (the bot command) and its `hub` alias: the computer's verbs moved, and an old-name call says so once."""
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock

import pytest

from clients import hubcli, tico

ROOT = Path(__file__).resolve().parents[2]
BOT_ENV = ("TICO_BOT", "HUB_BOT", "HUB_EMPLOYEE")


@pytest.fixture
def outside_a_turn(monkeypatch):
    for name in BOT_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("argv, verb", [
    (["restart"], "restart"),
    (["restart", "bot"], "restart"),
    (["-e", "acme", "install", "bot"], "install"),
    (["--env=acme", "status"], "status"),
    (["update"], "update"),
    (["bot", "create", "acme-bot"], "bot create"),
    (["logs", "bot", "-n", "20"], "logs"),
])
def test_a_computer_verb_names_its_verb(outside_a_turn, argv, verb):
    assert hubcli.moved_verb(argv) == verb


@pytest.mark.parametrize("argv", [
    ["task", "show", "acme-1"],
    ["update", "create", "--title", "x"],        # the updates command, not the computer's update
    ["bot", "list"],
    ["bot", "status", "acme"],
    [],
])
def test_bot_commands_name_no_computer_verb(outside_a_turn, argv):
    assert hubcli.moved_verb(argv) is None


@pytest.mark.parametrize("name", BOT_ENV)
def test_inside_a_turn_a_computer_verb_is_refused_and_nothing_runs(monkeypatch, capsys, name):
    for other in BOT_ENV:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(name, "acme-bot")
    with mock.patch("os.execv") as execv:
        assert hubcli.main(["status"]) == 2
    execv.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "This moved: tico-computer status\n"


def test_outside_a_turn_a_computer_verb_is_forwarded_to_tico_computer(outside_a_turn, capsys):
    with mock.patch("os.execv") as execv:
        hubcli.main(["-e", "acme", "restart", "bot"])
    script = str(ROOT / "scripts" / "tico-computer")
    execv.assert_called_once_with(script, [script, "-e", "acme", "restart", "bot"])
    assert capsys.readouterr().err == "tico restart moved to tico-computer restart; forwarding\n"


def test_the_alias_says_hub_when_it_forwards(outside_a_turn, monkeypatch, capsys):
    monkeypatch.setenv("TICO_CLI_ALIAS", "hub")
    with mock.patch("os.execv"):
        hubcli.main(["install", "bot"])
    assert capsys.readouterr().err == "hub install moved to tico-computer install; forwarding\n"


def test_bot_create_outside_a_turn_is_still_a_guarded_line(outside_a_turn, capsys):
    with mock.patch("os.execv") as execv:
        assert hubcli.main(["bot", "create", "acme-bot"]) == 2
    execv.assert_not_called()
    assert capsys.readouterr().err == "This moved: tico-computer bot create\n"


def test_the_bot_command_and_the_alias_refuse_a_computer_verb_inside_a_turn(monkeypatch):
    env = {k: v for k, v in os.environ.items() if k not in BOT_ENV}
    env["HUB_BOT"] = "acme-bot"
    for script in ("tico", "hub"):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "install", "bot"], env=env,
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 2, script
        assert done.stdout == "" and done.stderr == "This moved: tico-computer install\n", script


class Recorder(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        Recorder.seen.append(dict(self.headers))
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Recorder.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), Recorder)
    Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_an_alias_call_says_so_on_its_first_request_only(server, monkeypatch):
    monkeypatch.setattr(tico, "_ALIAS_SENT", [])
    monkeypatch.setenv("TICO_CLI_ALIAS", "hub")
    monkeypatch.setenv("TICO_CLI_COMMAND", "task show")
    client = tico.Client(server, "tok", retries=0)
    client.get("/api/v2/whoami")
    client.get("/api/v2/whoami")
    first, second = Recorder.seen
    assert first.get("X-Tico-Alias") == "cli" and first.get("X-Tico-Alias-Command") == "task show"
    assert "X-Tico-Alias" not in second


def test_a_tico_call_sends_no_alias_header(server, monkeypatch):
    monkeypatch.setattr(tico, "_ALIAS_SENT", [])
    monkeypatch.delenv("TICO_CLI_ALIAS", raising=False)
    tico.Client(server, "tok", retries=0).get("/api/v2/whoami")
    assert "X-Tico-Alias" not in Recorder.seen[0]
