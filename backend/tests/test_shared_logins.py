"""Codex bots sharing a ChatGPT login run concurrently. Optional queue support stays covered with a synthetic opt-in."""
import uuid

import pytest

from backend.tests.test_api import api, as_member, assign, get, post, restrict, runner  # noqa: F401


@pytest.fixture
def queued_login(monkeypatch):
    """Exercise dormant queue support without enabling it in the built-in Codex manifest."""
    from clients.harness_manifests import caps
    monkeypatch.setitem(caps("codex"), "one_turn_per_login", True)


def test_codex_bots_on_one_chatgpt_login_do_not_show_a_take_turns_warning(api):
    r = runner(api)
    for bot in ("ops", "finance"):
        assign(api, r, bot)
    for profile in ("", "acme"):
        beat(api, r, {"ops": profile, "finance": profile})
        assert shared(api, r) == []
        mine = next(m for m in get(api, "computers")["computers"] if m["id"] == r["runner_id"])
        assert mine["shared_logins"] == []


def beat(api, r, bots, detail="Signed in with ChatGPT", acme="Signed in with ChatGPT"):
    """A heartbeat where each of `bots` ({slug: profile}) runs Codex; the computer's own sign-in and the acme
    subscription read `detail` and `acme`."""
    logins = {"": {"installed": True, "authenticated": "ready", "detail": detail},
              "acme": {"installed": True, "authenticated": "ready", "detail": acme}}
    readiness = {"schema_version": 1,
                 "runtimes": {"codex": {"installed": True, "authenticated": "ready", "detail": detail, "profiles": logins}},
                 "bots": {bot: {"ready": True, "runtime": "codex", "profile": profile, "sign_in": "ready"}
                          for bot, profile in bots.items()}}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": readiness}, token=r["token"],
         key=uuid.uuid4().hex)


def shared(api, r, token="ana-test"):
    return next(m for m in get(api, "operations", token)["computers"] if m["id"] == r["runner_id"])["shared_logins"]


def test_an_opt_in_queue_lists_shared_logins_but_not_an_api_key_or_a_login_of_ones_own(api, queued_login):
    r = runner(api)
    for bot in ("ops", "finance", "inbox"):
        assign(api, r, bot)
    beat(api, r, {"ops": "", "finance": "", "inbox": "acme"})
    assert shared(api, r) == [{"runtime": "codex", "profile": "", "display_name": "", "count": 2, "bots": ["finance", "ops"]}]
    # The same list on the member-facing computers page.
    mine = next(m for m in get(api, "computers")["computers"] if m["id"] == r["runner_id"])
    assert mine["shared_logins"] == shared(api, r)

    beat(api, r, {"ops": "acme", "finance": "", "inbox": "acme"})
    assert shared(api, r) == [{"runtime": "codex", "profile": "acme", "display_name": "acme", "count": 2, "bots": ["inbox", "ops"]}]
    # A reader who may not see a bot learns how many share the sign-in, not who.
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    beat(api, r, {"ops": "", "finance": "", "inbox": "acme"})
    theirs = next((m for m in get(api, "computers", "ben-test")["computers"] if m["id"] == r["runner_id"]), None)
    assert theirs["shared_logins"] == [
        {"runtime": "codex", "profile": "", "display_name": "", "count": 2, "bots": ["finance"]}]


def test_a_key_login_or_one_whose_kind_is_unknown_is_not_listed_as_shared(api, queued_login):
    # Bots on a key run side by side; a login that does not say it is ChatGPT is not claimed to take turns.
    r = runner(api)
    for bot in ("ops", "finance"):
        assign(api, r, bot)
    for detail in ("Signed in with an API key", "Signed in with ChatGPT using API key", "Signed in", "Ready", ""):
        beat(api, r, {"ops": "", "finance": ""}, detail=detail)
        assert shared(api, r) == [], detail
    beat(api, r, {"ops": "", "finance": ""})
    assert shared(api, r)[0]["count"] == 2



def test_a_turns_login_class_puts_release_blockers_first_then_reviews_and_a_persons_chat_and_watches_last(tmp_path):
    from backend.execution import login_class
    from backend.store import H
    from backend.tests.test_hubdb import ANA, CMO, EMPLOYEES, PEOPLE
    c = H.connect(tmp_path / "hub.db")
    H.sync_registry(c, EMPLOYEES, PEOPLE)
    make = lambda title, **kw: H.task(c, H.task_create(c, ANA, title, "Check it.", CMO, lint=False, **kw)["id"])  # noqa: E731
    person, bot = {"from_actor": "human:ana"}, {"from_actor": "bot:lead"}
    release, critical, plain = make("Release v1", labels=["release-blocker"]), make("PR 7", labels=["critical-path"]), make("Chore")
    assert login_class(c, release, bot, None) == "release"
    assert login_class(c, critical, bot, None) == "review"
    assert login_class(c, None, person, None) == "review"
    assert login_class(c, plain, bot, None) == "other"
    # An unlabelled step subtask takes the class of the work it serves.
    assert login_class(c, make("QA: v1", parent_id=release["id"]), bot, None) == "release"
    assert login_class(c, make("Review 1: PR 7", parent_id=critical["id"]), bot, None) == "review"
    # A standing watch never jumps the queue, labelled or not.
    assert login_class(c, release, {"from_actor": "keeper", "refs": {"wake": "next-run"}}, None) == "other"
    assert login_class(c, release, person, {"id": "r1"}) == "other"


def test_a_turn_that_gave_its_login_up_at_the_cap_goes_back_in_the_queue_unanswered_and_not_held_for_review(api):
    from backend.tests.test_api import claim, setup_attempt
    r, origin, a = setup_attempt(api)
    # The lease names the turn's class: the runner yields only for a server that takes the turn back.
    assert a["login_class"] == "review"
    post(api, f"attempts/{a['id']}/started", {"thread_id": "thread"}, r["token"])
    line = "Paused after 20 min so the next turn could use this computer's ChatGPT login; resumes next"
    post(api, f"attempts/{a['id']}/events", {"events": [{"seq": 1, "kind": "status", "payload": {"yielded": line}}]},
         r["token"])
    post(api, f"attempts/{a['id']}/complete", {"outcome": "interrupted", "yielded": True, "last_seq": 1,
                                               "text": "Pushed my work in progress; next: the summary table."}, r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (origin["id"],)).fetchone()[0] == "queued"
        assert not c.execute("SELECT 1 FROM messages WHERE in_reply_to=?", (origin["id"],)).fetchone()
    # The bot is idle with the pause line until its continuation starts, not crashed, and Health raises nothing.
    [status] = [s for s in get(api, "status")["bots"] if s and s["bot"] == a["bot"]]
    assert (status["state"], status["focus"]) == ("idle", line)
    assert not [i for i in get(api, "operations")["issues"] if i.get("bot") == a["bot"]]
    # It continues on the next claim, as the same work.
    again = claim(api, r)
    assert again["job_id"] == a["job_id"] and again["id"] != a["id"]


def test_a_turn_waiting_for_its_login_whose_task_is_closed_meanwhile_is_skipped_cleanly(api, queued_login):
    from backend.tests.test_api import claim, ready
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    tid = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})["id"]
    a = claim(api, r)
    assert "stop" not in post(api, f"attempts/{a['id']}/renew", {}, r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed',closed_at=? WHERE id=?", ("9999-01-01T00:00:00Z", tid))
    # Only a turn waiting for its login is stopped: one still preparing runs on, as before there was a queue.
    assert "stop" not in post(api, f"attempts/{a['id']}/renew", {}, r["token"])
    wait = {"waiting": "Waiting for this computer's ChatGPT login to be free", "login": "this computer's ChatGPT login",
            "holder": "finance", "position": 1}
    post(api, f"attempts/{a['id']}/events", {"events": [{"seq": 1, "kind": "status", "payload": wait}]}, r["token"])
    # The runner hears stop on its next renewal, leaves the queue and reports the turn it never started.
    assert post(api, f"attempts/{a['id']}/renew", {}, r["token"])["stop"] is True
    post(api, f"attempts/{a['id']}/complete", {"outcome": "interrupted", "last_seq": 1}, r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0] == "canceled"
        assert not c.execute("SELECT 1 FROM messages WHERE kind='notice' AND body LIKE 'Stopped by%'").fetchone()
    assert claim(api, r) is None
    # A person's own comment waiting behind the login is never skipped: their words still get an answer.
    tid = post(api, "tasks", {"owner": "ops", "title": "Count the lines", "body": "Count them."})["id"]
    post(api, f"tasks/{tid}/comments", {"text": "@ops count the blank ones too"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued' AND message_id IN "
                  "(SELECT id FROM messages WHERE sender_id NOT LIKE 'human:%')")
    b = claim(api, r)
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT m.sender_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.id=?",
                         (b["job_id"],)).fetchone()[0].startswith("human:")
        c.execute("UPDATE tasks SET status='closed',closed_at=? WHERE id=?", ("9999-01-01T00:00:00Z", tid))
    post(api, f"attempts/{b['id']}/events", {"events": [{"seq": 1, "kind": "status", "payload": wait}]}, r["token"])
    assert "stop" not in post(api, f"attempts/{b['id']}/renew", {}, r["token"])


def test_a_person_stopping_a_turn_before_it_started_ends_it_unstarted(api):
    from backend.tests.test_api import claim, ready
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    a = claim(api, r)
    post(api, "bots/ops/stop", {"run_id": a["id"]})
    assert post(api, f"attempts/{a['id']}/renew", {}, r["token"])["stop"] is True
    # The runner stops before it acknowledges the start (waiting for its login, or preparing): nothing ran.
    post(api, f"attempts/{a['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0] == "canceled"
        assert c.execute("SELECT 1 FROM messages WHERE kind='notice' AND body='Stopped by Ana'").fetchone()
