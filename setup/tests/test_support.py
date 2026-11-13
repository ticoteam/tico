"""The offer to send a failed setup to Tico support: asked only on a terminal, sent only on "y", redacted with the
diagnostics rules plus the run's domain, names and secrets, and never changing how the run ends."""

import ast
import json
import sys
from pathlib import Path

import pytest

from setup import cli, state, support, verify
from setup.tests import fakes
from setup.tests.fakes import CaptureIO

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = "tico.acme.example"
SECRET = "GOCSPX-supersecret-value"
TICKET = {"ticket_id": "TK-ABCDEFGH", "secret": "s" * 32, "status": "open"}
HQ = "https://hq.acme-support.example"


class AnswerIO(CaptureIO):
    """A terminal that answers `confirm` from a list, as IO.confirm reads a line."""

    def __init__(self, confirms=(), answers=()):
        super().__init__(interactive=True, answers=answers)
        self.confirms, self.asked = list(confirms), []

    def confirm(self, question, default=True):
        self.asked.append(question)
        self.shown = self.text  # what the person had read when asked
        v = (self.confirms.pop(0) if self.confirms else "").strip().lower()
        return default if not v else v.startswith("y")


class Post:
    def __init__(self, *answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, url, body):
        self.bodies.append((url, body))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(support, "_tty", lambda: True)
    monkeypatch.setenv("TICO_HQ_URL", HQ)
    monkeypatch.delenv("TICO_SUPPORT", raising=False)
    monkeypatch.delenv("TICO_SUPPORT_ASKED", raising=False)


FAILING = [
    verify.Check(
        "DNS",
        False,
        f"{DOMAIN} does not resolve to 198.51.100.4 for me@acme.example (Acme Corp)",
        f"token={SECRET}; check https://{DOMAIN}/healthz?key=abc",
    ),
    verify.Check("Server container", True, "Up 3 minutes"),
]


def offer(io, post, **kw):
    return support.offer(
        io,
        "tico setup doctor",
        FAILING,
        domains=support.domains_of(DOMAIN, "me@acme.example"),
        secrets=[SECRET],
        names=["Acme Corp"],
        version="0.3.51",
        post=post,
        **kw,
    )


def test_a_yes_sends_a_redacted_setup_ticket_and_prints_where_replies_show(tty):
    io, post = AnswerIO(["y"], ["ana@acme.example"]), Post((201, TICKET))
    assert offer(io, post) is True
    assert io.asked == ["Send this to Tico support?"]
    url, body = post.bodies[0]
    assert url == HQ + "/v1/support" and body["kind"] == "setup" and body["version"] == "0.3.51"
    assert body["email"] == "ana@acme.example"
    message = body["message"]
    assert message.startswith("Setup failed: tico setup doctor\n[FAIL] DNS: ") and "Tico: 0.3.51" in message
    for leaked in (SECRET, "198.51.100.4", "acme.example", "Acme Corp", "ana@", "me@", "key=abc", "Up 3 minutes"):
        assert leaked not in message
    assert "[company-domain]" in message and "[ip]" in message
    assert f"Sent as TK-ABCDEFGH. Replies show up at {HQ}/support/TK-ABCDEFGH#s={'s' * 32}" in io.text


def test_the_person_reads_exactly_what_would_be_sent_before_the_question(tty):
    io, post = AnswerIO(["y"]), Post((201, TICKET))
    assert offer(io, post) is True
    message = post.bodies[0][1]["message"]
    assert all(line in io.shown for line in message.splitlines())


def test_home_folders_and_ssh_logins_lose_the_account_name():
    text = support.scrub(
        "cd /home/ana/tico; open /Users/bob/x; C:\\Users\\cara\\x; ssh ubuntu@203.0.113.7; root@[2001:db8::1]"
    )
    for leaked in ("ana", "bob", "cara", "ubuntu", "root", "203.0.113.7"):
        assert leaked not in text, leaked
    assert "/home/[user]/tico" in text and "[user]@[ip]" in text
    assert support.scrub("write to me@acme.example") == "write to [email]"
    assert support.scrub("write to me@12.34.example.com") == "write to [email]"


@pytest.mark.parametrize("answer", ["", "n", "no"])
def test_anything_but_yes_sends_nothing(tty, answer):
    io, post = AnswerIO([answer]), Post()
    assert offer(io, post) is False and post.bodies == []


def test_no_terminal_or_support_off_never_asks(tty, monkeypatch):
    post = Post()
    monkeypatch.setattr(support, "_tty", lambda: False)
    io = AnswerIO(["y"])
    assert offer(io, post) is False and io.asked == []
    monkeypatch.setattr(support, "_tty", lambda: True)
    monkeypatch.setenv("TICO_SUPPORT", "off")
    assert offer(io, post) is False and io.asked == []
    monkeypatch.delenv("TICO_SUPPORT")
    io = CaptureIO(interactive=False)
    assert offer(io, post) is False and post.bodies == []


def test_an_older_hq_gets_the_kind_in_the_text(tty):
    io, post = AnswerIO(["y"]), Post((422, {"error": "invalid", "field": "fields"}), (201, TICKET))
    assert offer(io, post) is True
    first, second = post.bodies[0][1], post.bodies[1][1]
    assert first["kind"] == "setup" and "kind" not in second and "email" not in second
    assert second["message"] == "Kind: setup\n" + first["message"]


def test_a_failure_to_send_is_one_line_and_the_doctor_still_fails(tty, monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_SUPPORT_ASKED", str(tmp_path / "asked"))
    state.save(
        DOMAIN,
        {
            "target": "local",
            "domain": DOMAIN,
            "owner_email": "me@acme.example",
            "company": "Acme Corp",
            "front_door": "caddy",
            "auth": "google",
        },
        f"TICO_OIDC_CLIENT_SECRET={SECRET}\n",
    )
    monkeypatch.setattr(verify, "run_all", lambda **kw: FAILING)
    post = Post(OSError("connection refused"))
    monkeypatch.setattr(support, "_post", post)
    io = AnswerIO(["y"], [""])
    assert cli.main(["doctor", "--domain", DOMAIN], deps=fakes.deps(), io=io) == 1
    assert io.lines[-1] == f"Not sent: could not reach {HQ} (OSError)."
    assert SECRET not in post.bodies[0][1]["message"] and "acme.example" not in post.bodies[0][1]["message"]
    assert (tmp_path / "asked").exists()  # the installer does not ask a second time
    io = AnswerIO(["y"])
    assert cli.main(["doctor", "--domain", DOMAIN, "--no-support"], deps=fakes.deps(), io=io) == 1 and io.asked == []


def test_the_installer_command_sends_its_error_and_returns_zero(tty, monkeypatch):
    post = Post((201, TICKET))
    monkeypatch.setattr(support, "_post", post)
    io = AnswerIO(["y"], [""])
    assert (
        cli.main(
            ["support", "--step", "6", "--error", "docker compose failed in /opt/tico on 10.1.2.3"],
            deps=fakes.deps(),
            io=io,
        )
        == 0
    )
    message = post.bodies[0][1]["message"]
    assert message.startswith(
        "Setup failed: install.sh (exit 6: the wizard or the health check failed)\nerror: docker compose"
    )
    assert "10.1.2.3" not in message
