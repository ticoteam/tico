"""scripts/ci/tell_release_owner.sh: CI hands a failed queue group, a retry-only pass or a red validation of main to the
Release Owner bot through an incoming webhook, signed the way Tico's own `hmac` preset checks."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

from backend import trigger_presets
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ci/tell_release_owner.sh"


def _serve(status=200):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(({k.lower(): v for k, v in self.headers.items()}, body))
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, calls


def _run(*args, **env):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "TICO_RELEASE_OWNER"))}
    return subprocess.run(["bash", str(SCRIPT), *args], env={**base, **env}, capture_output=True, text=True, timeout=60)


def test_a_hand_off_is_signed_so_tico_accepts_it_and_names_the_run():
    server, calls = _serve()
    try:
        result = _run(
            "queue-flaky",
            "backend/tests/test_a.py::test_b",
            TICO_RELEASE_OWNER_HOOK="http://127.0.0.1:%d/api/v2/hooks/release/x" % server.server_port,
            TICO_RELEASE_OWNER_HOOK_SECRET="s3cret",
            GITHUB_SERVER_URL="https://github.com",
            GITHUB_REPOSITORY="ticoteam/tico",
            GITHUB_RUN_ID="42",
            GITHUB_RUN_ATTEMPT="2",
            GITHUB_SHA="abc",
            GITHUB_EVENT_NAME="merge_group",
        )
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr
    [(headers, body)] = calls
    ok, why = trigger_presets._hmac(headers, body, "s3cret", 0)  # the check an `hmac` trigger runs on each call
    assert ok, why
    assert headers["idempotency-key"] == "42-2-queue-flaky"  # a resent call is the same delivery to Tico
    sent = json.loads(body)
    assert sent["kind"] == "queue-flaky" and sent["run"] == "https://github.com/ticoteam/tico/actions/runs/42"
    assert sent["detail"] == "backend/tests/test_a.py::test_b" and sent["event"] == "merge_group"


def test_without_the_hook_or_when_tico_refuses_it_never_fails_its_job():
    assert "nothing sent" in _run("queue-failed").stdout
    server, calls = _serve(status=500)
    try:
        result = _run(
            "validation-red",
            TICO_RELEASE_OWNER_HOOK="http://127.0.0.1:%d/" % server.server_port,
            TICO_RELEASE_OWNER_HOOK_SECRET="s",
        )
    finally:
        server.shutdown()
    assert result.returncode == 0 and "Release Owner not told" in result.stdout
    assert calls  # it tried


def test_queue_groups_and_red_validations_of_main_call_it_with_the_hook_secrets():
    checks = yaml.safe_load((ROOT / ".github/workflows/pr-checks.yml").read_text())["jobs"]
    owner = checks["release-owner"]
    assert owner["needs"] == ["checks", "timings"] and "merge_group" in owner["if"]
    assert checks["checks"]["outputs"]["failed"] == "${{ steps.verdict.outputs.failed }}"
    assert checks["timings"]["outputs"]["flaky"] == "${{ steps.flaky.outputs.tests }}"
    assert "release-owner" not in checks["checks"]["needs"]  # never part of the required check
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    runs = [
        step["run"]
        for step in owner["steps"] + ci["main-red"]["steps"]
        if "tell_release_owner.sh" in step.get("run", "")
    ]
    assert [run.split()[1] for run in runs] == ["queue-failed", "queue-flaky", "validation-red"]
    for step in owner["steps"] + ci["main-red"]["steps"]:
        if "tell_release_owner.sh" in step.get("run", ""):
            assert step["env"]["TICO_RELEASE_OWNER_HOOK"] == "${{ secrets.TICO_RELEASE_OWNER_HOOK }}"
