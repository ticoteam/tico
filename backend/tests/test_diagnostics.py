"""Support diagnostics (PRIVACY.md, "Support diagnostics"): the redactor, the allowlist, and that a preview is what is sent."""

import json
import logging

import pytest

from backend import diagnostics as D
from backend.tests.helpers.onboarding import as_person, environment, signed_in  # noqa: F401
from backend.tests.helpers.support import hq, file  # noqa: F401  (the fake HQ, autouse)


@pytest.fixture(autouse=True)
def own_server_log():
    """The log ring is one per process. A server's holds only its own hub's lines; a test worker's held every earlier
    test's warnings, other companies' bot names included (a Slack test's "routes to coo"), so which tests ran before on
    the worker decided a bundle's content."""
    D.RING.lines.clear()
    D.RING.previous, D.RING.count = None, 0


def redactor():
    return D.Redactor(
        ["acme.example"],
        [("assistant", ["Morgan the assistant"]), ("botops", ["BotOps"])],
        [("morgan", ["Morgan Reed", "morgan@acme.example"]), ("riley", ["Riley Quinn", "riley@acme.example"])],
    )


# ------------------------------------------------------------------ the redactor
@pytest.mark.parametrize(
    "raw, gone, shown",
    [
        ("key sk-abcdEFGH1234567890xyz here", "sk-abcdEFGH1234567890xyz", "[key]"),
        ("Authorization: Bearer abc123.def-456_ghi", "abc123.def-456_ghi", "[token]"),
        ("mail stranger@elsewhere.org sent", "stranger@elsewhere.org", "[email]"),
        ("from 10.0.0.12 and 192.168.1.1", "10.0.0.12", "[ip]"),
        ("db.acme.example refused", "db.acme.example", "[company-domain]"),
    ],
)
def test_the_redactor_removes_each_kind_of_secret(raw, gone, shown):
    out = redactor().text(raw)
    assert gone not in out and shown in out


@pytest.mark.parametrize("raw", ["Tico 0.2.18 on Python 3.12.4"])
def test_the_redactor_leaves_what_is_not_a_secret(raw):
    assert redactor().text(raw) == raw


def test_names_become_labels_that_are_the_same_all_through_a_bundle():
    r = redactor()
    out = r.text(
        "Morgan Reed (morgan@acme.example, morgan) asked bot:assistant; BotOps and botops; Riley Quinn later; morgan again"
    )
    assert out == "person-1 (person-1, person-1) asked bot-1; bot-2 and bot-2; person-2 later; person-1 again"
    assert r.label("BOTOPS") == "bot-2" and r.label("Zed") is None
    # Whole words only, and every string, however nested, goes through it.
    assert r.text("coordinator") == "coordinator"
    assert r.clean({"a": ["riley failed", {"b": "Morgan Reed"}], "n": 3, "ok": True, "x": None}) == {
        "a": ["person-2 failed", {"b": "person-1"}],
        "n": 3,
        "ok": True,
        "x": None,
    }
    # Another bundle with the same people in a different order gets its own labels: they mean nothing outside one bundle.
    other = D.Redactor([], [], [("riley", []), ("morgan", [])])
    assert other.text("riley") == "person-2" and other.text("morgan") == "person-1"


def test_a_bundle_over_the_cap_shrinks_its_logs_first():
    bundle = {
        "format": 1,
        "counts": {"bots": 1},
        "containers": [],
        "logs": {"server": ["x" * 300] * 2000, "updater": [], "app": ["z" * 300] * 3000},
        "runners": [{"log": ["y" * 300] * 2000, "problems": []}],
    }
    out = D.fit(bundle)
    assert len(json.dumps(out).encode()) <= D.MAX_BYTES and out["counts"] == {"bots": 1}
    assert len(out["logs"]["app"]) < 3000  # the desktop app's log shrinks like the others


# ------------------------------------------------------------------ the allowlist
def walk(value, shape, where="$"):
    """Every key of `value` must be in `shape`, at every depth."""
    if isinstance(shape, list):
        for item in value:
            walk(item, shape[0], where + "[]")
    elif isinstance(shape, dict) and shape:
        assert isinstance(value, dict), where
        for key, inner in value.items():
            assert key in shape, f"{where}.{key} is not allowlisted"
            walk(inner, shape[key], where + "." + key)
    elif isinstance(shape, dict):
        assert set(value) <= set(D.FEATURES) and all(isinstance(v, bool) for v in value.values())


def seed(api):
    """A company with content in it: a task, a message, a doc, and a log line that carries an address and a key."""
    owner = signed_in()
    api.post(
        "/api/v2/tasks",
        json={"title": "CONTENT-task-title", "body": "CONTENT-task-body", "owner": "assistant"},
        headers=owner,
    )
    api.post("/api/v2/chat/assistant", json={"body": "CONTENT-chat-message"}, headers=owner)
    api.post("/api/v2/docs", json={"title": "CONTENT-doc-title", "body": "CONTENT-doc-body"}, headers=owner)
    logging.getLogger("tico.test").warning(
        "Sync for riley@acme.example failed from 203.0.113.9 with sk-abcdEFGH1234567890xyz"
    )
    logging.getLogger("tico.test").info("not kept: below WARNING")


def bundle_of(api, headers=None):
    got = api.get("/api/v2/support/diagnostics", headers=headers or signed_in())
    assert got.status_code == 200, got.text
    return got.json(), json.loads(got.json()["text"])


def test_the_bundle_holds_only_allowlisted_facts_and_never_content(environment, hq):
    api = environment()
    seed(api)
    answer, bundle = bundle_of(api)
    walk(bundle, D.ALLOWED)
    text = answer["text"]
    for content in (
        "CONTENT-task-title",
        "CONTENT-task-body",
        "CONTENT-chat-message",
        "CONTENT-doc-title",
        "CONTENT-doc-body",
    ):
        assert content not in text
    # Every string was redacted: the log line kept its shape, not its address, key or names.
    lines = [line for line in bundle["logs"]["server"] if "Sync for" in line]
    assert len(lines) == 1 and "person-" in lines[0] and "[ip]" in lines[0] and "[key]" in lines[0]
    assert "riley@acme.example" not in text and "203.0.113.9" not in text and "sk-abcd" not in text
    assert "not kept: below WARNING" not in text
    # The company, its people and its bots by name: none of it appears.
    for name in (
        "Morgan Reed",
        "Riley Quinn",
        "acme.example",
        "AcmeCorp",
        "Acme",
        "Atlas",
        "morgan",
        "riley",
        "assistant",
        "botops",
    ):
        # The features block names the Assistant feature (`"assistant": false`), which is not the bot's slug.
        assert name not in text.replace("[company-domain]", "").replace('"assistant":', ""), name
    assert bundle["versions"]["tico"] and bundle["counts"]["people"] == 3 and bundle["counts"]["bots"] >= 2
    assert bundle["database"]["migration"] >= 11 and set(bundle["features"]) <= set(D.FEATURES)
    assert all(set(x) == {"name", "status"} for x in bundle["health"]) and bundle["health"]
    assert D.digest(bundle) == answer["id"] and answer["bytes"] == len(text.encode()) <= D.MAX_BYTES


# ------------------------------------------------------------------ the preview is what is sent
def test_the_bundle_lists_failed_setup_steps_with_labels_and_no_secret(environment, hq):
    api = environment()
    secret = "ghs_PLANTEDsecret0123456789abcdef"
    refused = api.get("/api/v2/github/app/callback", params={"code": secret, "state": secret}, headers=signed_in())
    assert refused.status_code == 400
    answer, bundle = bundle_of(api)
    walk(bundle, D.ALLOWED)
    (item,) = bundle["failures"]
    assert item["event"] == "setup.failed" and item["category"] == "github_state" and item["status"] == 400
    assert item["step"] == "GET /api/v2/github/app/callback" and item["ref"] == refused.json()["error"]["ref"]
    assert item["who"].startswith("person-") and secret not in answer["text"]


def test_what_the_preview_showed_is_exactly_what_hq_gets(environment, hq):
    api = environment()
    seed(api)
    answer, shown = bundle_of(api)
    r = file(api, diagnostics=answer["id"])
    assert r.status_code == 200 and r.json()["sent"]["diagnostics"] == answer["bytes"]
    (request,) = hq.seen
    sent = json.loads(request.content)
    assert sent["diagnostics"] == shown  # the same bundle, key for key
    assert D.canonical(sent["diagnostics"]) == answer["text"]  # and the same text
    # A second send needs a bundle of its own: the digest is looked up per person and lives a few minutes.
    assert file(api, diagnostics=answer["id"]).status_code == 200
    riley = as_person(api, "riley")
    assert file(api, headers=riley, diagnostics=answer["id"]).status_code == 409
    assert file(api, diagnostics="0" * 64).status_code == 409
    assert len(hq.tickets) == 2  # the two refusals reached nobody


def test_edits_are_validated_redacted_and_bound_to_the_person(environment, hq):
    api = environment()
    answer, original = bundle_of(api)
    edited = {"format": 1, "logs": {"server": ["contact stranger@elsewhere.org sk-abcdEFGH1234567890xyz"]}}
    # A sender can shorten a section, but cannot create extra log entries in an empty capture.
    logging.getLogger("tico.test").warning("A failure occurred")
    answer, original = bundle_of(api)
    body = {"id": answer["id"], "text": json.dumps(edited)}
    r = api.post("/api/v2/support/diagnostics", headers=signed_in(), json=body)
    assert r.status_code == 200, r.text
    final = r.json()
    assert "stranger@" not in final["text"] and "sk-abcd" not in final["text"]
    assert "[email]" in final["text"] and json.loads(final["text"])["capture"]["edited"]
    assert file(api, diagnostics=final["id"]).status_code == 200
    assert D.canonical(json.loads(hq.seen[-1].content)["diagnostics"]) == final["text"]
    assert api.post("/api/v2/support/diagnostics", headers=as_person(api, "riley"), json=body).status_code == 409
    for text in ('{', '{"format":1,"secret":"new field"}', '{"format":"1"}'):
        assert (
            api.post("/api/v2/support/diagnostics", headers=signed_in(), json={**body, "text": text}).status_code == 422
        )


def test_failure_ring_is_bounded_and_groups_repeats_without_exception_values():
    ring = D.LogRing(size=2)
    for _ in range(100):
        ring.handle(
            logging.LogRecord("tico.request", logging.ERROR, "", 0, "GET /api/v2/tasks/{id} HTTP 500", (), None)
        )
    lines, capture = ring.snapshot()
    assert len(lines) == 1 and "x100" in lines[0] and capture["server_repeats"] == 99
    for name in ("second", "third"):
        ring.handle(logging.LogRecord("tico.request", logging.ERROR, "", 0, name, (), None))
    assert len(ring.snapshot()[0]) == 2 and ring.snapshot()[1]["server_evicted"] == 1


def test_browser_capture_never_accepts_messages_or_private_filenames(environment):
    api = environment()
    event = {
        "kind": "error",
        "at": "2026-10-02T12:00:00Z",
        "file": "/private/company/secret.js",
        "line": 42,
        "count": 2,
    }
    url = "/api/v2/support/diagnostics/capture"
    r = api.post(url, headers=signed_in(), json={"browser": [event]})
    assert r.status_code == 200, r.text
    assert json.loads(r.json()["text"])["browser"][0]["file"] == ""
    assert (
        api.post(url, headers=signed_in(), json={"browser": [{**event, "message": "private content"}]}).status_code
        == 422
    )
    assert api.post(url, headers=signed_in(), json={"browser": [event] * 21}).status_code == 422


def test_browser_capture_names_the_service_worker_script(environment):
    # ui/sw.ts is served at the root as /sw.js: an error located in the worker carries that name, not a blank.
    api = environment()
    event = {"kind": "error", "at": "2026-10-02T12:00:00Z", "file": "sw.js", "line": 7, "count": 1}
    r = api.post("/api/v2/support/diagnostics/capture", headers=signed_in(), json={"browser": [event]})
    assert r.status_code == 200, r.text
    assert json.loads(r.json()["text"])["browser"][0]["file"] == "sw.js"


def test_the_desktop_apps_log_is_labelled_and_redacted(environment):
    api = environment()
    url = "/api/v2/support/diagnostics/capture"
    line = "2026-10-16T06:47:31Z WARN tico_app: update check failed: 401 from https://acme.example/feed sk-abcdEFGH1234567890xyz"
    r = api.post(url, headers=signed_in(), json={"app": {"version": "0.3.47", "lines": [line]}})
    assert r.status_code == 200, r.text
    bundle = json.loads(r.json()["text"])
    assert bundle["versions"]["app"] == "0.3.47" and bundle["versions"]["server"] != "0.3.47"
    (logged,) = bundle["logs"]["app"]
    assert "update check failed" in logged and "sk-abcd" not in logged
    assert "app" not in json.loads(api.post(url, headers=signed_in(), json={}).json()["text"])["logs"]
    assert api.post(url, headers=signed_in(), json={"app": {"lines": ["x"] * (D.APP_LINES + 1)}}).status_code == 422


def test_request_failures_keep_only_route_templates_and_safe_exception_locations(monkeypatch, caplog):
    from types import SimpleNamespace

    ring = D.LogRing()
    monkeypatch.setattr(D, "RING", ring)
    request = SimpleNamespace(method="POST", scope={"route": SimpleNamespace(path="/api/v2/tasks/{task_id}")})
    try:
        raise ValueError("private customer body and secret")
    except ValueError as exc:
        D.request_failure(request, 500, exc)
    line = ring.snapshot()[0][0]
    assert "POST /api/v2/tasks/{task_id} HTTP 500" in line and "ValueError" in line
    assert "private customer" not in line and "secret" not in line
    assert not caplog.records  # no extra process log line for every failing request


@pytest.mark.parametrize(
    "raw",
    [
        "{'api_key' : 'Fixture spaced value', 'secret': 'z'}",
        r'''{"password": "Fixture\"escaped tail", "token": "a\\b"}''',
    ],
)
def test_secret_fields_consume_short_spaced_escaped_and_truncated_values(raw):
    clean = D.Redactor().text(raw)
    assert "[redacted]" in clean
    for value in ("Fixture", "xy", "escaped", "tail", "spaced", "value", "a\\\\b"):
        assert value not in clean


@pytest.mark.parametrize("authority", ["private.acme-customer.app", "user:FixturePass7@acme.app:8443"])
def test_url_authorities_do_not_depend_on_the_prose_hostname_heuristic(authority):
    assert D.Redactor().text("https://" + authority + "/api") == "https://[host]/api"
