"""Health: every check is computed from live state, and people who are not administrators see counts only."""

import pytest

from backend import onboarding, releases
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


def silent_computer(api, seconds_ago):
    """A computer enrolled for riley, holding one queued request for its bot, last heard from `seconds_ago`."""
    runner = enrolled(api)
    add_bot(api, "helper")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET operator='riley' WHERE id=?", (runner,))
        c.execute("INSERT INTO assignments(bot,runner_id,generation,updated,updated_by) VALUES('helper',?,1,?,'t')",
                  (runner, H.now()))
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) VALUES('m1','human:ana','bot:helper','chat','hi',?)",
                  (H.now(),))       # queues its job
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
    monkeypatch.setattr(views.privacy, "job_count", lambda *a, **k: 0)      # someone else's private request
    issue = machine_issue(api, person_headers(api, "riley"))
    assert issue["title"] == "Owner Mac stopped checking in" and issue["needs_person"]


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
        "id", "label", "online", "last_seen", "platform", "disk", "update"}
    assert body["failures"] == [] and set(checks) == {"computers", "waiting", "queue", "failed"}
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
        c.execute("INSERT INTO bot_config(bot,config_json,operator,onboarding_state) VALUES('support','{}','ana','needs_setup') "
                  "ON CONFLICT(bot) DO UPDATE SET onboarding_state='needs_setup'")
        task = H.task_create(c, H.KEEPER, "Ticket 42", "", "bot:support")
    assert "setup:support" not in health_of(api)[1]                 # just filed: setup may be under way
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created='2026-01-02T03:04:05Z' WHERE id=?", (task["id"],))
    check = health_of(api)[1]["setup:support"]
    assert check["status"] == "warn" and check["summary"] == "support hasn't finished setup; 1 task waiting since 2026-01-02."
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
    assert stuck["support"]["why"] == "Stuck in setup for 14 days"
    assert stuck["support"]["fix"]["finish"] == {"bot": "support"}
    quinn = as_person(api, "quinn")                 # neither an owner of it nor an administrator
    assert all(row["fix"] is None for row in health_of(api, quinn)[0]["stuck"])
    assert api.post("/api/v2/bots/support/onboarded", json={}, headers=quinn).status_code == 403
    assert api.post("/api/v2/bots/support/onboarded", json={}, headers=signed_in()).json()["onboarding_state"] == "onboarded"
    assert health_of(api)[0]["stuck"] == []


def test_a_bot_left_in_setup_is_nudged_once_to_whoever_its_setup_reminder_goes_to(environment):
    """The same person as the "Finish setting up" reminder, and nobody while that reminder is open: one ask per bot."""
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
