"""Nearby reach, enforced (docs/reach.md, backend/reach.py): who a bot in `nearby` reaches, the refusal it reads, and
the writes that are never refused. Acme is the fictional company here.

The chart: cpo reports to Ben; product-design reports to cpo; finance reports to cpo too (product-design's peer); ops
reports to product-design; doc-updater has no manager and is on the Product team with cpo and product-design. A bot
with no manager counts as reporting to the owner, Ana: doc-updater and Scout, which is on no team, are peers that way.
Cara is a person nobody here reports to. assistant is the Assistant, a built-in helper."""

import json
from datetime import datetime, timedelta, timezone

from backend import bot_settings as BS
from backend import reach
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.helpers.api import get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.helpers.task_caps import as_bot
import pytest


@pytest.fixture(autouse=True)
def nearby_default(monkeypatch):
    """conftest.py starts other tests' bots open; these test the shipped default."""
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)


def chart(api):
    with api.app.state.store.transaction() as c:
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster["org_groups"] = {"product": {"name": "Product"}}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
        for bot, boss, team in (
            ("cpo", "human:ben", "product"),
            ("product-design", "cpo", "product"),
            ("finance", "cpo", ""),
            ("ops", "product-design", ""),
            ("doc-updater", None, "product"),
        ):
            c.execute("UPDATE bot_config SET reports_to=?, team=? WHERE bot=?", (boss, team, bot))
            BS.update(c, bot, {"team": team})
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('scout', 'scout', 'Scout', 'active')")
        c.execute("INSERT INTO bot_config(bot, team, owner_id) VALUES('scout', '', 'ana')")
    as_bot(api, "product-design", "pd-test")
    as_bot(api, "doc-updater", "doc-test")


def ctx(api):
    with api.app.state.store.read() as c:
        return reach.context(c)


def task(api, token, owner, title="Draft the launch note", expected=200, **extra):
    r = api.post(
        "/api/v2/tasks",
        json={"title": title, "body": "Scenario task.", "owner": owner, **extra},
        headers=headers(token),
    )
    assert r.status_code == expected, r.text
    data = r.json()
    return data.get("task", data)


def wait_on_cara(c, task_id):
    """Ben, who handed the task over, asks Cara on it: she is one it waits on, and so one it is shared with."""
    H.task_ask(
        c,
        "human:ben",
        task_id,
        "human:cara",
        "Can you check the launch note before it goes out?",
        seen_version=H.task(c, task_id)["version"],
    )


def message(api, token, to, text, expected=200, **extra):
    r = api.post("/api/v2/messages", json={"to": to, "text": text, **extra}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def titled(api, title):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM tasks WHERE title=?", (title,)).fetchone()[0]


def test_nearby_is_the_manager_reports_peers_and_team_and_a_line_adds_one_with_its_per_day(api):
    chart(api)
    found = ctx(api)
    assert reach.effective(found, "bot:product-design")["mode"] == "nearby"
    assert reach.effective(found, "human:cara")["mode"] == "open"
    assert reach.nearby(found, "bot:product-design") == {
        "bot:cpo": "manager",
        "bot:finance": "peer",
        "bot:ops": "report",
        "bot:doc-updater": "team",
    }
    assert reach.nearby(found, "bot:cpo")["human:ben"] == "manager"
    # No manager: the owner (Ana) stands in, so the other bots with no manager are its peers.
    near = reach.nearby(found, "bot:doc-updater")
    assert (near["human:ana"], near["bot:scout"], near["bot:cpo"], near["bot:product-design"]) == (
        "manager",
        "peer",
        "team",
        "team",
    )
    for receiver in ("bot:cpo", "bot:finance", "bot:ops", "bot:doc-updater"):
        assert reach.decide(found, "bot:product-design", receiver).allowed, receiver
    assert not reach.decide(found, "bot:product-design", "human:cara").allowed
    assert not reach.decide(found, "bot:product-design", "human:ben", "chat").allowed  # a manager's manager
    # A line adds a receiver, and its per_day still counts.
    r = api.post(
        "/api/v2/reach/lines",
        json={"sender": "bot:product-design", "receiver": "human:cara", "per_day": 2},
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    found = ctx(api)
    assert reach.decide(found, "bot:product-design", "human:cara", count=lambda scope: 1).why == "line"
    over = reach.decide(found, "bot:product-design", "human:cara", count=lambda scope: 2 if scope[0] == "line" else 0)
    assert (over.allowed, over.why) == (False, "reach_line")


def test_an_out_of_reach_task_is_a_409_that_names_the_manager_and_writes_nothing(api):
    chart(api)
    refused = api.post(
        "/api/v2/tasks",
        json={"title": "Straight to Cara", "body": "Scenario task.", "owner": "human:cara"},
        headers=headers("pd-test"),
    )
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "reach_closed" and error["retryable"] is False
    assert error["receiver"] == "human:cara" and error["via"] == "bot:cpo"
    assert set(error["can_reach"]) == {"bot:cpo", "bot:finance", "bot:ops", "bot:doc-updater"}
    assert "Send it to cpo, who can pass it on." in error["detail"] and "Do not retry" in error["detail"]
    assert all(n in error["detail"] for n in ("cpo", "finance", "ops", "doc-updater"))  # who it does reach
    assert titled(api, "Straight to Cara") == 0
    task(api, "pd-test", "bot:finance", title="To a peer")
    # A chat out of reach is refused the same way, before anything is delivered.
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM messages WHERE sender_id='bot:product-design'").fetchone()[0]
    sent = message(api, "pd-test", "human:cara", "Can you look at this?", expected=409)
    assert sent["error"]["code"] == "reach_closed"
    dm = api.post("/api/v2/threads/dm", json={"members": ["human:cara"], "body": "Hi Cara"}, headers=headers("pd-test"))
    assert dm.status_code == 409 and dm.json()["error"]["code"] == "reach_closed", dm.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE sender_id='bot:product-design'").fetchone()[0] == before
    dm = api.post("/api/v2/threads/dm", json={"members": ["bot:finance"], "body": "Hi"}, headers=headers("pd-test"))
    assert dm.status_code == 200, dm.text


def test_the_owner_is_reached_only_by_a_bot_that_reports_to_them(api):
    chart(api)
    task(api, "pd-test", "human:ana", title="For the owner", expected=409)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ana' WHERE bot='product-design'")
    task(api, "pd-test", "human:ana", title="For the owner")


def test_a_bot_with_no_manager_reports_to_the_owner(api):
    chart(api)
    # It reaches the owner and the other bots with no manager, never no one.
    task(api, "doc-test", "human:ana", title="For the owner")
    message(api, "doc-test", "bot:scout", "Can you take this?")
    error = refused_code(
        api.post(
            "/api/v2/tasks",
            json={"title": "To Cara", "body": "Scenario task.", "owner": "human:cara"},
            headers=headers("doc-test"),
        )
    )
    assert error["code"] == "reach_closed" and error["via"] == "human:ana"
    assert "reaches only the owner (it has no manager on the chart)" in error["detail"]
    assert "Send it to Ana, who can pass it on." in error["detail"] and "reaches no one" not in error["detail"]
    assert {"human:ana", "bot:scout"} <= set(error["can_reach"])
    # Given a manager, the owner is no longer its stand-in.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='doc-updater'")
    task(api, "doc-test", "human:ana", title="For the owner again", expected=409)


def test_a_solicited_reply_and_work_on_a_shared_task_pass(api):
    chart(api)
    message(api, "pd-test", "human:ben", "Status?", expected=409)
    asked = message(api, "ben-test", "product-design", "How is the launch note going?")
    message(api, "pd-test", "human:ben", "Draft is up.", in_reply_to=asked["id"])  # a direct reply to Ben's message
    # Cara is on Ben's task as one it waits on; that does not open her to product-design off the task.
    shared = task(api, "ben-test", "bot:product-design", title="Launch note")
    with api.app.state.store.transaction() as c:
        wait_on_cara(c, shared["id"])
        who = Identity("bot:product-design", "bot", agent="test")
        try:
            reach.check(c, who, "human:cara", "task")
            raise AssertionError("refused off the task")
        except reach.Refused as exc:
            assert exc.code == "reach_closed"
    task(api, "pd-test", "human:cara", title="Something new for Cara", expected=409)
    # Waiting on Cara no longer opens her to a bot that is not next to her: it takes it to its manager.
    r = api.post(
        f"/api/v2/tasks/{shared['id']}/owner",
        json={"owner": "human:cara", "version": shared["version"]},
        headers=headers("pd-test"),
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "reach_closed", r.text
    # Ben handed it the task, so a hand-back to Ben passes.
    r = api.post(
        f"/api/v2/tasks/{shared['id']}/owner",
        json={"owner": "human:ben", "version": shared["version"]},
        headers=headers("pd-test"),
    )
    assert r.status_code == 200, r.text


def test_a_stage_hand_off_a_helper_and_a_person_are_never_refused(api):
    from backend.tests.helpers import engine as X

    chart(api)
    task(api, "pd-test", "bot:scout", title="To Scout", expected=409)
    with api.app.state.store.transaction() as c:
        X.pipeline(
            c,
            "copy",
            "Copy",
            (
                {"name": "Draft", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
                {"name": "Edit", "owner": "bot:scout", "requirements": [{"kind": "owner_done"}]},
            ),
        )
    copy = task(api, "pd-test", "bot:product-design", title="Copy task", pipeline="copy")
    # The pipeline's next stage is Scout's: the engine hands it on, whoever moved it.
    r = api.post(f"/api/v2/tasks/{copy['id']}/done", json={"version": copy["version"]}, headers=headers("pd-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        assert (H.task(c, copy["id"])["stage_id"], H.task(c, copy["id"])["owner_id"]) == ("copy-edit", "bot:scout")
    # The Assistant (assistant) and BotOps are off the chart: anyone reaches them.
    task(api, "pd-test", "bot:assistant", title="For the Assistant")
    assert reach.decide(ctx(api), "bot:product-design", "bot:botops").why == "helper"
    # A person is never limited, even writing to a bot nowhere near them.
    task(api, "ben-test", "bot:scout", title="Ben to Scout")


def test_everyone_open_restores_reach_to_anyone(api):
    chart(api)
    task(api, "doc-test", "human:cara", title="Off the chart", expected=409)
    r = api.put("/api/v2/reach/settings/*", json={"mode": "open"}, headers=headers())
    assert r.status_code == 200, r.text
    task(api, "doc-test", "human:cara", title="Off the chart")
    # A bot set back to nearby under an open Everyone is nearby again.
    r = api.put("/api/v2/reach/settings/bot:doc-updater", json={"mode": "nearby"}, headers=headers())
    assert r.status_code == 200, r.text
    task(api, "doc-test", "human:cara", title="Off the chart again", expected=409)
    assert get(api, "reach")["defaults"] == {**reach.DEFAULTS, "person_mode": "open"}


def dry_run(api, capsys):
    """scripts/reach_dry_run.py over the last day of `api`'s database, as its JSON."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "reach_dry_run.py"
    spec = importlib.util.spec_from_file_location("reach_dry_run", path)
    dry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dry)
    assert dry.main(["--db", str(api.app.state.store.settings.db_path), "--days", "1", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_the_dry_run_replays_history_through_the_same_decision(api, capsys):
    chart(api)
    r = api.put("/api/v2/reach/settings/*", json={"mode": "open"}, headers=headers())
    assert r.status_code == 200, r.text
    task(api, "pd-test", "human:cara", title="Before nearby, to Cara")
    task(api, "pd-test", "human:cara", title="Before nearby, to Cara again")
    task(api, "pd-test", "bot:finance", title="Before nearby, to a peer")
    r = api.put("/api/v2/reach/settings/*", json={"mode": None}, headers=headers())
    assert r.status_code == 200, r.text
    out = dry_run(api, capsys)
    assert out["refused"] == 2 and out["refused_to_people"] == 2 and out["people_hit"] == 1
    assert out["pairs"] == [
        {
            "sender": "bot:product-design",
            "receiver": "human:cara",
            "sender_name": "product-design",
            "receiver_name": "Cara",
            "count": 2,
            "via": "bot:cpo",
        }
    ]
    assert out["by_kind"] == {"task:made": 2}


def test_a_task_made_in_a_slow_write_is_still_read_as_made(api, capsys, monkeypatch):
    """A new task's send is logged in the write that makes it, after everything else that write does. On a loaded box
    that took 2 s or more once, and the dry run, which matched the send to the task by the clock, read the task as
    handed on (merge queue run 42484614049). The send carries the task's own creation time."""
    import time_machine

    chart(api)
    r = api.put("/api/v2/reach/settings/*", json={"mode": "open"}, headers=headers())
    assert r.status_code == 200, r.text
    create = H.task_create
    with time_machine.travel(datetime.now(timezone.utc), tick=True) as clock:

        def slow(*args, **kwargs):
            row = create(*args, **kwargs)
            clock.shift(timedelta(seconds=3))  # the rest of the write takes 3 s
            return row

        monkeypatch.setattr(H, "task_create", slow)
        made = task(api, "pd-test", "human:cara", title="Filed on a slow day")
        monkeypatch.setattr(H, "task_create", create)
    with api.app.state.store.read() as c:
        sent = c.execute("SELECT sent_at FROM task_cap_log WHERE task_id=?", (made["id"],)).fetchone()[0]
        assert sent == H.task(c, made["id"])["created_at"]
    r = api.put("/api/v2/reach/settings/*", json={"mode": None}, headers=headers())
    assert r.status_code == 200, r.text
    assert dry_run(api, capsys)["by_kind"] == {"task:made": 1}


def refused_code(r):
    assert r.status_code == 409, r.text
    return r.json()["error"]


def test_a_refusal_never_names_a_teammate_the_bot_may_not_see(api):
    from backend.tests.helpers.api import restrict

    chart(api)
    with api.app.state.store.transaction() as c:
        restrict(c, "cpo", people=["ana"])  # product-design may no longer See its own manager
    error = refused_code(
        api.post(
            "/api/v2/tasks",
            json={"title": "To Cara", "body": "Scenario task.", "owner": "human:cara"},
            headers=headers("pd-test"),
        )
    )
    assert error["via"] is None and "bot:cpo" not in error["can_reach"]
    assert "cpo" not in error["detail"] and "finance" in error["detail"]


def members_thread(api, members, body=None):
    r = api.post(
        "/api/v2/threads",
        json={"title": "Numbers", "audience": "members", "members": members, **({"body": body} if body else {})},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_dm_a_person_brought_the_bot_into_is_solicited_and_a_legacy_message_checks_every_recipient(api):
    chart(api)
    # Ben opens a DM with product-design and Cara: product-design may answer there, Cara included.
    dm = api.post(
        "/api/v2/threads/dm",
        json={"members": ["bot:product-design", "human:cara"], "body": "Plan?", "title": "Plan"},
        headers=headers("ben-test"),
    )
    assert dm.status_code == 200, dm.text
    # A person wrote in this DM, so the bot may answer every person in it, Cara included.
    r = api.post(
        f"/api/v2/threads/{dm.json()['id']}/messages", json={"text": "Here is the plan."}, headers=headers("pd-test")
    )
    assert r.status_code == 200, r.text
    # Ben writing to the bot in a DIFFERENT conversation does not open this one for Cara.
    other = api.post(
        "/api/v2/threads/dm",
        json={"members": ["human:cara"], "body": "A new one", "title": "Quiet"},
        headers=headers("pd-test"),
    )
    assert other.status_code == 409 and other.json()["error"]["receiver"] == "human:cara", other.text
    own = api.post(
        "/api/v2/threads/dm",
        json={"members": ["bot:product-design"], "body": "Plan?", "title": "Plan"},
        headers=headers("ben-test"),
    )
    assert own.status_code == 200, own.text
    r = api.post(
        f"/api/v2/threads/{own.json()['id']}/messages", json={"text": "Here is the plan."}, headers=headers("pd-test")
    )
    assert r.status_code == 200, r.text
    # A legacy message to a peer that @mentions Cara where she reads it is refused whole: the mention reaches her.
    tid = members_thread(api, ["bot:finance", "human:cara", "bot:product-design"])
    with api.app.state.store.read() as c:
        before = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    r = api.post(
        "/api/v2/messages",
        json={"to": "bot:finance", "text": "@cara can you check this?", "conversation_id": tid},
        headers=headers("pd-test"),
    )
    assert r.status_code == 409 and r.json()["error"]["receiver"] == "human:cara", r.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == before


def test_an_edit_that_mentions_someone_new_out_of_reach_is_refused(api):
    chart(api)
    tid = members_thread(api, ["bot:finance", "human:cara", "bot:product-design"])
    posted = api.post(f"/api/v2/threads/{tid}/messages", json={"text": "@finance numbers?"}, headers=headers("pd-test"))
    assert posted.status_code == 200, posted.text
    message_id = posted.json()["id"]
    error = refused_code(
        api.patch(
            f"/api/v2/messages/{message_id}", json={"text": "@finance @cara numbers?"}, headers=headers("pd-test")
        )
    )
    assert error["code"] == "reach_closed" and error["receiver"] == "human:cara"
    with api.app.state.store.read() as c:
        assert H.message(c, message_id)["body"] == "@finance numbers?"


def test_a_sub_thread_reply_to_the_receivers_message_is_solicited(api):
    chart(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO conversations(id, kind, subject) VALUES('t-top', 'thread', 'Top')")
        c.execute(
            "INSERT INTO messages(id, conversation_id, sender_id, body, created_at) VALUES"
            "('m-cara', 't-top', 'human:cara', 'Who can help?', ?)",
            (H.now(),),
        )
        # check_post passes the sub-thread and the thread above it, and the message the sub-thread hangs from.
        assert reach.solicited(c, "bot:product-design", "human:cara", ["t-sub", "t-top"], [None, "m-cara"])
        assert not reach.solicited(c, "bot:product-design", "human:cara", "t-sub")
        assert reach.person_asked(c, "bot:product-design", "human:cara", None, ["t-sub", "t-top"], [None, "m-cara"])


def test_a_message_in_a_tasks_thread_uses_the_task_and_a_send_anyway_replay_is_not_rechecked(api):
    chart(api)
    shared = task(api, "ben-test", "bot:product-design", title="Launch note")
    with api.app.state.store.transaction() as c:
        wait_on_cara(c, shared["id"])
        conversation = H.task(c, shared["id"])["conversation_id"]
        who = Identity("bot:product-design", "bot", agent="test")
        reach.check(c, who, "human:ben", "chat", conversation_id=conversation)  # the thread's own task, Ben handed it
        try:
            reach.check(c, who, "human:cara", "chat", conversation_id=conversation)  # a thread member is not enough
            raise AssertionError("refused: Cara did not hand it over")
        except reach.Refused:
            pass
        replay = Identity("bot:product-design", "bot", agent=reach.REPLAY_AGENT)
        reach.check(c, replay, "human:cara", "task")  # a person sent it anyway
        try:
            reach.check(c, who, "human:cara", "task")
            raise AssertionError("refused off the task")
        except reach.Refused:
            pass


def test_the_built_in_helpers_are_reached_and_the_health_monitor_is_not_exempt(api):
    chart(api)
    found = ctx(api)
    for slug in ("botops", "rules-owner", "finance-owner", "general-manager"):
        assert reach.decide(found, "bot:product-design", "bot:" + slug).why == "helper", slug
    assert not reach.decide(found, "bot:health-monitor", "human:cara").allowed


def test_an_edit_of_a_message_not_the_bots_own_is_the_edits_own_answer_not_a_refusal(api):
    chart(api)
    with api.app.state.store.transaction() as c:
        for tid, audience in (("t-cara-home", "home"), ("t-cara-own", "personal")):
            c.execute(
                "INSERT INTO conversations(id, kind, subject, audience, owner_id) VALUES(?, 'thread', 'Home', ?, "
                "'human:cara')",
                (tid, audience),
            )
            c.execute(
                "INSERT INTO messages(id, conversation_id, sender_id, body, created_at) VALUES"
                "(?, ?, 'human:cara', 'Note to self', ?)",
                ("m-" + tid, tid, H.now()),
            )
    # Cara's Home message: 403 (not its author), never a 409 that names whose Home it is.
    r = api.patch("/api/v2/messages/m-t-cara-home", json={"text": "@finance numbers?"}, headers=headers("pd-test"))
    assert r.status_code == 403 and "Cara" not in r.text, r.text
    # One it cannot read: 404, as if it were not there.
    r = api.patch("/api/v2/messages/m-t-cara-own", json={"text": "@finance numbers?"}, headers=headers("pd-test"))
    assert r.status_code == 404 and "Cara" not in r.text, r.text
    with api.app.state.store.read() as c:
        assert H.message(c, "m-t-cara-home")["body"] == H.message(c, "m-t-cara-own")["body"] == "Note to self"


def test_a_cap_of_one_allows_one_legacy_message(api):
    chart(api)
    r = api.put("/api/v2/reach/settings/bot:product-design", json={"chats_per_day": 1}, headers=headers())
    assert r.status_code == 200, r.text
    message(api, "pd-test", "bot:finance", "First numbers.")
    assert message(api, "pd-test", "bot:finance", "More numbers.", expected=409)["error"]["code"] == "reach_cap"
    r = api.put("/api/v2/reach/settings/bot:product-design", json={"chats_per_day": None}, headers=headers())
    assert r.status_code == 200, r.text
    # A line that allows one allows one.
    r = api.post(
        "/api/v2/reach/lines",
        json={"sender": "bot:product-design", "receiver": "human:cara", "per_day": 1},
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    message(api, "pd-test", "human:cara", "Can you look at this?")
    assert message(api, "pd-test", "human:cara", "And this?", expected=409)["error"]["code"] == "reach_line"


def test_a_message_task_ref_sent_as_a_list_is_checked_on_its_first_task_and_junk_is_a_422(api):
    """Bots send `refs.task` as a list (clients/hubtools.py `_refs`). The list names its first task, as the rest of the
    code reads a message's task (hubdb.refs_task_id): reach judges that one, a shared task passes and an unshared one is
    a 409 that writes nothing. A ref that is neither a task id nor a list of them is a 422, never a 500."""
    chart(api)
    shared = task(api, "ben-test", "bot:product-design", title="Launch note")
    other = task(api, "ben-test", "bot:finance", title="Budget")  # Ben did not hand it to product-design
    message(api, "pd-test", "human:ben", "On the launch note.", refs={"task": [shared["id"]]})
    refused = message(api, "pd-test", "human:ben", "On the budget.", expected=409, refs={"task": [other["id"]]})
    assert refused["error"]["code"] == "reach_closed"
    message(api, "pd-test", "human:ben", "Both.", refs={"task": [shared["id"], other["id"]]})
    refused = message(
        api, "pd-test", "human:ben", "Budget first.", expected=409, refs={"task": [other["id"], shared["id"]]}
    )
    assert refused["error"]["code"] == "reach_closed"
    for ref in ({"id": shared["id"]}, [[shared["id"]]]):
        junk = message(api, "pd-test", "bot:finance", "Bad ref.", expected=422, refs={"task": ref})
        assert junk["error"]["code"] == "reference"


def test_a_bots_task_comment_is_checked_against_its_reach_as_a_thread_post_is(api):
    chart(api)
    shared = task(api, "ana-test", "bot:product-design", title="Launch plan")
    thread = get(api, "tasks/" + shared["id"])["task"]["conversation_id"]
    text = "@cara can you check the launch dates?"
    said = api.post(f"/api/v2/tasks/{shared['id']}/comments", json={"text": text}, headers=headers("pd-test"))
    assert said.status_code == 409 and said.json()["error"]["code"] == "reach_closed", said.text
    posted = api.post(f"/api/v2/threads/{thread}/messages", json={"text": text}, headers=headers("pd-test"))
    assert posted.status_code == 409 and posted.json()["error"]["code"] == "reach_closed", posted.text
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE body=?", (text,)).fetchone()[0] == 0
    fine = api.post(
        f"/api/v2/tasks/{shared['id']}/comments",
        json={"text": "@finance can you check the budget?"},
        headers=headers("pd-test"),
    )
    assert fine.status_code == 200, fine.text


def two_levels_down(api):
    """ops reports to product-design, which reports to cpo, who reports to Ben: ops is two levels below cpo's manager."""
    chart(api)
    as_bot(api, "ops", "ops-test")


def test_a_bot_two_levels_down_is_refused_a_person_whatever_it_shares_with_them(api):
    two_levels_down(api)
    # Ben requested a task in ops's tree, is a reader of it, and chatted with ops in another conversation.
    child = task(api, "pd-test", "bot:ops", title="Child of Ben's task", private=True)
    with api.app.state.store.transaction() as c:  # set as data: product-design itself may not name Ben
        c.execute("UPDATE tasks SET readers=? WHERE id=?", ('["human:ben"]', child["id"]))
    asked = message(api, "ben-test", "ops", "Hello ops")
    other = api.post(
        "/api/v2/threads",
        json={"title": "Other", "audience": "members", "members": ["bot:ops"]},
        headers=headers("ben-test"),
    )
    assert other.status_code == 200, other.text
    with pytest.raises(reach.Refused) as asked_ben:  # an ask of Ben on the task is a write to him, and refused
        with api.app.state.store.transaction() as c:
            H.task_ask(c, "bot:ops", child["id"], "human:ben", "Question for Ben?", seen_version=child["version"])
    assert asked_ben.value.code == "reach_closed"
    for title in ("Straight to Ben",):
        task(api, "ops-test", "human:ben", title=title, expected=409)
    refused = message(api, "ops-test", "human:ben", "A question.", expected=409, refs={"task": [child["id"]]})
    assert refused["error"]["code"] == "reach_closed" and refused["error"]["via"] == "bot:product-design"
    assert "Send it to product-design, who can pass it on." in refused["error"]["detail"]
    # Handing Ben the task is checked the same way.
    r = api.post(
        f"/api/v2/tasks/{child['id']}/owner",
        json={"owner": "human:ben", "version": child["version"]},
        headers=headers("ops-test"),
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "reach_closed", r.text
    # Bot to bot is as before.
    task(api, "ops-test", "bot:product-design", title="To its manager")
    assert asked["id"]


def test_a_bot_two_levels_down_may_answer_a_person_who_came_to_it_or_handed_it_the_task(api):
    two_levels_down(api)
    asked = message(api, "ben-test", "ops", "Status on the note?")
    message(api, "ops-test", "human:ben", "Done.", in_reply_to=asked["id"])  # a direct reply
    with api.app.state.store.read() as c:
        room = c.execute("SELECT conversation_id FROM messages WHERE id=?", (asked["id"],)).fetchone()[0]
    message(api, "ops-test", "human:ben", "One more thing.", conversation_id=room)  # the conversation Ben opened
    handed = task(api, "ben-test", "bot:ops", title="Ben's own task for ops")
    message(api, "ops-test", "human:ben", "A question on it.", refs={"task": [handed["id"]]})
    # Ben handed it the task, so a message to Ben on it passes; a new task for Ben does not.
    task(api, "ops-test", "human:ben", title="Unrelated", expected=409)


def test_a_direct_report_reaches_the_owner_and_other_people_stay_closed(api):
    chart(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ana' WHERE bot='product-design'")
    task(api, "pd-test", "human:ana", title="From a direct report")
    message(api, "pd-test", "human:ana", "Hello owner.")
    task(api, "pd-test", "human:cara", title="To Cara", expected=409)
