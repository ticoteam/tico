"""Every request to the Tico AI Gateway and Tico HQ carries Tico's own user agent (Dev 276). Cloudflare in front of HQ
answers a library's default one (`Python-urllib/3.12`) with 403 before the request reaches the Gateway, so a Decide
call sent without it never arrives, and with no other provider left nothing answers. CI talks to a fake Gateway
directly; here that fake refuses every agent that is not Tico's, the way the release guard's proxy does for the runner
(runner/tests/test_conformance.py `RefusingProxy`)."""

import json
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.tests.test_decide_gateway import answer_all
from backend.tests.test_judge import QUESTIONS
from clients import gateway as G
from clients import judge as J

KEY = "tgw-synthetic-fixture-key-0276"
ROOT = Path(__file__).resolve().parents[2]


class RefusingGateway:
    """The Gateway behind an edge that answers 403 to any user agent but Tico's: `decisions`, `chat/completions` and
    `key` as the Gateway answers them. `seen` keeps (method, path, user agent) for every request, refused or not."""

    def __init__(self):
        self.seen = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def edge(self):
                agent = self.headers.get("User-Agent", "")
                owner.seen.append((self.command, self.path, agent))
                if not agent.startswith("Tico-Client/"):
                    self.answer(403, {"error": "edge refused the user agent"})
                    return False
                return True

            def do_GET(self):
                if self.edge():
                    self.answer(200, {"status": "active", "month": "2026-10", "cap_usd": 50.0, "spent_usd": 1.0,
                                      "used": 0.02, "warning": False, "cap_reached": False})

            def do_POST(self):
                asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if not self.edge():
                    return
                if self.path.endswith("/decisions"):
                    self.answer(200, {"model": "tico-decide-1.0", "answers": answer_all(asked["questions"]),
                                      "usage": {"input_tokens": 120, "output_tokens": 0, "cost_usd": 0.00001}})
                else:
                    self.answer(200, {"choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/gateway"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@contextmanager
def refusing_gateway():
    fake = RefusingGateway()
    try:
        yield fake
    finally:
        fake.server.shutdown()
        fake.server.server_close()


def decide(base):
    """Decide asking the Gateway with a gateway key (backend/judge.py's first provider)."""
    result = J.gateway(KEY, G.decisions_url(base))({"team": "Acme"}, QUESTIONS)
    assert (result["provider"], result["model"]) == ("Tico AI Gateway", "tico-decide-1.0")
    return "POST", "/v1/gateway/decisions"


def write(base):
    """Writing on a Tico model through the Gateway's chat completions (backend/updates.py's waiting messages)."""
    assert J.gateway_writer(lambda: KEY, base, "tico-cheap-1.0")("system", "user") == {"ok": True}
    return "POST", "/v1/gateway/chat/completions"


def check(base):
    """Settings > AI providers checking the key (backend/gateway_connection.py)."""
    assert G.check(KEY, base)["state"] == "connected"
    return "GET", "/v1/gateway/key"


def enrol(base):
    """Connecting automatically: the install asks for its own key (backend/gateway_connection.py, Dev 210)."""
    assert G.enroll(base, "gi_" + "0" * 24, "gis_" + "s" * 43, "tgi_" + "i" * 32)[0] == 200
    return "POST", "/v1/gateway/enroll"


def turn(base):
    """Rotating the automatic key before it is due (backend/gateway_connection.py, Dev 210)."""
    assert G.rotate(base, KEY)[0] == 200
    return "POST", "/v1/gateway/rotate"


# Every request the app sends the Gateway. A new one is added here, and to HQ_CLIENTS below when it is a new module.
CALLS = {"decisions": decide, "chat completions": write, "key check": check, "enroll": enrol, "rotate": turn}


@pytest.mark.parametrize("call", list(CALLS))
def test_every_gateway_call_carries_ticos_user_agent_past_an_edge_that_refuses_the_default(call, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "v0.3.65")
    with refusing_gateway() as fake:
        method, path = CALLS[call](fake.url)
    assert fake.seen == [(method, path, "Tico-Client/0.3.65")]


def test_without_tico_s_user_agent_the_edge_refuses_the_call_and_decide_falls_through(monkeypatch):
    """The failure this guards: urllib's default agent is refused before the key is read, so the Gateway answers
    nothing and Decide moves on to the next provider."""
    monkeypatch.setattr(G, "user_agent", lambda: "Python-urllib/3.12")
    with refusing_gateway() as fake, pytest.raises(J.JudgeError) as refused:
        decide(fake.url)
    assert refused.value.status == 403 and fake.seen == [("POST", "/v1/gateway/decisions", "Python-urllib/3.12")]


@pytest.mark.parametrize("version, sent", [("0.3.65", "0.3.65"), ("v1.2.3-rc.1", "1.2.3-rc.1"), ("", "dev"),
                                           ("dev", "dev"), ("1.0\r\nX-Injected: 1", "dev"), ("x" * 65, "dev")])
def test_the_user_agent_names_the_app_version_and_never_carries_a_bad_header_value(version, sent, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", version)
    assert G.user_agent() == "Tico-Client/" + sent


# What names Tico HQ or the Gateway in a module, and what a module uses to send HTTP.
HQ_NAMES = re.compile(r"updates\.tico\.team|TICO_HQ_URL|hq_url\(|DEFAULT_HQ|TICO_GATEWAY_URL|/v1/gateway|gateway_url")
SENDS = re.compile(r"urllib\.request|httpx|\brequests\.|aiohttp|http\.client")
# Each module that sends to HQ or the Gateway, and how it names its user agent.
HQ_CLIENTS = {
    "backend/recruit.py": '"User-Agent": "tico"',
    "backend/releases.py": '"User-Agent": "tico-update-check"',
    "backend/support.py": '"User-Agent": "tico-support"',
    "backend/support_access.py": '"User-Agent": "tico-support-access"',
    "clients/gateway.py": "branded_opener(*handlers).open(",
    "clients/judge.py": "G.branded_opener(*handlers).open(",
    "setup/support.py": '"User-Agent": "tico-setup"',
}


def test_every_module_that_sends_to_tico_hq_is_known_and_names_its_user_agent():
    """A new client of Tico HQ or the Gateway fails here until it sends a user agent of its own (a library's default
    is refused at the edge) and is listed above; one that stops naming its agent fails too."""
    found = set()
    for folder in ("backend", "clients", "setup", "connectors", "runner", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if "tests" in path.relative_to(ROOT).parts:
                continue
            text = path.read_text(errors="replace")
            if HQ_NAMES.search(text) and SENDS.search(text):
                found.add(path.relative_to(ROOT).as_posix())
    assert found == set(HQ_CLIENTS), "a module sends to Tico HQ: give it Tico's user agent and list it in HQ_CLIENTS"
    for name, agent in HQ_CLIENTS.items():
        assert agent in (ROOT / name).read_text(), f"{name} no longer sends its user agent"
