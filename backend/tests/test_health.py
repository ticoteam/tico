"""Health: every check is computed from live state, and people who are not administrators see counts only."""

import json

import pytest

from backend import releases, replication
from backend.auth import Identity
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
    if backup:
        monkeypatch.setattr(replication, "status", lambda: backup)


def test_all_good(environment, monkeypatch):
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner, seconds_ago=5, runtimes=SIGNED_IN)
    with_backup(monkeypatch, {"mode": "remote", "last_replicated_at": H.now(), "target_kind": "s3"})
    body, checks = health_of(api)
    assert {k: v["status"] for k, v in checks.items() if k != "signin"} == {
        "version": "ok", "computers": "ok", "models": "ok", "waiting": "ok", "queue": "ok",
        "github": "info", "backups": "ok", "failed": "ok", "stalled_runs": "ok", "turns:" + body["computers"][0]["id"]: "ok"}
    assert body["computers"][0]["online"] and body["computers"][0]["runtimes"][0]["ready"]


def test_stalled_health_does_not_disclose_another_persons_private_task(environment):
    api = environment()
    add_bot(api, 'helper')
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, 'human:riley', 'Private repair request', '', 'bot:helper', private=True)
        H.event(c, H.KEEPER, 'task.stall_escalated', task['id'], {'wakes': 3})
    assert 'stalled_tasks' not in health_of(api)[1]


@pytest.mark.parametrize('hidden', [False, 'private', 'activity'])
def test_an_escalated_task_waiting_on_its_own_quiet_hold_is_not_progressing(environment, hidden):
    api = environment()
    add_bot(api, 'helper')
    add_bot(api, 'holder')
    with api.app.state.store.transaction() as c:
        window = H.task_create(c, 'bot:helper', 'Open the deploy window', '', 'bot:helper')
        hold = H.task_create(c, 'bot:helper', 'Hold merges until RELEASED', '', 'bot:holder', parent_id=window['id'])
        H.TR.relate(c, 'bot:helper', window['id'], hold['id'], 'blocked_by')
        H.task_update(c, 'bot:helper', window['id'], status='waiting', note="Waiting on the hold's ack.")
        c.execute("UPDATE jobs SET state='completed'")
        c.execute("UPDATE tasks SET private=0, updated=? WHERE id IN (?,?)",
                  (H.shift(H.now(), seconds=-3600), window['id'], hold['id']))
        H.event(c, H.KEEPER, 'task.stall_escalated', window['id'], {'wakes': 3})
        if hidden == 'private':
            c.execute("UPDATE tasks SET private=1 WHERE id=?", (hold['id'],))
        elif hidden == 'activity':
            from backend import bot_access as A
            only_ana = A.audience({'people': ['ana']})
            c.execute("INSERT INTO bot_config(bot,config_json,operator,access_json) VALUES('holder','{}','ana',?) "
                      "ON CONFLICT(bot) DO UPDATE SET access_json=excluded.access_json",
                      (A.stored({'see': A.OPEN_LEVEL, 'read': only_ana, 'write': only_ana}),))
    for status in ('open', 'review'):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET status=? WHERE id=?", (status, hold['id']))
        check = health_of(api, as_person(api, 'quinn') if hidden == 'activity' else None)[1].get('stalled_tasks')
        if hidden or status == 'review':
            assert check is None
        else:
            assert check['status'] == 'warn' and [item['task'] for item in check['items']] == [window['id']]


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


def silent_computer(api, seconds_ago):
    """A computer enrolled for riley, holding one queued request for its bot, last heard from `seconds_ago`."""
    runner = enrolled(api)
    add_bot(api, "helper")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET operator='riley' WHERE id=?", (runner,))
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('helper',?,1,?,'t')",
                  (runner, H.now()))
        # A chat message in a conversation, as the product writes them: the trigger queues its job, and Pipeline counts it.
        c.execute("INSERT INTO conversations(id,kind,participants_json,created) VALUES('cv1','chat','[]',?)", (H.now(),))
        c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,created) "
                  "VALUES('m1','cv1','human:ana','bot:helper','chat','hi',?)", (H.now(),))       # queues its job
    heartbeat(api, runner, seconds_ago=seconds_ago, runtimes=SIGNED_IN)
    return runner


def machine_issue(api, headers):
    return next(i for i in api.get("/api/status", headers=headers).json()["health_issues"] if i["kind"] == "machine")


def test_a_computer_silent_with_work_queued_goes_to_its_owner(environment):
    from backend.tests.test_people_access import person_headers
    api = environment()
    runner = silent_computer(api, 300)
    riley, quinn, owner = person_headers(api, "riley"), as_person(api, "quinn"), signed_in()
    # A few minutes offline is a blip that clears itself.
    assert machine_issue(api, riley)["title"] == "Owner Mac is offline" and not machine_issue(api, riley)["needs_person"]
    # Past ten minutes with work queued it is an outage: said on Health and Overview, nobody interrupted yet.
    heartbeat(api, runner, seconds_ago=700, runtimes=SIGNED_IN)
    issue = machine_issue(api, riley)
    assert issue["title"] == "Owner Mac stopped checking in" and issue["severity"] == "error" and not issue["needs_person"]
    body, checks = health_of(api)
    assert checks["waiting"]["status"] == "bad" and "Owner Mac has not checked in for 11 minutes" in checks["waiting"]["summary"]
    assert body["stuck"][0]["why"].startswith("Owner Mac has not checked in for 11 minutes")
    # Past thirty, the person who enrolled it has it in Needs you; nobody else does while they are here.
    heartbeat(api, runner, seconds_ago=1900, runtimes=SIGNED_IN)
    assert machine_issue(api, riley)["needs_person"] and machine_issue(api, riley)["action"]
    assert not machine_issue(api, quinn)["needs_person"] and not machine_issue(api, owner)["needs_person"]
    # Once they have left, the company owner is the one asked.
    assert api.post("/api/v2/people/riley", json={"left": True}, headers=owner).status_code == 200
    assert machine_issue(api, owner)["needs_person"]


def test_a_computer_stops_on_all_its_queued_work_not_what_the_viewer_may_read(environment, monkeypatch):
    from backend import views
    from backend.tests.test_people_access import person_headers
    api = environment()
    silent_computer(api, 1900)
    monkeypatch.setattr(views.pipeline_stats, "job_queues",                  # someone else's private request
                        lambda c, who, bots, now=None: {b: {"queued": 0, "uncertain": 0} for b in bots})
    issue = machine_issue(api, person_headers(api, "riley"))
    assert issue["title"] == "Owner Mac stopped checking in" and issue["needs_person"]


def test_backups_are_judged_by_how_long_they_have_been_behind_not_by_the_copy_age(environment, monkeypatch):
    api = environment()
    old = H.shift(H.now(), hours=-30)
    # A quiet database: its newest copy is a day old, and nothing is behind it.
    with_backup(monkeypatch, {"mode": "remote", "last_replicated_at": old, "behind_since": None, "target_kind": "s3"})
    assert health_of(api)[1]["backups"]["status"] == "ok"
    with_backup(monkeypatch, {"mode": "remote", "last_replicated_at": old, "behind_since": old, "target_kind": "s3"})
    assert health_of(api)[1]["backups"]["status"] == "bad"
    with_backup(monkeypatch, {"mode": "off"})
    assert health_of(api)[1]["backups"]["status"] == "bad"


def test_lag_is_judged_by_how_long_it_has_lasted_against_the_sync_interval(monkeypatch):
    from types import SimpleNamespace

    from backend import health
    remote = SimpleNamespace(loopback=False)
    status_at = lambda minutes: health._backups(
        {"backup": {"mode": "remote", "target_kind": "s3", "last_replicated_at": H.now(),
                    "behind_since": H.shift(H.now(), minutes=-minutes)}}, remote)["status"]
    monkeypatch.delenv("TICO_BACKUP_SYNC_INTERVAL", raising=False)
    assert [status_at(m) for m in (5, 15, 120)] == ["ok", "warn", "bad"]
    # Ten sync intervals is the warning time: ten minutes at 1m, a hundred minutes at 10m.
    monkeypatch.setenv("TICO_BACKUP_SYNC_INTERVAL", "10m")
    assert [status_at(m) for m in (30, 90, 110)] == ["ok", "ok", "bad"]


def test_a_replica_with_no_database_file_says_copies_are_not_arriving():
    from types import SimpleNamespace

    from backend import health, replication
    backup = {"mode": "remote", "target_kind": "s3", "last_replicated_at": None, "reason": replication.NO_RECENT_COPY}
    check = health._backups({"backup": backup}, SimpleNamespace(loopback=False))
    assert check["status"] == "warn" and check["summary"] == replication.NO_RECENT_COPY
    assert health._backups({"backup": {"mode": "remote", "last_replicated_at": None}},
                           SimpleNamespace(loopback=False))["summary"] == "Set up, but nothing has been copied yet."
    behind = {**backup, "behind_since": H.shift(H.now(), hours=-2)}
    assert health._backups({"backup": behind}, SimpleNamespace(loopback=False))["status"] == "bad"


def test_a_pile_up_of_local_files_is_a_stuck_backup():
    from types import SimpleNamespace

    from backend import health
    remote = {"mode": "remote", "target_kind": "s3", "last_replicated_at": H.now()}
    check = lambda **extra: health._backups({"backup": {**remote, **extra}}, SimpleNamespace(loopback=False))
    assert check(unsent_files=1000)["status"] == "ok"
    stuck = check(unsent_files=27914)
    assert stuck["status"] == "warn" and "27,914 unsent files" in stuck["summary"] and stuck["fixes"]
    assert check(unsent_files=27914, behind_since=H.shift(H.now(), hours=-3))["status"] == "bad"


def test_others_see_counts_not_details(environment):
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner, seconds_ago=600)
    body, checks = health_of(api, as_person(api, "quinn"))
    assert body["audience"] == "human" and body["computers"] == [] and body["waiting"] == []
    assert set(checks) == {"computers", "waiting", "queue", "failed", "stalled_runs"}
    assert all(not row["fixes"] for row in checks.values())
    assert "helper" not in checks["waiting"]["summary"]


def test_a_bot_reads_computers_and_failed_runs_without_admin_detail(environment):
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner, seconds_ago=5, runtimes=SIGNED_IN)
    add_bot(api, "qa")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('qa','hermes','x',?,'human:ana')",
                  (H.now(),))
    api.app.state.store.settings.test_identities["qa-bot"] = Identity("bot:qa", "bot", agent="hermes")
    body, checks = health_of(api, signed_in("qa-bot"))
    assert body["audience"] == "bot" and "storage" not in body
    assert body["computers"][0]["online"] and set(body["computers"][0]) == {
        "id", "label", "online", "last_seen", "platform", "disk", "update", "turn_counts", "turn_summary"}
    assert body["failures"] == [] and set(checks) == {
        "computers", "waiting", "queue", "failed", "stalled_runs", "turns:" + body["computers"][0]["id"]}
    assert api.get("/api/v2/health/issues", headers=signed_in("qa-bot")).status_code == 200


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
    # github-app.key and slack.key, when present, must be in the backup as they are now too.
    files = {"github-app.key": {"present": True, "copied_at": None, "current": False}}
    behind = check({**remote, "credential_key": held, "key_files": files})
    assert behind["status"] == "warn" and "github-app.key" in behind["summary"]
    assert check({**only, "credential_key": held, "key_files": files}, local)["status"] == "warn"
    files["github-app.key"].update(copied_at="2026-01-01T00:00:00Z", current=True)
    assert check({**remote, "credential_key": held, "key_files": files})["status"] == "ok"


def test_secrets_a_missing_key_file_cannot_open_are_named_with_the_fix(environment):
    from backend import slack_app
    api = environment()
    state = api.app.state
    settings, store, github = state.store.settings, state.store, state.github_app
    with store.transaction() as c:
        github.save(c, "human:ana", "Acme", True, {"id": 7, "slug": "acme-tico", "client_id": "Iv1.synthetic",
                                                   "pem": "synthetic-pem", "webhook_secret": "whsec-synthetic"})
        state.directory.put_credentials(c, "human:ana", "entra", {"client_secret": "s"}, "app c in t")
    slack_app.save(store, settings, "human:ana", "xoxb-synthetic-0000000000", "xapp-synthetic-0000000000")
    assert "sealed_secrets" not in health_of(api)[1]
    data = settings.db_path.parent
    for name in ("github-app.key", "slack.key"):
        (data / name).rename(data / (name + ".elsewhere"))
    check = health_of(api)[1]["sealed_secrets"]
    assert check["status"] == "warn" and check["fixes"]
    for words in ("GitHub App", "directory sync", "Slack tokens", "github-app.key is missing", "slack.key is missing",
                  "credential-key/github-app.key"):
        assert words in check["summary"]
    assert not (data / "github-app.key").exists()           # Health and the reads behind it make no new key
    (data / "slack.key").write_bytes(b"\x01" * 32)          # the wrong key is named too
    assert "the key they were sealed with" in health_of(api)[1]["sealed_secrets"]["summary"]
    for name in ("github-app.key", "slack.key"):
        (data / (name + ".elsewhere")).replace(data / name)
    assert "sealed_secrets" not in health_of(api)[1]


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
    assert checks["queue"]["status"] == "bad"
    assert "release" in checks["queue"]["summary"] and checks["queue"]["fixes"]
    [issue] = [i for i in api.get("/api/v2/health/issues", headers=signed_in()).json()["issues"] if i["kind"] == "queue"]
    assert issue["bot"] == "release" and issue["queued"] == 1 and issue["oldest_minutes"] == 0
    assert issue["reason"] == "not_ready" and "Claude login required" in issue["text"] and issue["fixes"]
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


def test_a_chat_waiting_on_its_own_model_sign_in_says_so_and_other_waits_keep_their_reason(environment):
    """The queued chat names a sign-in reason (the Set up chat shows a Sign in hint from it) only when this bot's own
    model is not signed in on its own computer. A bot on another runtime that waits on something else keeps its reason,
    even while the team's default model is signed in nowhere."""
    import json
    api = environment()
    revision = api.get("/api/v2/providers", headers=signed_in()).json()["revision"]
    assert api.put("/api/v2/providers", headers=signed_in(), json={
        "enabled": ["openai", "anthropic"], "runtime": "claude", "expected_revision": revision}).status_code == 200
    runner = enrolled(api)
    add_bot(api, "writer")
    add_bot(api, "coder")

    def report(claude):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runners SET last_seen=?, readiness_json=? WHERE id=?", (H.now(), json.dumps({
                "schema_version": 1, "runtimes": {
                    "claude": {"installed": True, "authenticated": claude, "detail": "" if claude == "ready" else "Claude login required"},
                    "codex": {"installed": True, "authenticated": "ready"}},
                "bots": {"writer": {"ready": claude == "ready", "runtime": "claude",
                                    "problems": [] if claude == "ready" else ["Claude login required"]},
                         "coder": {"ready": False, "runtime": "codex", "problems": ["Missing bot repository or AGENT.md"]}}}),
                runner))

    report("missing")
    chats = {}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('coder',?,'ana')", (json.dumps({"runtime": "codex"}),))
        owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
        for bot in ("writer", "coder"):
            c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES(?,?,1,?,'t')",
                      (bot, runner, H.now()))
            chats[bot] = H.open_conversation(c, owner, [owner, "bot:" + bot], kind="chat")
            H._write_message(c, owner, "bot:" + bot, "start setup", chats[bot], "say", {}, None, None)
    snapshot = lambda bot: api.get("/api/v2/conversations/" + chats[bot]["id"] + "/snapshot", headers=signed_in()).json()["execution"]
    writer, coder = snapshot("writer"), snapshot("coder")
    assert writer["state"] == "queued" and writer["readiness_reason"] == "model_sign_in"
    assert writer["label"] == "Saved — Claude login required"
    assert "readiness_reason" not in coder and coder["label"] == "Saved — Missing bot repository or AGENT.md"
    assert health_of(api)[1]["models"]["summary"] == "No online computer is signed in to your model."
    report("ready")
    assert "readiness_reason" not in snapshot("writer") and health_of(api)[1]["models"]["status"] == "ok"


def test_an_antigravity_bot_is_not_told_to_sign_in_to_gemini_cli(environment):
    """The sign-in hint and Health's stuck check judge the bot's own harness, not its provider's default: an Antigravity
    bot on Gemini, on a computer where the Gemini sign-in is missing, keeps its own reason and gets no Gemini sign-in fix."""
    import json
    api = environment()
    revision = api.get("/api/v2/providers", headers=signed_in()).json()["revision"]
    assert api.put("/api/v2/providers", headers=signed_in(), json={
        "enabled": ["google"], "runtime": "gemini", "expected_revision": revision}).status_code == 200
    runner = enrolled(api)
    add_bot(api, "scout")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET label='Team box', last_seen=?, readiness_json=? WHERE id=?", (H.now(), json.dumps({
            "schema_version": 1, "runtimes": {
                "gemini": {"installed": True, "authenticated": "missing", "detail": "Gemini login required"}},
            "bots": {"scout": {"ready": False, "runtime": "gemini", "problems": ["Missing bot repository or AGENT.md"]}}}),
            runner))
        c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('scout',?,'ana')",
                  (json.dumps({"runtime": "gemini", "harness": "antigravity"}),))
        c.execute("UPDATE bots SET runtime='gemini' WHERE slug='scout'")
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('scout',?,1,?,'t')",
                  (runner, H.now()))
        owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
        chat = H.open_conversation(c, owner, [owner, "bot:scout"], kind="chat")
        H._write_message(c, owner, "bot:scout", "start setup", chat, "say", {}, None, None)
    execution = api.get("/api/v2/conversations/" + chat["id"] + "/snapshot", headers=signed_in()).json()["execution"]
    assert execution["state"] == "queued" and "readiness_reason" not in execution
    assert execution["label"] == "Saved — Missing bot repository or AGENT.md"
    [row] = health_of(api)[0]["stuck"]
    assert row["bot"] == "scout" and row["why"] == "Missing bot repository or AGENT.md on Team box"
    assert "Gemini" not in json.dumps(row) and "login" not in row["fix"] and "move" not in row["fix"]
    # Judged as Gemini CLI, the same computer's answer is a sign-in: the hint the bot must not get.
    from backend.readiness import can_run
    with api.app.state.store.transaction() as c:
        assert can_run(c, "scout", "gemini")["sign_in"] is True
        assert "sign_in" not in can_run(c, "scout", "gemini", harness="antigravity")

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


def test_a_computer_with_no_bots_and_no_company_default_is_asked_to_sign_in_on_the_one_cli_it_installs(environment):
    """No default model: the computer installs only providers.default_runtime (Claude before Codex), so that is the
    one it is asked to sign in on and the one it needs; Codex, installed anyway, is only signable."""
    from backend.store import encode
    api = environment()
    with api.app.state.store.transaction() as c:      # a stored record with no default model
        c.execute("DELETE FROM registry_metadata WHERE key='providers'")
        c.execute("INSERT INTO registry_metadata VALUES('providers',?)",
                  (encode({"revision": 1, "enabled": ["openai", "anthropic"], "runtime": "", "model": ""}),))
    runner = enrolled(api)
    heartbeat(api, runner, runtimes={"codex": {"installed": True, "authenticated": "missing"},
                                     "claude": {"installed": True, "authenticated": "missing"}})
    body, checks = health_of(api)
    runtimes = {r["name"]: r for r in body["computers"][0]["runtimes"]}
    assert runtimes["claude"]["sign_in"] and runtimes["claude"]["needed"]
    assert not runtimes["codex"]["sign_in"] and not runtimes["codex"]["needed"] and runtimes["codex"]["signable"]
    assert "sign in to Claude Code" in checks["computer_signin"]["summary"]
    assert "Codex" not in checks["computer_signin"]["summary"]


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


def test_a_bot_that_has_not_finished_setup_warns_once_its_held_work_is_old(environment):
    """A parked bot holds its work on purpose, so it is not "slow"; but work an hour old means nobody is finishing setup."""
    api = environment()
    add_bot(api, "support")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, H.KEEPER, "Ticket 42", "", "bot:support")    # from before an upgrade
        c.execute("INSERT INTO bot_config(bot,config_json,operator,onboarding_state) VALUES('support','{}','ana','needs_setup') "
                  "ON CONFLICT(bot) DO UPDATE SET onboarding_state='needs_setup'")
    assert "setup:support" not in health_of(api)[1]                 # just filed: setup may be under way
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created='2026-01-02T03:04:05Z' WHERE id=?", (task["id"],))
    check = health_of(api)[1]["setup:support"]
    assert check["status"] == "warn" and check["summary"] == "support is still onboarding; 1 task waiting since 2026-01-02."
    assert check["label"] == "Onboarding not finished"
    assert check["fixes"][0]["href"] == "#/bot/support"
    assert "setup:support" not in health_of(api, as_person(api, "quinn"))[1]


def _parked(api, slug, days_ago, created_by="", operator="morgan"):
    add_bot(api, slug)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_config(bot,config_json,operator,onboarding_state,created_by) VALUES(?,'{}',?,'needs_setup',?) "
                  "ON CONFLICT(bot) DO UPDATE SET onboarding_state='needs_setup',operator=excluded.operator,"
                  "created_by=excluded.created_by", (slug, operator, created_by))
        c.execute("UPDATE bots SET created=? WHERE slug=?", (H.shift(H.now(), days=-days_ago), slug))


def test_a_bot_left_in_setup_for_a_day_is_stuck_and_its_manager_may_finish_it(environment):
    """Overview's stuck list names a bot parked over a day, and only someone who may manage it gets the finish."""
    api = environment()
    _parked(api, "support", 14)
    _parked(api, "sales", 0)                        # added today: setup may be under way
    stuck = {row["bot"]: row for row in health_of(api)[0]["stuck"]}
    assert set(stuck) == {"support"} and stuck["support"]["reason"] == "setup"
    assert stuck["support"]["why"] == "Stuck in onboarding for 14 days"
    assert stuck["support"]["fix"]["finish"] == {"bot": "support"}
    quinn = as_person(api, "quinn")                 # neither an owner of it nor an administrator
    assert all(row["fix"] is None for row in health_of(api, quinn)[0]["stuck"])
    assert api.post("/api/v2/bots/support/onboarded", json={}, headers=quinn).status_code == 403
    assert api.post("/api/v2/bots/support/onboarded", json={}, headers=signed_in()).json()["onboarding_state"] == "onboarded"
    assert health_of(api)[0]["stuck"] == []


def test_a_bot_left_in_setup_is_nudged_once_to_whoever_its_setup_reminder_goes_to(environment):
    """The same person as the "Finish onboarding" reminder, and nobody while that reminder is open: one ask per bot."""
    from backend import setup_stuck
    api = environment()
    as_person(api, "quinn")
    _parked(api, "support", 3, created_by="human:quinn")
    _parked(api, "sales", 3, operator="quinn")      # nobody known added it: its operator, as the reminder picks
    _parked(api, "ops", 3, created_by="human:quinn")
    with api.app.state.store.transaction() as c:
        assert H._setup_reminder(c, "ops")["owner"] == "human:quinn"     # it already has its ask
        told = setup_stuck.nudge(c)
        assert told == {"support": ["human:quinn"], "sales": ["human:quinn"]}
        assert setup_stuck.nudge(c) == {}
        notes = c.execute("SELECT to_actor,body FROM messages WHERE from_actor='keeper' AND body LIKE '%waiting in setup%'")
        assert sorted(r["to_actor"] for r in notes) == ["human:quinn", "human:quinn"]


def test_a_repository_a_computer_could_not_copy_is_named_with_gits_reason(request):
    from backend.repositories import save_metadata
    api = request.getfixturevalue("environment")()
    runner = enrolled(api)
    heartbeat(api, runner, seconds_ago=5, runtimes=SIGNED_IN)
    why = ("Cloning the local mirror of acme/web failed: error: unable to create file a: Filename too long. "
           "Retrying in 5 minutes, or restart the computer's runner to retry now")
    with api.app.state.store.transaction() as c:
        save_metadata(c, "computer-repositories:" + runner, {"repositories": [
            {"full_name": "acme/web", "state": "failed", "last_fetch": None, "size_mb": 0, "error": why},
            {"full_name": "acme/api", "state": "cloned", "last_fetch": None, "size_mb": 1}]})
    check = health_of(api)[1]["repository_copies"]
    assert check["status"] == "warn"
    assert "acme/web on " in check["summary"] and "Filename too long" in check["summary"]
    assert "acme/api" not in check["summary"]
    heartbeat(api, runner, seconds_ago=3600, runtimes=SIGNED_IN)          # an offline computer is reported elsewhere
    assert "repository_copies" not in health_of(api)[1]


def _hosted(api, runner, bot, report):
    import json
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET label='Team box', last_seen=?, readiness_json=? WHERE id=?",
                  (H.now(), json.dumps({"schema_version": 1, "runtimes": SIGNED_IN, "bots": {bot: report}}), runner))
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES(?,?,1,?,'t') "
                  "ON CONFLICT(bot) DO NOTHING", (bot, runner, H.now()))


def test_a_harness_deny_on_a_github_write_the_access_allows_is_named(environment):
    """A bot that may open pull requests but whose Claude Code settings deny `gh pr create` stops at that step; a bot
    that only reads keeps its denies, and the deny on its own repository is not counted."""
    api = environment()
    runner = enrolled(api)
    add_bot(api, "monitor")
    deny = "Claude Code settings deny: gh issue, gh pr create, git push"
    tool = {"service": "github", "identity": "acme/app", "can": ["read", "write", "pull_requests"],
            "scope": {"repo": "acme/app"}}
    _hosted(api, runner, "monitor", {"ready": True, "runtime": "claude", "warnings": [deny], "tools": [tool]})
    check = health_of(api)[1]["harness_denies"]
    assert check["status"] == "warn" and check["label"] == "Harness permissions"
    assert "monitor may write to acme/app" in check["summary"]
    assert "gh pr create, gh issue create, gh issue comment, git push" in check["summary"]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('sam','Sam','sam@example.com')")
    assert "harness_denies" not in health_of(api, as_person(api, "sam"))[1]
    _hosted(api, runner, "monitor", {"ready": True, "runtime": "claude", "warnings": [deny],
                                     "tools": [dict(tool, can=["read"])]})
    assert "harness_denies" not in health_of(api)[1]
    _hosted(api, runner, "monitor", {"ready": True, "runtime": "claude", "warnings": [deny],
                                     "tools": [dict(tool, identity="bot-monitor", scope={"repo": "bot-monitor"})]})
    assert "harness_denies" not in health_of(api)[1]
    # Denying merge and review to a bot that opens pull requests is a policy, not a leftover.
    _hosted(api, runner, "monitor", {"ready": True, "runtime": "claude", "tools": [tool],
                                     "warnings": ["Claude Code settings deny: gh pr merge, gh pr review, gh release create"]})
    assert "harness_denies" not in health_of(api)[1]


def test_a_read_only_reviewer_given_every_repository_keeps_its_denies(environment, monkeypatch):
    """Every repository comes with write by default; a bot whose bot.yaml reads GitHub only is not flagged for it,
    while a repository a person chose for it with write is."""
    api = environment()
    settings = api.app.state.store.settings
    monkeypatch.setattr(settings, "github_owner", "acme")
    runner = enrolled(api)
    add_bot(api, "reviewer")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO repositories(full_name,enabled,bot_repo) VALUES('acme/product',1,0)")
        c.execute("INSERT INTO bot_config(bot,config_json,repo,operator) VALUES('reviewer',?,'bot-reviewer','ana') ON CONFLICT(bot) DO "
                  "UPDATE SET config_json=excluded.config_json,repo=excluded.repo", ('{"repo_access_mode": "all"}',))
    read = {"service": "github", "identity": "acme/docs", "can": ["read"], "scope": {"repo": "acme/docs"}}
    deny = "Claude Code settings deny: gh pr comment, gh pr create, gh pr review"
    _hosted(api, runner, "reviewer", {"ready": True, "runtime": "claude", "warnings": [deny], "tools": [read]})
    from backend.bot_tools import listing
    with api.app.state.store.read() as c:
        assert any(t["identity"] == "acme/product" and "write" in t["can"] for t in listing(c, settings, "reviewer")["tools"])
    assert "harness_denies" not in health_of(api)[1]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='reviewer'", ('{"repo_access_mode": "chosen"}',))
        c.execute("INSERT INTO bot_repo_access VALUES('reviewer','acme/product','write')")
    summary = health_of(api)[1]["harness_denies"]["summary"]
    assert "reviewer may write to acme/product" in summary and "gh pr create, gh pr comment" in summary


def test_harness_denies_answers_each_bot_as_a_bot_by_bot_pass_does(environment, monkeypatch):
    """The shared reads behind harness_denies give each bot the answer its own listing gives, for bots in each state."""
    from backend import bot_tools
    from backend.health import harness_denies
    api = environment()
    settings = api.app.state.store.settings
    monkeypatch.setattr(settings, "github_owner", "acme")
    runner = enrolled(api)
    deny = "Claude Code settings deny: gh pr create, git push"
    write = {"service": "github", "identity": "acme/app", "can": ["read", "write", "pull_requests"], "scope": {"repo": "acme/app"}}
    states = {                                  # bot: (its bot_config row's settings, or None for no row, its bot.yaml tools)
        "chosen": ('{"repo_access_mode": "chosen"}', [write]),
        "reader": ('{"repo_access_mode": "own"}', [dict(write, can=["read"])]),
        "owner": ('{"repo_access_mode": "own"}', [dict(write, identity="acme/bot-owner", scope={"repo": "acme/bot-owner"})]),
        "everywhere": ('{"repo_access_mode": "all"}', [dict(write, identity="acme/other", scope={"repo": "acme/other"})]),
        "unconfigured": (None, [write]),
    }
    readiness = {}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO repositories(full_name,enabled,bot_repo) VALUES('acme/shared',1,0)")
    for slug, (config, tools) in states.items():
        add_bot(api, slug)
        readiness[slug] = {"ready": True, "runtime": "claude", "warnings": [deny], "tools": tools}
        with api.app.state.store.transaction() as c:
            if config:
                c.execute("INSERT INTO bot_config(bot,config_json,repo,operator) VALUES(?,?,?,'ana')", (slug, config, "bot-" + slug))
            c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES(?,?,1,?,'t')", (slug, runner, H.now()))
            if slug == "chosen":
                c.execute("INSERT INTO bot_repo_access VALUES('chosen','acme/app','write')")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET label='Team box', last_seen=?, readiness_json=? WHERE id=?",
                  (H.now(), json.dumps({"schema_version": 1, "runtimes": SIGNED_IN, "bots": readiness}), runner))
    with api.app.state.store.read_transaction() as c:
        batched = harness_denies(c, settings)
        # The bot-by-bot pass: each bot's listing reads its own rows, with nothing primed and no shared reads.
        listing = bot_tools.listing
        monkeypatch.setattr(bot_tools, "prime_state", lambda c, bots: None)
        monkeypatch.setattr(bot_tools, "listing", lambda c, settings, bot, reads=None: listing(c, settings, bot))
        bot_by_bot = harness_denies(c, settings)
    assert batched == bot_by_bot
    assert {bot for bot, _, _ in batched} == {"chosen", "everywhere", "unconfigured"}
    # A failure in the shared read falls back to the bot-by-bot pass instead of ending the health response.
    def unreadable(c, bots):
        raise RuntimeError("unreadable")
    with api.app.state.store.read_transaction() as c:
        monkeypatch.setattr(bot_tools, "prime_state", unreadable)
        assert harness_denies(c, settings) == batched


def _listened(api, *, routed_days_ago, saved_days_ago, judged=None):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO listen_items(id,source,native_id,url,content,content_hash,first_seen_at) "
                  "VALUES('i1','reddit','1','https://example.com/1','a post','h1',?)",
                  (H.shift(H.now(), days=-routed_days_ago - 1),))
        c.execute("INSERT INTO intake_items(id,destination,item_id,judgment_id,created_at) VALUES('in1','leads','i1','j1',?)",
                  (H.shift(H.now(), days=-routed_days_ago),))
        c.execute("INSERT INTO listen_items(id,source,native_id,url,content,content_hash,first_seen_at) "
                  "VALUES('i2','reddit','2','https://example.com/2','a later post','h2',?)",
                  (H.shift(H.now(), days=-saved_days_ago),))
        if judged:
            c.execute("INSERT INTO listen_judgments(id,item_id,question_set,scores_json,judged_at) VALUES('j1','i1',?,'{}',?)",
                      (judged, H.shift(H.now(), days=-routed_days_ago)))


def test_listening_that_routed_before_but_lost_its_registry_file_is_bad(environment, monkeypatch):
    """A server move that leaves listening.yaml behind keeps saving posts and sends them nowhere, silently."""
    from backend import listening
    monkeypatch.setattr(listening, "_warning_revisions", listening.OrderedDict())
    api = environment()
    settings = api.app.state.store.settings
    (settings.registry_dir / "listening.yaml").unlink(missing_ok=True)
    assert "listening" not in health_of(api)[1]                 # never routed: nothing was lost
    _listened(api, routed_days_ago=14, saved_days_ago=2)
    check = health_of(api)[1]["listening"]
    assert check["status"] == "bad"
    assert "listening.yaml is missing from the company registry" in check["summary"]
    assert "1 post saved since went to no inbox" in check["summary"]
    (settings.registry_dir / "listening.yaml").write_text("destinations: {}\n")
    check = health_of(api)[1]["listening"]          # emptied on purpose is possible: a warning, not bad
    assert check["status"] == "warn" and "has no valid destinations" in check["summary"]


def test_listening_that_stopped_saving_posts_is_not_flagged(environment, monkeypatch):
    from backend import listening
    monkeypatch.setattr(listening, "_warning_revisions", listening.OrderedDict())
    api = environment()
    (api.app.state.store.settings.registry_dir / "listening.yaml").unlink(missing_ok=True)
    _listened(api, routed_days_ago=60, saved_days_ago=40)
    assert "listening" not in health_of(api)[1]


def test_listening_scoring_with_the_shipped_questions_after_its_own_went_missing_is_named(environment, monkeypatch):
    from backend import listening
    monkeypatch.setattr(listening, "_warning_revisions", listening.OrderedDict())
    api = environment()
    settings = api.app.state.store.settings
    (settings.registry_dir / "listening.yaml").write_text(
        "destinations:\n  leads: {category: lead, threshold: 0.75, receiver: bot:ana}\n")
    shipped = listening.question_set(settings)
    _listened(api, routed_days_ago=3, saved_days_ago=1, judged=f"listening-item@{shipped['version'] + 4}")
    check = health_of(api)[1]["listening"]
    assert check["status"] == "warn" and f"({shipped['label']})" in check["summary"]
    assert f"listening-item@{shipped['version'] + 4}" in check["summary"]


def test_a_computer_on_the_python_runner_is_told_to_update_and_the_heartbeat_takes_the_engine(environment):
    import json
    from backend.models import StructuredReadiness
    report = StructuredReadiness.model_validate({"engine": {"name": "python", "note": "TICO_RUNNER_ENGINE=python"}})
    assert report.model_dump()["engine"] == {"name": "python", "build": "", "note": "TICO_RUNNER_ENGINE=python"}
    assert "engine" not in StructuredReadiness.model_validate({}).model_dump()
    api = environment()
    runner = enrolled(api)
    for engine, warned in (({"name": "rust", "build": "0123456789abcdef"}, False),
                           ({"name": "python", "note": "TICO_RUNNER_ENGINE=python"}, True)):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runners SET label='Team box', last_seen=?, readiness_json=? WHERE id=?",
                      (H.now(), json.dumps({"schema_version": 1, "runtimes": {}, "bots": {}, "engine": engine}), runner))
        check = health_of(api)[1].get("engine:" + runner)
        assert bool(check) == warned
    assert check["status"] == "warn" and check["summary"] == (
        "Team box runs the Python runner, which Tico no longer ships. Update this computer.")


def claimable(c, runner, *bots):
    """`bots` as a claim on `runner` could take their work: configured, and ready there. Anything less is a reason the
    claim holds the work (execution.claim_refusal), which Health names instead of "slow"."""
    import json
    for bot in bots:
        c.execute("INSERT OR IGNORE INTO bot_config(bot,config_json,operator) VALUES(?,'{}','ana')", (bot,))
    document = json.loads(c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner,)).fetchone()[0] or "{}")
    document.setdefault("bots", {}).update({bot: {"ready": True} for bot in bots})
    c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (json.dumps(document), runner))


def test_refusal_quarantine_explains_the_claim_hold_and_matches_bot_status(environment):
    api = environment()
    runner = silent_computer(api, 0)
    words = "Only a person changes a task number; leave the requested number for the person managing this bot."
    with api.app.state.store.transaction() as c:
        claimable(c, runner, "helper")
        for _ in range(H.QUARANTINE_AT + 1):
            with pytest.raises(H.Refused):
                H.refuse(c, "bot:helper", "identity", words)
        assert H.bot(c, "helper")["state"] == "quarantined"
        c.execute("UPDATE events SET ts='2030-10-26T23:55:00Z' WHERE action='quarantine' AND target='bot:helper'")
    body, checks = health_of(api)
    [held] = body["stuck"]
    status = api.get("/api/v2/status", headers=signed_in()).json()["bots"]
    quarantine = next(row for row in status if row["bot"] == "helper")["quarantine"]
    assert held["reason"] == "quarantined" and held["why"] == quarantine["why"]
    assert quarantine["auto"] and quarantine["resumes_at"] == "2030-10-27T00:55:00.000000Z"
    assert "rule 8; 10 repeated refusals today" in held["why"]
    assert "2030-10-27 00:55 UTC" in held["why"] and "resumes by itself" in held["why"]
    assert "Refused write (identity): " + words in held["why"]
    assert "BotOps must review" not in held["why"]
    assert "a person who manages it can clear it sooner" in held["why"]
    # Both the summary report and its diagnostic items must exclude manager-only refused words.
    assert "Quarantined" in checks["queue"]["summary"]
    assert words not in json.dumps(checks["queue"])
    assert "Refused write" not in json.dumps(checks["queue"])


def test_manual_quarantine_has_no_lift_promise_and_other_holds_keep_their_explanation(environment):
    api = environment()
    runner = silent_computer(api, 0)
    with api.app.state.store.transaction() as c:
        claimable(c, runner, "helper")
        H.quarantine(c, "helper", "A person is checking its work")
    [held] = health_of(api)[0]["stuck"]
    status = api.get("/api/v2/status", headers=signed_in()).json()["bots"]
    quarantine = next(row for row in status if row["bot"] == "helper")["quarantine"]
    assert held["why"] == quarantine["why"] == "Quarantined until a person clears it."
    assert not quarantine["auto"] and quarantine["resumes_at"] is None
    with api.app.state.store.transaction() as c:
        H.status_set(c, "human:morgan", "helper", state="active", reason="Checked")
        H.status_set(c, "human:morgan", "helper", state="paused", reason="Waiting")
    body, checks = health_of(api)
    [paused] = body["stuck"]
    assert paused["reason"] == "paused"
    assert paused["why"] == "Paused: its owner or BotOps must resume it"
    assert paused["why"] in checks["queue"]["items"][0]["text"]


def test_quarantine_refused_words_keep_manager_access_and_mask_secrets(environment):
    api = environment()
    runner = silent_computer(api, 0)
    with api.app.state.store.transaction() as c:
        claimable(c, runner, "helper")
        H.quarantine(c, "helper", "10 repeated refusals today",
                     refusal={"rule": "identity", "detail": "Private request token=synthetic-private-value"})
    [held] = health_of(api)[0]["stuck"]
    assert "token=[hidden]" in held["why"] and "synthetic-private-value" not in held["why"]
    reader = as_person(api, "quinn")
    status = api.get("/api/v2/status", headers=reader).json()["bots"]
    why = next(row for row in status if row["bot"] == "helper")["quarantine"]["why"]
    assert "resumes by itself" in why and "Refused write" not in why and "Private request" not in why
    [held] = health_of(api, reader)[0]["stuck"]
    assert held["why"] == why


def test_an_interrupted_run_fence_explains_a_busy_bot_queue(environment, monkeypatch):
    from backend import execution, health

    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner)
    add_bot(api, "helper")
    old = H.shift(H.now(), minutes=-30)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('helper',?,1,?,'t')",
                  (runner, H.now()))
        claimable(c, runner, "helper")
        owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
        task = H.task_create(c, owner, "Interrupted task", "", "bot:helper", private=False)
        interrupted = H.task_comment(c, owner, task["id"], "interrupted work")
        interrupted_job = c.execute("SELECT id FROM jobs WHERE message_id=?", (interrupted["id"],)).fetchone()["id"]
        c.execute("UPDATE jobs SET state='uncertain',created=? WHERE id=?", (old, interrupted_job))
        H.task_comment(c, owner, task["id"], "queued behind interrupted work")
        c.execute("UPDATE jobs SET created=? WHERE bot='helper' AND state='queued'", (old,))

        running_chat = H.open_conversation(c, owner, [owner, "bot:helper"], kind="chat")
        H._write_message(c, owner, "bot:helper", "running turn", running_chat, "say", {}, None, None)
        running_job = c.execute("SELECT id FROM jobs WHERE bot='helper' AND state='queued' ORDER BY created DESC LIMIT 1").fetchone()["id"]
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (running_job,))
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,started) "
                  "VALUES('helper-running',?,'helper',?,1,'x','running',?,?,?)",
                  (running_job, runner, H.shift(H.now(), hours=1), H.now(), H.now()))
        online = {r["id"] for r in health._online_runners(c)}
        monkeypatch.setattr(execution, "bot_claimable", lambda *args, **kwargs: ("lane_held", None, False))

        [row] = health._waiting(c, online)[1]
        assert row["bot"] == "helper" and row["queued"] == 0 and row["dead"] >= 1 and row["reason"] == "uncertain"

        # Removing the fenced-only override reproduces the old, misleading busy reason.
        original = health._fenced_queue_reason
        monkeypatch.setattr(health, "_fenced_queue_reason", lambda row, refused, first, person: (refused, first, person))
        [without_override] = health._waiting(c, online)[1]
        assert without_override["reason"] == "busy"
        monkeypatch.setattr(health, "_fenced_queue_reason", original)

        # With every slot occupied, no_slot is the truthful immediate wait reason, while dead stays explicit.
        c.execute("UPDATE runners SET capacity=1 WHERE id=?", (runner,))
        [full] = health._waiting(c, online)[1]
        assert (full["reason"], full["wait_reason"]) == ("busy", "no_slot") and full["dead"] >= 1


def fenced_task_jobs(c, bot, created):
    """A task for `bot` with an interrupted run on it: the IDs of its jobs queued behind that run (Pipeline's `dead`)."""
    before = {r["id"] for r in c.execute("SELECT id FROM jobs WHERE bot=? AND state='queued'", (bot,))}
    owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
    task = H.task_create(c, owner, "Interrupted task", "", "bot:" + bot, private=False)
    interrupted = H.task_comment(c, owner, task["id"], "interrupted work")
    c.execute("UPDATE jobs SET state='uncertain',created=? WHERE message_id=?", (created, interrupted["id"]))
    H.task_comment(c, owner, task["id"], "queued behind interrupted work")
    jobs = [r["id"] for r in c.execute("SELECT id FROM jobs WHERE bot=? AND state='queued'", (bot,)) if r["id"] not in before]
    c.executemany("UPDATE jobs SET created=? WHERE id=?", [(created, job) for job in jobs])
    return jobs


def test_work_held_on_purpose_behind_an_interrupted_run_is_not_stuck(environment):
    """Pipeline counts a held job behind an interrupted run as dead, but the claim never reads held work: a bot whose
    only waiting work is held on purpose is neither stuck nor slow, however old it is."""
    from backend import health

    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner)
    add_bot(api, "helper")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('helper',?,1,?,'t')",
                  (runner, H.now()))
        claimable(c, runner, "helper")
        jobs = fenced_task_jobs(c, "helper", H.shift(H.now(), minutes=-30))
        online = {r["id"] for r in health._online_runners(c)}
        # Unheld, the fenced work is stuck behind the interrupted run until someone reviews it.
        [row] = health._waiting(c, online)[2]
        assert (row["bot"], row["reason"], row["queued"], row["dead"]) == ("helper", "uncertain", 0, len(jobs))
        c.executemany("UPDATE jobs SET held=1 WHERE id=?", [(job,) for job in jobs])
        assert health._waiting(c, online)[1:] == ([], [])


def test_a_silent_computer_splits_its_queue_as_pipeline_does(environment):
    """Behind a computer that stopped checking in, work an interrupted run fences is not counted as queued."""
    from backend import health

    api = environment()
    silent_computer(api, 1900)
    with api.app.state.store.transaction() as c:
        jobs = fenced_task_jobs(c, "helper", H.now())
        [computer] = health.silent_computers(c)
    assert jobs and (computer["queued"], computer["dead"]) == (1, len(jobs))


def test_a_persons_message_waiting_two_minutes_on_a_computer_with_room_is_stuck(environment):
    """Someone is sitting there waiting for the reply: two minutes, not the fifteen other queued work gets."""
    from backend import health
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner)
    add_bot(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('ops',?,1,?,'t')",
                  (runner, H.now()))
        claimable(c, runner, "ops")
        owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
        chat = H.open_conversation(c, owner, [owner, "bot:ops"], kind="chat")
        H._write_message(c, owner, "bot:ops", "hello?", chat, "say", {}, None, None)
        online = {r["id"] for r in health._online_runners(c)}
        assert health._waiting(c, online)[1] == []                    # just sent
        c.execute("UPDATE jobs SET created=? WHERE bot='ops'", (H.shift(H.now(), minutes=-3),))
        [row] = health._waiting(c, online)[1]
        assert row["bot"] == "ops" and row["reason"] == "slow"
        c.execute("UPDATE messages SET from_actor='bot:helper' WHERE conversation_id=?", (chat["id"],))
        assert health._waiting(c, online)[1] == []                    # a bot's request gets the usual fifteen


def test_work_a_computer_holds_while_it_waits_to_update_is_the_update_not_a_tico_bug(environment):
    """Past its drain a computer due an update claims nothing while any turn runs: its queued work waits on that."""
    from backend import health, health_monitor
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner)
    add_bot(api, "ops")
    add_bot(api, "dev")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET label='Team box' WHERE id=?", (runner,))
        for bot in ("ops", "dev"):
            c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES(?,?,1,?,'t')",
                      (bot, runner, H.now()))
        claimable(c, runner, "ops", "dev")
        owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
        for bot in ("ops", "dev"):
            chat = H.open_conversation(c, owner, [owner, "bot:" + bot], kind="chat")
            H._write_message(c, owner, "bot:" + bot, "hello?", chat, "say", {}, None, None)
        job = c.execute("SELECT id FROM jobs WHERE bot='dev'").fetchone()["id"]
        c.execute("UPDATE jobs SET state='leased' WHERE id=?", (job,))
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) "
                  "VALUES('a-dev',?,'dev',?,1,'x','running',?,?)", (job, runner, H.shift(H.now(), hours=1), H.now()))
        c.execute("UPDATE jobs SET created=? WHERE bot='ops'", (H.shift(H.now(), minutes=-30),))
        online = {r["id"] for r in health._online_runners(c)}
        [row] = health._waiting(c, online)[2]
        assert row["bot"] == "ops" and row["reason"] == "slow"         # nothing says it is updating: a bug
        c.execute("INSERT INTO runner_versions(runner_id,release,kind,update_state,update_target,reported) "
                  "VALUES(?,'0.4.46','docker','waiting','0.4.48',?)", (runner, H.now()))
        [row] = health._waiting(c, online)[2]
        assert row["reason"] == "updating" and row["running"] == 1 and row["target"] == "0.4.48"
        [turn] = row["turns"]
        assert turn["run"] == "a-dev" and turn["bot"] == "dev" and turn["task_title"] == ""   # a chat: no task
        since = turn["since"][11:16]
        found = {f["check_id"]: f for f in health_monitor.findings(c, api.app.state.store.settings)}
    assert "queue_idle" not in found
    held = found["runner_outdated"]
    assert held["kind"] == health_monitor.SETUP
    assert (f"Team box is holding new work to update to Tico 0.4.48: it starts once dev's turn (running since {since} "
            "UTC) ends; it holds 1 queued job(s) for ops.") in held["detail"]
    # Nothing to update by hand, and nothing to watch: BotOps is told not to hold its own computer watching this one.
    assert "BotOps: update the runner" not in held["fix"] and "record the wait" in held["fix"]
    assert "person's choice" in held["fix"]
    body, checks = health_of(api)
    [stuck] = body["stuck"]
    assert stuck["reason"] == "updating" and stuck["why"].startswith("Team box is holding new work to update")
    assert stuck["why"].endswith(health.OUTSIDE_WAIT)
    assert checks["queue"]["status"] == "warn"
    # Anything else wrong with the update still asks BotOps to update it: an error, an incompatible release, or an
    # update under way with no running turn to wait for (the updater itself may be stuck).
    store = api.app.state.store

    def fix(**change):
        with store.transaction() as c:
            c.execute("UPDATE runner_versions SET " + ",".join(f"{k}=?" for k in change) + " WHERE runner_id=?",
                      (*change.values(), runner))
            found = {f["check_id"]: f for f in health_monitor.findings(c, store.settings)}["runner_outdated"]["fix"]
            c.execute("UPDATE runner_versions SET release='0.4.46',update_state='waiting',update_error='' WHERE runner_id=?",
                      (runner,))
        return found
    assert fix(update_error="could not start the update: OSError").startswith("BotOps: update the runner on Team box")
    assert fix(release="0.0.1").startswith("BotOps: update the runner on Team box")
    with store.transaction() as c:
        c.execute("UPDATE attempts SET state='finished' WHERE id='a-dev'")
    assert fix(update_state="updating").startswith("BotOps: update the runner on Team box")


def test_the_update_hold_names_each_turn_by_bot_task_and_start_but_never_a_private_task(environment):
    """TK-SHNKOY4E: a Mac held its update ~28 min behind one BotOps turn that watched another computer's update.
    Health names the bot, its task and when it started, so a person can tell that turn from real work."""
    from backend import health
    api = environment()
    runner = enrolled(api)
    heartbeat(api, runner)
    for bot in ("ops", "botops", "audit"):
        add_bot(api, bot)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET label='Mac' WHERE id=?", (runner,))
        c.execute("UPDATE bots SET display_name='BotOps' WHERE slug='botops'")
        for bot in ("ops", "botops", "audit"):
            c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES(?,?,1,?,'t')",
                      (bot, runner, H.now()))
        owner = "human:" + c.execute("SELECT id FROM humans ORDER BY id LIMIT 1").fetchone()[0]
        for bot in ("ops", "botops", "audit"):
            chat = H.open_conversation(c, owner, [owner, "bot:" + bot], kind="chat")
            H._write_message(c, owner, "bot:" + bot, "hello?", chat, "say", {}, None, None)
        started = {"audit": H.shift(H.now(), minutes=-5), "botops": H.shift(H.now(), minutes=-28)}   # newest first
        run = {"audit": "a-audit", "botops": "b-botops"}   # ids and slugs sort newest first too: only `since` orders
        for bot, since in started.items():
            job = c.execute("SELECT id FROM jobs WHERE bot=?", (bot,)).fetchone()["id"]
            c.execute("UPDATE jobs SET state='leased' WHERE id=?", (job,))
            c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,"
                      "started) VALUES(?,?,?,?,1,?,'running',?,?,?)",
                      (run[bot], job, bot, runner, "x" + bot, H.shift(H.now(), hours=1), since, since))
        watch = H.task_create(c, "bot:botops", "Watch HotMic's update to 0.3.48", "x", "bot:botops", lint=False)
        secret = H.task_create(c, "bot:audit", "Private release gate", "x", "bot:audit", lint=False, private=True)
        c.execute("UPDATE tasks SET carried_by='b-botops' WHERE id=?", (watch["id"],))
        c.execute("UPDATE tasks SET carried_by='a-audit' WHERE id=?", (secret["id"],))
        c.execute("UPDATE jobs SET created=? WHERE bot='ops'", (H.shift(H.now(), minutes=-30),))
        c.execute("INSERT INTO runner_versions(runner_id,release,kind,update_state,update_target,reported) "
                  "VALUES(?,'0.3.46','mac','waiting','0.3.48',?)", (runner, H.now()))
        online = {r["id"] for r in health._online_runners(c)}
        [row] = health._waiting(c, online)[2]
        assert row["reason"] == "updating" and row["running"] == 2
        botops, qa = row["turns"]                                       # oldest first
        assert (botops["bot"], botops["run"], botops["task"], botops["task_title"]) == (
            "botops", "b-botops", watch["id"], "Watch HotMic's update to 0.3.48")
        assert (qa["bot"], qa["task"], qa["task_title"]) == ("audit", "", "")   # its only task is private
        words = health.updating_words(row)
        assert words.startswith("Mac is holding new work to update to Tico 0.3.48: it starts once 2 running turns "
                                f"end: BotOps's turn (running since {started['botops'][11:16]} UTC); audit's turn")
        assert "Private release gate" not in words and words.endswith(health.OUTSIDE_WAIT)
        one = {**row, "turns": row["turns"][:1], "running": 1}
        assert (f"it starts once BotOps's turn on \u201cWatch HotMic's update to 0.3.48\u201d (running since "
                f"{started['botops'][11:16]} UTC) ends. {health.OUTSIDE_WAIT}") in health.updating_words(one)

        # Health says it to each caller as they may read it: a bot whose activity they may not read is "another
        # bot", and a task hidden from them (auth.task_sql) is not named (Senior review of PR 638).
        class Access:
            def __init__(self, unreadable, tasks):
                self.unreadable, self.tasks = unreadable, tasks

            def unreadable_bots(self, c, who):
                return self.unreadable

            def task_sql(self, c, who):
                return self.tasks

        def why(auth):
            [out] = health._stuck(c, [{**one, "runtime": ""}], online, None, False, auth)
            return out["why"]
        since = started["botops"][11:16]
        assert f"BotOps's turn on \u201cWatch HotMic" in why(Access(set(), "1"))
        assert f"once another bot's turn (running since {since} UTC) ends" in why(Access({"botops"}, "1"))
        assert f"once BotOps's turn (running since {since} UTC) ends" in why(Access(set(), "0"))
        assert f"once another bot's turn (running since {since} UTC) ends" in why(None)
        assert "Watch HotMic" not in why(Access({"botops"}, "1")) + why(Access(set(), "0")) + why(None)


def test_roadmap_suggestions_warn_from_the_routing_rule_and_only_to_administrators(environment):
    api = environment()
    for slug in ("pm", "ops"):
        add_bot(api, slug)

    def health(chosen=None, labels=("roadmap",), headers=None, **states):
        with api.app.state.store.transaction() as c:
            c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES('pm',?,'morgan') ON CONFLICT(bot) "
                      "DO UPDATE SET config_json=excluded.config_json", ('{"template": "product-manager"}',))
            c.execute("UPDATE bots SET state='active', display_name=upper(slug) WHERE slug IN ('pm','ops')")
            for bot, state in states.items():
                c.execute("UPDATE bots SET state=? WHERE slug=?", (state, bot))
            c.execute("INSERT INTO registry_metadata(key,value_json) VALUES('roadmaps',?) ON CONFLICT(key) "
                      "DO UPDATE SET value_json=excluded.value_json",
                      (json.dumps({"labels": list(labels), "suggestions_bot": chosen}),))
        return health_of(api, headers)[1].get("roadmap_suggestions")

    def label(**kwargs):
        row = health(**kwargs)
        return row and row["label"]

    fix = [{"label": "Open Public roadmap", "href": "#/settings/roadmap", "tab": "", "click": ""}]
    assert health(labels=(), pm="archived") is None              # no public label: nobody can suggest anything
    assert health() is None                                      # Automatic finds the Product Manager
    assert health(pm="archived") == {
        "id": "roadmap_suggestions", "label": "No bot receives roadmap suggestions", "status": "warn",
        "summary": "Visitors who suggest a feature on your public roadmap are being turned away.", "fixes": fix}
    archived = health(chosen="ops", ops="archived")
    assert archived["label"] == "The bot chosen for roadmap suggestions is archived; suggestions now go to PM"
    assert (archived["status"], archived["fixes"]) == ("warn", fix)
    assert label(chosen="ops", ops="paused") == (
        "The bot chosen for roadmap suggestions isn't active; suggestions now go to PM")
    assert label(chosen="ops", ops="archived", pm="archived") == "No bot receives roadmap suggestions"
    assert health(chosen="ops", pm="archived") is None           # choosing an active bot clears it
    assert health(pm="archived", headers=as_person(api, "quinn")) is None


def test_health_names_queued_bot_and_stalled_tasks_for_bot_reader(environment):
    api = environment()
    runner = enrolled(api)
    add_bot(api, "helper")
    add_bot(api, "observer")
    api.app.state.store.settings.test_identities["observer-bot"] = Identity("bot:observer", "bot", agent="hermes")
    old = H.shift(H.now(), hours=-3)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('observer','hermes','x',?,'human:ana')", (H.now(),))
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('helper',?,1,?,'t')", (runner, H.now()))
        c.execute("UPDATE runners SET last_seen=? WHERE id=?", (H.now(), runner))
        cv = H.open_conversation(c, 'human:morgan', ['human:morgan', 'bot:helper'], kind='chat')
        H._write_message(c, 'human:morgan', 'bot:helper', 'check the queue', cv, 'say', {}, None, None)
        c.execute("UPDATE jobs SET created=? WHERE bot='helper'", (old,))
        tasks = [H.task_create(c, 'human:morgan', title, '', 'bot:helper', private=False)
                 for title in ('Repair the queue', 'Check the diagnosis')]
        for task in tasks:
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (old, task['id']))
            H.event(c, H.KEEPER, 'task.stall_escalated', task['id'], {'wakes': 3})
        c.execute("UPDATE bots SET state='paused' WHERE slug='helper'")
    headers = signed_in('observer-bot')
    body, checks = health_of(api, headers)
    [queued] = checks['queue']['items']
    assert (queued['bot'], queued['queued'], queued['oldest'], queued['oldest_minutes'], queued['reason']) == ('helper', 3, old, 180, 'paused')
    assert queued['fix'] == 'hub run list helper' and queued['fixes']
    issues = api.get('/api/v2/health/issues', headers=headers).json()['issues']
    assert next(i for i in issues if i['kind'] == 'queue') == {**queued, 'kind': 'queue', 'severity': 'high'}
    assert {i['task'] for i in issues if i['kind'] == 'stalled_tasks'} == {t['id'] for t in tasks}
    assert all(t['title'] in checks['stalled_tasks']['summary'] for t in tasks)

    # Capacity stays an explained queue; bots no longer receive a no-slot alert.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='helper'")
        claimable(c, runner, 'helper')
        c.execute("UPDATE runners SET capacity=1 WHERE id=?", (runner,))
        H._write_message(c, 'human:morgan', 'bot:helper', 'one more request', cv, 'say', {}, None, None)
        job = c.execute("SELECT id FROM jobs WHERE bot='helper' ORDER BY created DESC LIMIT 1").fetchone()[0]
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) "
                  "VALUES('helper-running',?,'helper',?,1,'x','running',?,?)",
                  (job, runner, H.shift(H.now(), hours=1), H.now()))
    queue = health_of(api, headers)[1]['queue']
    assert queue['status'] == 'info' and not queue.get('items')
    issues = api.get('/api/v2/health/issues', headers=headers).json()['issues']
    assert not any(i['kind'] == 'queue' and i['reason'] == 'no_slot' for i in issues)


def test_aged_prs_use_synced_condition_age_and_clear_on_review_or_resolution(environment, monkeypatch):
    from backend import github as G
    from backend import task_relations
    api = environment()
    add_bot(api, 'builder')
    add_bot(api, 'reviewer')
    add_bot(api, 'hidden-reviewer')
    old = H.shift(H.now(), hours=-13)
    young = H.shift(H.now(), hours=-11)
    urls = [f'https://github.com/AcmeCorp/atlas/pull/{n}' for n in range(1, 6)]
    tasks = []
    with api.app.state.store.transaction() as c:
        for i, url in enumerate(urls):
            task = H.task_create(c, 'human:morgan', f'Build change {i + 1}', '', 'bot:builder', private=i == 3)
            tasks.append(task)
            H.task_link(c, 'human:morgan', task['id'], url)
            payload = {'action': 'refresh', 'pull_request': {'html_url': url, 'state': 'open',
                       'mergeable': i != 0, 'head': {'sha': str(i + 1) * 40, 'ref': f'change-{i + 1}'}}}
            with monkeypatch.context() as clock:
                clock.setattr(H, 'now', lambda: young if i == 2 else old)
                G.pull_request(c, payload)
                if i != 0:
                    G.pr_signal(c, 'status', {'sha': str(i + 1) * 40, 'state': 'success', 'context': 'tests',
                                            'repository': {'full_name': 'AcmeCorp/atlas'}})
        child = H.task_create(c, 'human:morgan', 'Review 1: PR #2', '', 'bot:reviewer', private=False)
        task_relations.set_parent(c, 'human:morgan', child['id'], tasks[1]['id'])
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (child['id'],))
        hidden = H.task_create(c, 'human:morgan', 'Review 2: PR #2', '', 'bot:hidden-reviewer', private=True)
        task_relations.set_parent(c, 'human:morgan', hidden['id'], tasks[1]['id'])
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (hidden['id'],))
        # Refreshes do not reset a wait, and a fresh check on the same head does not either.
        G.pull_request(c, {'action': 'refresh', 'pull_request': {'html_url': urls[0], 'mergeable': False, 'state': 'open', 'head': {'sha': '1' * 40}}})
        G.pr_signal(c, 'status', {'sha': '2' * 40, 'state': 'success', 'context': 'tests', 'repository': {'full_name': 'AcmeCorp/atlas'}})
        # A human-owned task is outside the bot PR check.
        c.execute("UPDATE tasks SET owner='human:morgan' WHERE id=?", (tasks[4]['id'],))
    _, checks = health_of(api)
    items = checks['aged_prs']['items']
    assert {i['pr'] for i in items} == set(urls[:2])
    assert all(i['age_minutes'] == 780 and i['fixes'] and i['task'] in {t['id'] for t in tasks[:2]} for i in items)
    assert items[0]['reason'] == 'conflict' and items[0]['whose_move'] == 'bot:builder'
    assert items[1]['reason'] == 'review' and items[1]['whose_move'] == 'bot:reviewer'
    issues = api.get('/api/v2/health/issues', headers=signed_in()).json()['issues']
    assert {i['pr'] for i in issues if i['kind'] == 'aged_prs'} == set(urls[:2])
    assert next(i for i in issues if i.get('pr') == urls[1])['whose_move'] == 'bot:reviewer'
    assert 'hidden-reviewer' not in json.dumps({'check': checks['aged_prs'],
                                               'issues': [i for i in issues if i['kind'] == 'aged_prs']})
    with api.app.state.store.transaction() as c:
        G.pull_request(c, {'action': 'refresh', 'pull_request': {'html_url': urls[0], 'mergeable': True, 'state': 'open', 'head': {'sha': '1' * 40}}})
        G.pr_signal(c, 'pull_request_review', {'action': 'submitted', 'pull_request': {'html_url': urls[1]},
                    'review': {'id': 1, 'state': 'approved', 'commit_id': '2' * 40, 'user': {'login': 'reviewer'}}})
    assert 'aged_prs' not in health_of(api)[1]


def test_a_status_the_loop_has_not_checked_for_too_long_is_a_warning_then_bad(monkeypatch):
    from types import SimpleNamespace

    from backend import health
    remote = SimpleNamespace(loopback=False)
    backup = lambda checked: {"mode": "remote", "target_kind": "s3", "last_replicated_at": H.now(), "checked_at": checked}
    check_at = lambda minutes: health._backups({"backup": backup(H.shift(H.now(), minutes=-minutes))}, remote)
    monkeypatch.delenv("TICO_BACKUP_SYNC_INTERVAL", raising=False)
    assert check_at(5)["status"] == "ok"
    stale = check_at(15)
    assert stale["status"] == "warn" and stale["summary"].startswith("Backup status not checked since ")
    assert check_at(120)["status"] == "bad"


def test_a_pile_up_is_judged_against_two_sync_intervals_of_local_files(monkeypatch):
    from types import SimpleNamespace

    from backend import health
    remote = {"mode": "remote", "target_kind": "s3", "last_replicated_at": H.now()}
    check = lambda unsent: health._backups({"backup": {**remote, "unsent_files": unsent}}, SimpleNamespace(loopback=False))
    monkeypatch.setenv("TICO_BACKUP_SYNC_INTERVAL", "1m")
    assert check(1000)["status"] == "ok" and check(1001)["status"] == "warn"
    monkeypatch.setenv("TICO_BACKUP_SYNC_INTERVAL", "1h")    # a busy database writes about 3,600 files an hour; the limit is 7,200
    assert check(3600)["status"] == "ok" and check(7200)["status"] == "ok"
    stuck = check(7201)
    assert stuck["status"] == "warn" and "7,201 unsent files" in stuck["summary"]


def test_batched_computer_load_and_overload_reads_answer_what_each_computer_read_would(environment):
    """Health and the computers list read every computer's load and overload in one pass (performance.py); each
    answer must be the one that computer's own read gives: off, revoked, calm, overloaded and recently overloaded."""
    from backend import performance
    api = environment()
    now = 1_800_000_000
    busy, off, calm, revoked = enrolled(api), "r-off", "r-calm", "r-gone"
    with api.app.state.store.transaction() as c:
        for rid in (off, calm, revoked):
            c.execute("INSERT INTO runners(id,label,operator,token_hash,created,last_seen) "
                      "SELECT ?,?,operator,?,created,last_seen FROM runners WHERE id=?", (rid, rid, "h" + rid, busy))
        c.execute("UPDATE runners SET performance='off' WHERE id=?", (off,))
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), revoked))
        for rid, cpu in ((busy, 95), (off, 95), (calm, 10), (revoked, 95)):
            for k in range(6):                       # six readings a minute apart, the newest just now
                ts = now - 60 * k
                c.execute("INSERT INTO runner_load_samples VALUES(?,?,?,?,?,?,?,?,?)",
                          (rid, ts, ts, cpu, 90, 100, 0, 0, 1))
        c.execute("INSERT INTO perf_overloads(runner_id,started,last,ended,reasons,cpu_max,mem_max,runs_max) "
                  "VALUES(?,?,?,?,'[]',99,99,1)", (busy, now - 7200, now - 3600, now - 3500))      # ended long ago
        c.execute("INSERT INTO perf_overloads(runner_id,started,last,ended,reasons,cpu_max,mem_max,runs_max) "
                  "VALUES(?,?,?,?,'[]',99,99,1)", (busy, now - 900, now - 100, now - 50))         # ended just now
        c.execute("INSERT INTO perf_overloads(runner_id,started,last,ended,reasons,cpu_max,mem_max,runs_max) "
                  "VALUES(?,?,?,NULL,'[]',99,99,1)", (calm, now - 900, now - 100))                # still open
    ids = [busy, off, calm, revoked]
    with api.app.state.store.read() as c:
        loads = performance.load_statuses(c, ids, now=now)
        overloads = performance.recent_overloads(c, ids, now=now)
        for rid in ids:
            assert loads[rid] == performance.load_status(c, rid, now=now)
            assert overloads[rid] == performance.recent_overload(c, rid, now=now)
    assert loads[busy]["overloaded"] and not loads[off]["overloaded"] and loads[off]["latest_sample"] is None
    assert not loads[calm]["overloaded"] and loads[calm]["latest_sample"]["cpu_pct"] == 10
    assert not loads[revoked]["overloaded"]
    assert overloads[busy]["ended"] == now - 50 and overloads[calm]["ended"] is None
    assert overloads[off] is None and overloads[revoked] is None
