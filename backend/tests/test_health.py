"""Health: every check is computed from live state, and people who are not administrators see counts only."""

import pytest

from backend import onboarding, releases
from backend.store import H
from backend.tests.test_getting_started import SIGNED_IN, add_bot, enrolled, heartbeat  # noqa: F401
from backend.tests.test_onboarding import as_person, environment, signed_in  # noqa: F401


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(releases, "CHECKER", releases.Checker())
    monkeypatch.setenv("TICO_UPDATE_CHECK", "off")


def health_of(api, headers=None):
    body = api.get("/api/v2/health", headers=headers or signed_in()).json()
    return body, {row["id"]: row for row in body["checks"]}


def with_backup(monkeypatch, backup):
    real = onboarding.config_view
    monkeypatch.setattr(onboarding, "config_view", lambda *a, **k: {**real(*a, **k), **({"backup": backup} if backup else {})})


def test_all_good(environment, monkeypatch):
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner, seconds_ago=5, runtimes=SIGNED_IN)
    with_backup(monkeypatch, {"mode": "remote", "last_replicated_at": H.now(), "target_kind": "s3"})
    body, checks = health_of(api)
    assert {k: v["status"] for k, v in checks.items() if k != "signin"} == {
        "version": "ok", "computers": "ok", "models": "ok", "waiting": "ok", "queue": "ok",
        "github": "info", "backups": "ok", "failed": "ok"}
    assert body["computers"][0]["online"] and body["computers"][0]["runtimes"][0]["ready"]


def test_stalled_health_does_not_disclose_another_persons_private_task(environment):
    api = environment()
    add_bot(api, 'helper')
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:riley', 'Private repair request', '', 'bot:helper', private=True)
        H.event(c, H.KEEPER, 'task.stall_escalated', task['id'], {'wakes': 3})
    assert 'stalled_tasks' not in health_of(api)[1]


def test_an_offline_computer_holds_its_bots(environment):
    api = environment()
    runner = enrolled(api)
    add_bot(api, "helper")
    heartbeat(api, runner, seconds_ago=600, runtimes=SIGNED_IN)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('helper',?,1,?,'t')",
                  (runner, H.now()))
    body, checks = health_of(api)
    assert checks["computers"]["status"] == "bad" and checks["waiting"]["status"] == "bad"
    assert body["waiting"][0]["bot"] == "helper" and body["waiting"][0]["reason"] == "computer_offline"
    assert body["computers"][0]["last_seen"]
    # A second computer that is up makes it a warning, not an outage.
    heartbeat(api, runner, seconds_ago=5, runtimes=SIGNED_IN)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO runners(id,label,operator,token_hash,created,last_seen) "
                  "SELECT 'r2','Old laptop',operator,'h2',created,? FROM runners WHERE id=?",
                  (H.shift(H.now(), hours=-2), runner))
    body, checks = health_of(api)
    assert checks["computers"]["status"] == "warn" and "Old laptop" in checks["computers"]["summary"]


def test_backups_local_only_and_stale(environment, monkeypatch):
    api = environment()
    old = H.shift(H.now(), hours=-30)
    with_backup(monkeypatch, {"mode": "remote", "last_replicated_at": old, "target_kind": "s3"})
    assert health_of(api)[1]["backups"]["status"] == "warn"
    with_backup(monkeypatch, {"mode": "off"})
    assert health_of(api)[1]["backups"]["status"] == "bad"


def test_others_see_counts_not_details(environment):
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner, seconds_ago=600)
    body, checks = health_of(api, as_person(api, "quinn"))
    assert body["audience"] == "human" and body["computers"] == [] and body["waiting"] == []
    assert set(checks) == {"computers", "waiting", "queue", "failed"}
    assert all(not row["fixes"] for row in checks.values())
    assert "helper" not in checks["waiting"]["summary"]


def test_a_local_credential_key_that_is_not_backed_up_is_a_warning_or_a_note():
    from types import SimpleNamespace

    from backend import health
    remote = {"mode": "remote", "last_replicated_at": H.now(), "target_kind": "s3"}
    server, local = SimpleNamespace(loopback=False), SimpleNamespace(loopback=True)
    check = lambda backup, where=server: health._backups({"backup": backup}, where)
    # No local key (a KMS install, or no credential saved yet): as before.
    assert check({**remote, "credential_key": {"present": False}})["status"] == "ok"
    assert check(remote)["status"] == "ok"
    # Backups are set up and the key is not in them: a warning that says why.
    missing = check({**remote, "credential_key": {"present": True, "copied_at": None, "current": False}})
    assert missing["status"] == "warn" and "credential key" in missing["summary"] and missing["fixes"]
    assert check({**remote, "credential_key": {"present": True, "copied_at": "2026-01-01T00:00:00Z", "current": True}})["status"] == "ok"
    # No off-disk backup: the key is named in the note, and a quick start stays a note once it is copied.
    only = {"mode": "local-only", "last_replicated_at": None, "target_kind": "local"}
    held = {"present": True, "copied_at": "2026-01-01T00:00:00Z", "current": True}
    assert "credential key" in check({**only, "credential_key": held})["summary"]
    quiet = check({**only, "credential_key": held}, local)
    assert quiet["status"] == "info" and "credential key" in quiet["summary"] and quiet["fixes"][0]["href"].endswith("#backups-and-restore")
    assert check({**only, "credential_key": {**held, "copied_at": None, "current": False}}, local)["status"] == "warn"
    assert "credential key" in check({"mode": "off", "credential_key": held})["summary"]


def test_listening_health_checks_custom_categories_without_question_text(environment, monkeypatch):
    import json
    from backend import listening
    monkeypatch.setattr(listening, "_warning_revisions", listening.OrderedDict())
    api = environment()
    settings = api.app.state.store.settings
    (settings.registry_dir / "listening.yaml").write_text("""
destinations:
  leads: {category: custom, threshold: 0.75, receiver: bot:ana, unless: {category: veto, threshold: 0.70}}
""")
    directory = settings.registry_dir / "questions"
    directory.mkdir()
    path = directory / "listening-item.json"
    secret = "private-question-fixture-value"
    qset = {"id": "listening-item", "version": 9, "summary": secret, "questions": {
        "custom": {"type": "choice", "instructions": secret, "criteria": {"yes": secret, "no": None}}}}
    path.write_text(json.dumps(qset))
    listening.destinations(settings)
    body, checks = health_of(api)
    assert checks["listening"]["status"] == "warn"
    assert secret not in json.dumps(body)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('sam','Sam','sam@example.com')")
    assert "listening" not in health_of(api, as_person(api, "sam"))[1]


def test_a_bot_its_computer_cannot_start_is_stuck_with_the_reason_and_one_fix(environment):
    """Queued work behind "Claude login required" is named at once (not after 15 minutes), with a sign-in fix
    for administrators; someone without Read access on the bot hears nothing about it."""
    import json
    api = environment()
    runner = enrolled(api)
    add_bot(api, "release")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET label='Team box', last_seen=?, readiness_json=? WHERE id=?",
                  (H.now(), json.dumps({"schema_version": 1, "runtimes": {
                      "claude": {"installed": True, "authenticated": "missing", "detail": "Claude login required"}},
                      "bots": {"release": {"ready": False, "runtime": "claude", "problems": ["Claude login required"]}}}), runner))
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('release',?,1,?,'t')",
                  (runner, H.now()))
        c.execute("UPDATE bots SET runtime='claude' WHERE slug='release'")
        owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
        conversation = H.open_conversation(c, owner, [owner, "bot:release"], kind="chat")
        H._write_message(c, owner, "bot:release", "ship it", conversation, "say", {}, None, None)
    body, checks = health_of(api)
    assert checks["queue"]["status"] == "bad" and checks["queue"]["summary"] == "1 bot with work that is not starting."
    [row] = body["stuck"]
    assert row["bot"] == "release" and row["why"] == "Claude login required on Team box"
    assert row["fix"]["login"] == {"runner_id": runner, "runtime": "claude", "computer": "Team box"}
    # The model picker gets the same answer from the same rule (backend/readiness.py can_run).
    picker = api.get("/api/v2/operations", headers=signed_in()).json()["runnable"]["release"]["claude"]
    assert picker["can_run"] is False and picker["short"] == "sign in" and picker["sign_in"]["runner_id"] == runner
    assert health_of(api, as_person(api, "quinn"))[0]["stuck"][0]["fix"] is None   # readable: the reason, no fix
    from backend import bot_access as A
    only_ana = A.audience({"people": ["ana"]})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_config(bot,config_json,operator,access_json) VALUES('release','{}','ana',?)",
                  (A.stored({"see": only_ana, "read": only_ana, "write": only_ana}),))
    body, checks = health_of(api, as_person(api, "quinn"))
    assert body["stuck"] == [] and "release" not in json.dumps(body)


def test_computer_sign_in_names_only_a_model_its_bots_run_on(environment):
    """The company default is Codex but the bots here run on Claude: no Codex warning, still a Sign in button."""
    import json
    api = environment()
    revision = api.get("/api/v2/providers", headers=signed_in()).json()["revision"]
    assert api.put("/api/v2/providers", headers=signed_in(), json={
        "enabled": ["openai", "anthropic"], "runtime": "codex", "expected_revision": revision}).status_code == 200
    runner = enrolled(api)
    add_bot(api, "writer")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('writer',?,'ana')", (json.dumps({"runtime": "claude"}),))
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('writer',?,1,?,'t')",
                  (runner, H.now()))
    heartbeat(api, runner, runtimes={"codex": {"installed": True, "authenticated": "missing"},
                                     "claude": {"installed": True, "authenticated": "ready"}})
    body, checks = health_of(api)
    assert "computer_signin" not in checks
    assert next(r for r in body["computers"][0]["runtimes"] if r["name"] == "codex")["signable"]
    add_bot(api, "coder")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('coder',?,'ana')", (json.dumps({"runtime": "codex"}),))
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('coder',?,1,?,'t')",
                  (runner, H.now()))
    assert "sign in to Codex" in health_of(api)[1]["computer_signin"]["summary"]


def test_a_bot_over_the_daily_token_threshold_is_flagged(environment):
    """Uncached input only: cache reads are cheap and would dominate the count."""
    api = environment()
    add_bot(api, "loop")
    api.app.state.store.settings.token_alert_input = 1000
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started,input_tokens,cached_tokens) VALUES('t1','loop',?,1100,90000)", (H.now(),))
        c.execute("INSERT INTO turns(id,bot,started,input_tokens,cached_tokens) VALUES('t0','loop',?,9000,0)",
                  (H.shift(H.now(), hours=-30),))
    checks = health_of(api)[1]
    assert checks["tokens"]["status"] == "warn" and "1,100 uncached input tokens in 24 h" in checks["tokens"]["summary"]
