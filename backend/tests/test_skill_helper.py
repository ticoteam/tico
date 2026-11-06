"""clients/skill_helper.mjs as a hook: it reports only known names while reporting is on, and always exits 0 quietly."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[2] / "clients" / "skill_helper.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


class Recorder(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("content-length") or 0)
        self.server.seen.append((self.path, self.headers.get("authorization"), json.loads(self.rfile.read(size) or b"{}")))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    httpd.seen = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()


def report(tmp_path, url, hook, trigger="model", reporting=True):
    (tmp_path / "skills.json").write_text(json.dumps({"server": url, "token": "tico_sd_x", "device_id": "skd_1",
                                                      "reporting": reporting, "skills": ["deploy"]}))
    began = time.monotonic()
    done = subprocess.run([NODE, str(HELPER), "report", "--trigger", trigger], input=json.dumps(hook),
                          capture_output=True, text=True, timeout=30, env={**os.environ, "TICO_SKILLS_DIR": str(tmp_path)})
    return done, time.monotonic() - began


def test_report_posts_only_known_names_while_reporting_is_on(tmp_path, server):
    url = f"http://127.0.0.1:{server.server_address[1]}"
    used = {"session_id": "sess-1", "tool_name": "Skill", "tool_input": {"skill": "deploy:deploy", "args": "secret"},
            "tool_use_id": "toolu_1", "prompt_id": "p1", "hook_event_name": "PostToolUse"}
    done, _ = report(tmp_path, url, used)
    assert done.returncode == 0 and done.stdout == ""
    typed = {"session_id": "sess-1", "expansion_type": "slash_command", "command_name": "deploy", "prompt_id": "p2",
             "prompt": "/deploy the private words"}
    assert report(tmp_path, url, typed, trigger="person")[0].returncode == 0
    for hook, kw in ((dict(used, tool_input={"skill": "other:other"}), {}),       # not one of this person's skills
                     (dict(used, tool_input={"skill": "other:deploy"}), {}),      # another plugin's skill of that name
                     (used, {"reporting": False})):
        assert report(tmp_path, url, hook, **kw)[0].returncode == 0
    assert [(p, a) for p, a, _ in server.seen] == [("/api/v2/skills/usage", "Bearer tico_sd_x")] * 2
    model, person = (body["events"][0] for _, _, body in server.seen)
    key = hashlib.sha256(b"sess-1").hexdigest()
    assert {k: model[k] for k in ("name", "trigger", "session_key", "tool_use_id")} == \
        {"name": "deploy", "trigger": "model", "session_key": key, "tool_use_id": "toolu_1"}
    assert (person["trigger"], person["tool_use_id"]) == ("person", "p2")
    assert "secret" not in json.dumps(server.seen) and "private" not in json.dumps(server.seen)


def test_a_typed_name_wins_over_the_model_skill_call_it_causes(tmp_path, server):
    url = f"http://127.0.0.1:{server.server_address[1]}"
    typed = {"session_id": "sess-2", "expansion_type": "slash_command", "command_name": "deploy:deploy", "prompt_id": "p1",
             "prompt": "/deploy:deploy the private words"}
    echo = {"session_id": "sess-2", "tool_name": "Skill", "tool_input": {"skill": "deploy:deploy"}, "tool_use_id": "toolu_1"}
    assert report(tmp_path, url, typed, trigger="person")[0].returncode == 0
    assert report(tmp_path, url, echo)[0].returncode == 0                                    # same session, within 30 s
    assert report(tmp_path, url, dict(echo, session_id="sess-3", tool_use_id="toolu_2"))[0].returncode == 0
    mark = tmp_path / "skills-typed" / (hashlib.sha256(b"sess-2").hexdigest() + ".json")
    assert set(json.loads(mark.read_text())) == {"skill", "ts"} and "private" not in mark.read_text()
    mark.write_text(json.dumps({"skill": "deploy", "ts": int(time.time() * 1000) - 31_000}))
    assert report(tmp_path, url, dict(echo, tool_use_id="toolu_3"))[0].returncode == 0     # a later model call counts
    assert [(e["trigger"], e["tool_use_id"]) for _, _, body in server.seen for e in body["events"]] == \
        [("person", "p1"), ("model", "toolu_2"), ("model", "toolu_3")]


def test_report_exits_0_quietly_when_the_server_is_down(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    hook = {"session_id": "s", "tool_name": "Skill", "tool_input": {"skill": "deploy"}, "tool_use_id": "t"}
    done, took = report(tmp_path, f"http://127.0.0.1:{port}", hook)
    assert done.returncode == 0 and done.stdout == "" and took < 10
    bad = subprocess.run([NODE, str(HELPER), "report", "--trigger", "model"], input="not json", capture_output=True,
                         text=True, timeout=30, env={**os.environ, "TICO_SKILLS_DIR": str(tmp_path)})
    assert bad.returncode == 0 and bad.stdout == ""


def test_auth_header_prints_one_json_object(tmp_path):
    env = {**os.environ, "TICO_SKILLS_DIR": str(tmp_path)}
    empty = subprocess.run([NODE, str(HELPER), "auth-header"], capture_output=True, text=True, timeout=30, env=env)
    assert empty.returncode == 0 and json.loads(empty.stdout) == {}
    (tmp_path / "skills.json").write_text(json.dumps({"server": "https://tico.acme.example", "token": "tico_sd_x"}))
    full = subprocess.run([NODE, str(HELPER), "auth-header"], capture_output=True, text=True, timeout=30, env=env)
    assert json.loads(full.stdout) == {"Authorization": "Bearer tico_sd_x"}
