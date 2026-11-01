"""Turns an automation starts (backend/automations.py, backend/automation_actions.py): in the bot's own room, never the
sender's; an llm step waits for the real turn and goes on with its text; the turn's priced cost is charged to the run,
so the spend cap counts it; `assistant:<person>` makes the person's Assistant room on first use."""
import json

from backend.tests.test_api import api, assign, claim, get, post, put, ready, runner  # noqa: F401

USAGE = {"input_tokens": 400_000, "cached_tokens": 0, "output_tokens": 10_000, "model": "claude-opus-5-5",
         "runtime": "claude", "billing": "api"}


def finish(api, r, text="A draft"):
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=r["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": text, "last_seq": 0,
                                                     "usage": USAGE}, token=r["token"])
    return attempt


def draft_then_tell(**extra):
    return {"name": "Draft", "owner_id": "bot:ops", "trigger": {"type": "event", "name": "x.y"}, **extra, "steps": [
        {"id": "draft", "type": "llm", "as": "bot:ops", "prompt": "Draft a reply", "out": "reply"},
        {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "coo", "text": "{{reply.text}}"}}]}


def test_an_llm_step_waits_for_the_bots_turn_in_its_own_room_and_goes_on(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = post(api, "automations", draft_then_tell())["id"]
    run = post(api, f"automations/{aid}/run", {})["run"]
    assert run["status"] == "waiting"
    turn = run["steps"][0]["outputs"]["turn"]
    assert turn["bot"] == "ops" and turn["task"]
    with api.app.state.store.read() as c:
        room = c.execute("SELECT room_key, scope FROM conversations WHERE id=?", (turn["session"],)).fetchone()
        task = c.execute("SELECT owner_id, requester_id FROM tasks WHERE id=?", (turn["task"],)).fetchone()
    assert room is not None   # a task of its own, with its own room: never a person's chat or another task's session
    assert task["owner_id"] == "bot:ops" and task["requester_id"] == "bot:ops"   # filed by the owner, bot:ops
    finish(api, r)
    done = get(api, f"automations/{aid}/runs")["runs"][0]
    assert done["status"] == "ok" and [s["status"] for s in done["steps"]] == ["ok", "ok"]
    assert done["steps"][0]["outputs"]["text"] == "A draft" and done["cost"] > 0
    assert done["steps"][0]["cost"] == done["cost"]
    with api.app.state.store.read() as c:
        told = c.execute("SELECT body, refs FROM messages WHERE recipient_id='bot:coo' ORDER BY created_at DESC").fetchone()
    # The answer reaches coo as quoted data, never as the automation's own words.
    assert '<data n="1" field="reply.text">\nA draft\n</data n="1">' in told["body"]
    assert json.loads(told["refs"])["automation_run"] == run["id"]


def test_a_turns_cost_counts_toward_the_spend_cap(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = post(api, "automations", draft_then_tell(spend_cap={"per_30m": 0.01}))["id"]
    post(api, f"automations/{aid}/run", {})
    finish(api, r)
    capped = post(api, f"automations/{aid}/run", {})["run"]
    assert [(s["id"], s["status"]) for s in capped["steps"]] == [("draft", "spend_cap"), ("tell", "ok")]
    assert claim(api, r) is None                                     # no turn started
    assert capped["summary"] == "1 stopped by the spend cap"


def test_an_assistant_step_makes_the_persons_assistant_room_on_first_use(api):
    assert get(api, "people/ben/assistant") == {"bot": None}
    flow = {"name": "Brief", "owner": "person:ben", "trigger": {"type": "event", "name": "x.y"},
            "steps": [{"id": "brief", "type": "llm", "as": "assistant:ben", "prompt": "Brief me", "out": "b"}]}
    aid = post(api, "automations", flow)["id"]
    run = post(api, f"automations/{aid}/run", {})["run"]
    bot = get(api, "people/ben/assistant")["bot"]
    assert bot and run["steps"][0]["outputs"]["turn"]["bot"] == bot
    get(api, "people/nobody/assistant", expected=404)


def test_a_market_change_made_in_an_automations_turn_names_the_turn_and_the_match(api):
    """A listening match reaches the graph only through the Librarian's turn: what the turn files through the market
    routes is shown on the graph with the turn, the run and the match it came from."""
    from backend.tests.test_market import _librarian
    _librarian(api)
    r = runner(api)
    assign(api, r, "librarian")
    ready(api, r, ["librarian"])
    flow = {"name": "Market · tools", "owner": "bot:librarian", "trigger": {"type": "listening", "destination": "tools"},
            "steps": [{"id": "update", "type": "bot_turn", "bot": "librarian", "prompt": "Update the graph."}]}
    aid = post(api, "automations", flow)["id"]
    heard = {"id": "li_1", "destination": "tools", "url": "https://news.acme.example/mapper", "source": "news",
             "author": "Acme News", "content": "Acme Mapper launched."}
    run = post(api, f"automations/{aid}/run", {"input": {"listening": heard}})["run"]
    before = get(api, "market/graph")
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, token=r["token"])
    seen = post(api, "market/evidence", {"source_url": heard["url"], "source_kind": "news", "quote": "Launched."},
                token=attempt["token"])["evidence"]["id"]
    post(api, "market/entities", {"type": "product", "name": "Acme Mapper", "evidence_ids": [seen]},
         token=attempt["token"])
    graph = get(api, "market/graph")
    assert before["entities"] == [] and [e["name"] for e in graph["entities"]] == ["Acme Mapper"]
    assert graph["entities"][0]["source"] == {
        "automation": aid, "run": run["id"], "turn": {"attempt": attempt["id"], "bot": "librarian",
                                                      "task": run["steps"][0]["outputs"]["turn"]["task"]},
        "match": {"id": "li_1", "destination": "tools", "url": heard["url"], "source": "news"}}
    # A change by hand names no turn.
    post(api, "market/entities", {"type": "product", "name": "Acme Atlas", "evidence_ids": [seen]})
    assert [("source" in e) for e in get(api, "market/graph")["entities"]] == [False, True]
