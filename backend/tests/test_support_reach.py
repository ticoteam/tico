"""Reach us from anywhere (docs/support.md): Report this attaches what failed to a request's diagnostics, redacted like
the rest, and Help's Setup check runs the installer's checks from the server."""

import json
from types import SimpleNamespace

from backend import setup_check
from backend.tests.helpers.onboarding import as_person, environment, signed_in  # noqa: F401
from backend.tests.helpers.support import file, hq  # noqa: F401  (the fake HQ, autouse)

CAPTURE = "/api/v2/support/diagnostics/capture"


def test_a_reported_error_goes_in_the_attachment_redacted_and_reaches_hq_as_previewed(environment, hq):  # noqa: F811
    api = environment()
    report = {
        "kind": "toast",
        "title": "Could not save: ana@acme.example token sk-abcdEFGH1234567890xyz",
        "detail": "from https://intranet.acme.example/x?id=7 at 10.0.0.4",
        "ref": "run_1",
        "group": "toast:Could not save",
    }
    r = api.post(CAPTURE, headers=signed_in(), json={"report": report})
    assert r.status_code == 200, r.text
    preview = r.json()
    attached = json.loads(preview["text"])["report"]
    assert attached["kind"] == "toast" and attached["group"] == "toast:Could not save" and attached["ref"] == "run_1"
    assert "Could not save" in attached["title"] and "[email]" in attached["title"]
    for secret in ("ana@acme.example", "sk-abcd", "intranet.acme.example", "10.0.0.4", "id=7"):
        assert secret not in preview["text"], secret

    assert file(api, diagnostics=preview["id"]).status_code == 200
    assert json.loads(hq.seen[-1].content)["diagnostics"]["report"] == attached


def test_setup_check_rows_ride_in_the_report_and_unknown_fields_are_refused(environment):  # noqa: F811
    api = environment()
    rows = [
        {
            "name": "HTTPS certificate",
            "status": "bad",
            "detail": "could not connect to acme.example:443",
            "fix": "Open port 443",
            "source": "doctor",
        },
        {"name": "Computers", "status": "warn", "detail": "1 offline", "fix": "", "source": "health"},
    ]
    r = api.post(
        CAPTURE,
        headers=signed_in(),
        json={"report": {"kind": "setup_check", "title": "1 of 2 checks fail", "checks": rows}},
    )
    assert r.status_code == 200, r.text
    checks = json.loads(r.json()["text"])["report"]["checks"]
    assert [c["name"] for c in checks] == ["HTTPS certificate", "Computers"] and checks[0]["fix"] == "Open port 443"
    assert api.post(CAPTURE, headers=signed_in(), json={"report": {"kind": "toast", "secret": "x"}}).status_code == 422
    assert api.post(CAPTURE, headers=signed_in(), json={"report": {"kind": "anything"}}).status_code == 422
    assert (
        api.post(
            CAPTURE, headers=signed_in(), json={"report": {"kind": "setup_check", "checks": rows * 21}}
        ).status_code
        == 422
    )


def test_without_a_report_the_bundle_has_no_report_section(environment):  # noqa: F811
    api = environment()
    assert "report" not in json.loads(api.post(CAPTURE, headers=signed_in(), json={}).json()["text"])


class FakeChecks:
    """setup/verify.py's checks, answering without the network, recording what each was asked."""

    def __init__(self, failing=()):
        self.asked, self.failing = [], set(failing)

    def _answer(self, name, *args):
        from setup.verify import Check

        self.asked.append((name, args))
        bad = name in self.failing
        return Check(name, not bad, f"{name} {'failed' if bad else 'ok'}", f"fix {name}" if bad else "")

    def check_tls(self, domain):
        return self._answer("tls", domain)

    def check_health(self, domain):
        return self._answer("health", domain)

    def check_signin(self, domain, provider, client_id):
        return self._answer("signin", domain, provider, client_id)

    def check_runner_host(self, host):
        return self._answer("runner", host)


def settings(**over):
    base = dict(public_url="https://tico.acme.example", runner_url="", auth_proxy="", oidc_issuer="", oidc_client_id="")
    return SimpleNamespace(**{**base, **over})


def test_setup_check_runs_doctors_https_checks_on_the_servers_own_addresses():
    checks = FakeChecks(failing={"tls"})
    rows = setup_check.doctor_checks(
        settings(
            runner_url="https://run.acme.example",
            auth_proxy="oidc",
            oidc_issuer="https://accounts.google.com",
            oidc_client_id="cid",
        ),
        checks,
    )
    assert sorted(name for name, _ in checks.asked) == ["health", "runner", "signin", "tls"]
    assert ("signin", ("tico.acme.example", "google", "cid")) in checks.asked
    assert ("runner", ("run.acme.example",)) in checks.asked
    bad = [r for r in rows if r["status"] == "bad"]
    assert bad == [{"name": "tls", "status": "bad", "detail": "tls failed", "fix": "fix tls", "source": "doctor"}]

    # Sign-in through another proxy, or a runner on the same host: only what applies is checked.
    checks = FakeChecks()
    setup_check.doctor_checks(settings(runner_url="https://tico.acme.example", auth_proxy="cloudflare"), checks)
    assert sorted(name for name, _ in checks.asked) == ["health", "tls"]


def test_a_tico_on_this_computer_only_has_no_https_to_check():
    checks = FakeChecks()
    for url in ("http://127.0.0.1:8765", "https://localhost", "http://tico.acme.example"):
        (row,) = setup_check.doctor_checks(settings(public_url=url), checks)
        assert row["status"] == "info"
    assert checks.asked == []


def test_health_lines_that_need_a_look_become_rows_with_their_fixes():
    view = {
        "checks": [
            {
                "id": "a",
                "label": "Computers",
                "status": "bad",
                "summary": "No computer online",
                "fixes": [{"label": "Add a computer"}, {"label": "Open Settings"}],
            },
            {"id": "b", "label": "Version", "status": "ok", "summary": "current", "fixes": []},
            {"id": "c", "label": "Backups", "status": "warn", "summary": "Old", "fixes": []},
        ]
    }
    assert setup_check.health_checks(view) == [
        {
            "name": "Computers",
            "status": "bad",
            "detail": "No computer online",
            "fix": "Add a computer; Open Settings",
            "source": "health",
        },
        {"name": "Backups", "status": "warn", "detail": "Old", "fix": "", "source": "health"},
    ]


def test_the_setup_check_route_answers_a_person_and_refuses_a_bot(environment):  # noqa: F811
    api = environment()
    r = api.post("/api/v2/support/setup-check", headers=signed_in())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checks"][0]["name"] == "HTTPS" and body["checks"][0]["status"] == "info"  # the test server is local
    assert "doctor" in body["doctor_only"] and body["failing"] == sum(c["status"] == "bad" for c in body["checks"])
    bot = api.post("/api/v2/support/setup-check", headers={"Authorization": "Bearer nope"})
    assert bot.status_code in (401, 403)


def test_one_person_runs_setup_check_at_most_twice_a_minute(environment, monkeypatch):  # noqa: F811
    api = environment()
    clock = [1000.0]
    monkeypatch.setattr(setup_check, "_last", {})
    monkeypatch.setattr(setup_check.time, "monotonic", lambda: clock[0])
    assert api.post("/api/v2/support/setup-check", headers=signed_in()).status_code == 200
    busy = api.post("/api/v2/support/setup-check", headers=signed_in())
    assert busy.status_code == 429 and "half a minute" in busy.text
    clock[0] += setup_check.EVERY
    assert api.post("/api/v2/support/setup-check", headers=signed_in()).status_code == 200
