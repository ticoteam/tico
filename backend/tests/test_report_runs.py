"""A bot reads the runs of the bots below it on the org chart (`Auth.run_conversation`): their routine, task and
wake-up runs, with cost and steps. A person's chat with a report stays theirs, and peers and reports see nothing."""
from backend import rooms
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401


def bot(api, slug, reports_to=None):
    post(api, "bots", {"slug": slug, "display_name": slug.title(), "description": "A test bot.",
                       "reports_to": reports_to, "status": "active", "repo": "emp-" + slug, "thread_mode": "personal",
                       "model": "gpt-6.1-sol", "effort": "high", "harness": None,
                       "operator": "ana", "owners": ["ana"], "runner_id": None})


def finish(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["id"]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "text": "Done.",
          "usage": {"model": "gpt-6.1-sol", "runtime": "codex", "harness": "codex", "billing": "api",
                    "input_tokens": 100, "output_tokens": 20}}, machine["token"])
    return attempt["id"]


def routine_run(api, machine, slug):
    """One run of an automation (what a routine became) that gives `slug` a turn: a task of its own."""
    aid = post(api, "automations", {"name": "Sweep", "owner": "person:ana", "trigger": {"type": "event", "name": "x.y"},
                                    "steps": [{"id": "turn", "type": "bot_turn", "bot": "bot:" + slug,
                                               "prompt": "Check prices."}]})["id"]
    post(api, f"automations/{aid}/run", {})
    api.app.state.store.automations.drain()
    return finish(api, machine, claim(api, machine, slug))


def wake(api, machine, slug):
    with api.app.state.store.transaction() as c:
        room = rooms.work_room(c, api.app.state.auth, slug, H.KEEPER)
        H.say(c, H.KEEPER, "bot:" + slug, "Prices moved.", kind="say", conversation_id=room["id"],
               refs={"wake": "update"})
    return claim(api, machine, slug)


def test_a_manager_reads_its_reports_runs_and_peers_and_reports_do_not(api):
    bot(api, "boss")
    bot(api, "pm", reports_to="boss")
    bot(api, "peer", reports_to="boss")
    bot(api, "mon", reports_to="pm")
    machine = runner(api, "ana")
    for slug in ("boss", "pm", "peer", "mon"):
        assign(api, machine, slug)
    ready(api, machine, ["boss", "pm", "peer", "mon"])
    sweep = routine_run(api, machine, "mon")
    post(api, "chat/mon", {"text": "Between us: anything new?"})
    chat = finish(api, machine, claim(api, machine, "mon"))
    turns = {}

    def review(slug):
        post(api, "tasks", {"owner": slug, "title": "Weekly review", "body": "Review."})
        turns[slug] = claim(api, machine, slug)["token"]
    review("boss")
    # A wake the hub posts into the operator's chat room, outside any task: before, only that room's run read it.
    asked = finish(api, machine, wake(api, machine, "mon"))
    above = finish(api, machine, wake(api, machine, "pm"))
    for slug in ("pm", "peer", "mon"):
        review(slug)

    def runs(caller, target):
        r = api.get(f"/api/v2/bots/{target}/turns", headers={"Authorization": "Bearer " + turns[caller]})
        assert r.status_code == 200, r.text
        return {row["id"]: row for row in r.json()}

    def steps(caller, aid):
        return api.get(f"/api/v2/turns/{aid}/steps", headers={"Authorization": "Bearer " + turns[caller]}).status_code

    for manager in ("pm", "boss"):          # the chain, at any depth
        seen = runs(manager, "mon")
        assert {sweep, asked} <= set(seen) and chat not in seen
        assert seen[asked]["input_tokens"] == 100 and seen[asked]["est_cost_usd"] is not None
        assert steps(manager, asked) == 200 and steps(manager, chat) != 200
    assert asked not in runs("peer", "mon") and steps("peer", asked) != 200
    assert above not in runs("mon", "pm") and steps("mon", above) != 200
    assert {sweep, chat, asked} <= {r["id"] for r in get(api, "bots/mon/turns")}     # people keep their own rule
