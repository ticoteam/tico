"""Nearby reach, enforced (docs/reach.md, backend/reach.py): who a bot in `nearby` reaches, the refusal it reads, and
the writes that are never refused. Acme is the fictional company here.

The chart: cpo reports to Ben; product-design reports to cpo; finance reports to cpo too (product-design's peer); ops
reports to product-design; doc-updater has no manager and is on the Product team with cpo and product-design. Scout
is a bot nobody is near. Cara is a person nobody here reports to. coo is the Assistant, a built-in helper."""
import json

from backend import reach, task_allocator
from backend.auth import Identity
from backend.store import H, encode
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (the api fixture)
from backend.tests.test_task_caps import as_bot


def chart(api):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM reach_settings WHERE party_id='*'")      # the fixture's open Everyone: back to default
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster["org_groups"] = {"product": {"name": "Product"}}
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
        for bot, boss, team in (("cpo", "human:ben", "product"), ("product-design", "cpo", "product"),
                                ("finance", "cpo", ""), ("ops", "product-design", ""), ("doc-updater", None, "product")):
            c.execute("UPDATE bot_config SET reports_to=?, team=?, config=json_set(config, '$.team', ?) WHERE bot=?",
                      (boss, team, team, bot))
        c.execute("INSERT INTO bots(id, slug, display_name, state) VALUES('scout', 'scout', 'Scout', 'active')")
        c.execute("INSERT INTO bot_config(bot, config, team, owner_id) VALUES('scout', '{}', '', 'ana')")
    as_bot(api, "product-design", "pd-test")
    as_bot(api, "doc-updater", "doc-test")


def ctx(api):
    with api.app.state.store.read() as c:
        return reach.context(c)


def task(api, token, owner, title="Draft the launch note", expected=200, **extra):
    r = api.post("/api/v2/tasks", json={"title": title, "body": "Scenario task.", "owner": owner, **extra},
                 headers=headers(token))
    assert r.status_code == expected, r.text
    data = r.json()
    return data.get("task", data)


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
        "bot:cpo": "manager", "bot:finance": "peer", "bot:ops": "report", "bot:doc-updater": "team"}
    assert reach.nearby(found, "bot:cpo")["human:ben"] == "manager"
    # No manager: its team and its reports only, no peers.
    assert set(reach.nearby(found, "bot:doc-updater")) == {"bot:cpo", "bot:product-design"}
    for receiver in ("bot:cpo", "bot:finance", "bot:ops", "bot:doc-updater"):
        assert reach.decide(found, "bot:product-design", receiver).allowed, receiver
    assert not reach.decide(found, "bot:product-design", "human:cara").allowed
    assert not reach.decide(found, "bot:product-design", "human:ben", "chat").allowed      # a manager's manager
    # A line adds a receiver, and its per_day still counts.
    r = api.post("/api/v2/reach/lines", json={"sender": "bot:product-design", "receiver": "human:cara", "per_day": 2},
                 headers=headers())
    assert r.status_code == 200, r.text
    found = ctx(api)
    assert reach.decide(found, "bot:product-design", "human:cara", count=lambda scope: 1).why == "line"
    over = reach.decide(found, "bot:product-design", "human:cara", count=lambda scope: 2 if scope[0] == "line" else 0)
    assert (over.allowed, over.why) == (False, "reach_line")


def test_an_out_of_reach_task_is_a_409_that_names_the_manager_and_writes_nothing(api):
    chart(api)
    refused = api.post("/api/v2/tasks", json={"title": "Straight to Cara", "body": "Scenario task.",
                                              "owner": "human:cara"}, headers=headers("pd-test"))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "reach_closed" and error["retryable"] is False
    assert error["receiver"] == "human:cara" and error["via"] == "bot:cpo"
    assert set(error["can_reach"]) == {"bot:cpo", "bot:finance", "bot:ops", "bot:doc-updater"}
    assert "Send it to cpo, who can pass it on." in error["detail"] and "Do not retry" in error["detail"]
    assert all(n in error["detail"] for n in ("cpo", "finance", "ops", "doc-updater"))      # who it does reach
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


def test_a_solicited_reply_and_work_on_a_shared_task_pass(api):
    chart(api)
    message(api, "pd-test", "human:ben", "Status?", expected=409)
    message(api, "ben-test", "product-design", "How is the launch note going?")
    message(api, "pd-test", "human:ben", "Draft is up.")                                   # Ben wrote first
    # Cara is on Ben's task as the one it waits on: product-design may hand it to her, and give her nothing else.
    shared = task(api, "ben-test", "bot:product-design", title="Launch note")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET waiting_on='human:cara' WHERE id=?", (shared["id"],))
        who = Identity("bot:product-design", "bot", agent="test")
        try:
            reach.check(c, who, "human:cara", "task")
            raise AssertionError("refused off the task")
        except reach.Refused as exc:
            assert exc.code == "reach_closed"
    task(api, "pd-test", "human:cara", title="Something new for Cara", expected=409)
    r = api.post(f"/api/v2/tasks/{shared['id']}", json={"owner": "human:cara", "version": shared["version"]},
                 headers=headers("pd-test"))
    assert r.status_code == 200, r.text


def test_a_step_flow_hand_off_a_helper_and_a_person_are_never_refused(api):
    chart(api)
    task(api, "pd-test", "bot:scout", title="To Scout", expected=409)
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Copy", [{"name": "Draft", "status": "doing"},
                                                 {"name": "Edit", "status": "review",
                                                  "flow": {"assign": {"pool": ["scout"]}}},
                                                 {"name": "Done", "status": "done"}])
    ticket = task(api, "pd-test", "bot:product-design", title="Copy ticket", type=typ["id"], step="Draft")
    r = api.post(f"/api/v2/tasks/{ticket['id']}", json={"step": "Edit", "version": ticket["version"]}, headers=headers("pd-test"))
    assert r.status_code == 200, r.text
    with api.app.state.store.transaction() as c:
        assert ticket["id"] in [t for t, _ in task_allocator.allocate(c)]
        assert H.task(c, ticket["id"])["owner_id"] == "bot:scout"
    # The Assistant (coo) and BotOps are off the chart: anyone reaches them.
    task(api, "pd-test", "bot:coo", title="For the Assistant")
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


def test_the_dry_run_replays_history_through_the_same_decision(api, capsys):
    import importlib.util
    from pathlib import Path
    chart(api)
    r = api.put("/api/v2/reach/settings/*", json={"mode": "open"}, headers=headers())
    assert r.status_code == 200, r.text
    task(api, "pd-test", "human:cara", title="Before nearby, to Cara")
    task(api, "pd-test", "human:cara", title="Before nearby, to Cara again")
    task(api, "pd-test", "bot:finance", title="Before nearby, to a peer")
    r = api.put("/api/v2/reach/settings/*", json={"mode": None}, headers=headers())
    assert r.status_code == 200, r.text
    path = Path(__file__).resolve().parents[2] / "scripts" / "reach_dry_run.py"
    spec = importlib.util.spec_from_file_location("reach_dry_run", path)
    dry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dry)
    assert dry.main(["--db", str(api.app.state.store.settings.db_path), "--days", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["refused"] == 2 and out["refused_to_people"] == 2 and out["people_hit"] == 1
    assert out["pairs"] == [{"sender": "bot:product-design", "receiver": "human:cara", "sender_name": "product-design",
                             "receiver_name": "Cara", "count": 2, "via": "bot:cpo"}]
    assert out["by_kind"] == {"task:made": 2}
