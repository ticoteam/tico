"""Exit for bots (backend/exits.py): only listed sites go through an exit, only the computer's operator or an admin
turns one on, every stream is authorized against the run, its bot's site list and the exit's own bot list, an offline
exit makes the task wait on a card (never direct), and every use is summed into history."""

import pytest

from backend import exits
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, put, ready, runner  # noqa: F401  (the api fixture)

KEY = "k" * 40


def private(api, body, key=KEY, expected=200):
    response = api.post(exits.PRIVATE_PATH, json=body, headers={"Authorization": "Bearer " + key})
    assert response.status_code == expected, response.text
    return response.json()


@pytest.fixture
def world(api):
    """Ops runs on Ana's bot box; Cara's Home Mac offers to be an exit. The relay's private key is set."""
    api.app.state.store.settings.screen_relay_key = KEY
    bot_box = runner(api, operator="ana", label="Bot box")
    assign(api, bot_box, "ops")
    ready(api, bot_box, ["ops"])
    mac = runner(api, operator="cara", label="Home Mac")
    beat = post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": [exits.CAPABILITY]},
                token=mac["token"])
    assert beat["exit"] is False
    return bot_box, mac


def task_attempt(api, bot_box):
    task = post(api, "tasks", {"owner": "ops", "title": "Check the order page", "body": "Look at the shop."})
    attempt = claim(api, bot_box)
    assert attempt and attempt["bot"] == "ops"
    return task, attempt


def turn_on(api, mac, bots=("ops",), token="cara-test", expected=200):
    return post(api, f"runners/{mac['runner_id']}/exit", {"on": True, "bots": list(bots)}, token=token,
                expected=expected)


def test_site_names_and_routing():
    assert exits.normalize_host("Shop.Acme.Example.") == "shop.acme.example"
    assert exits.normalize_host("https://shop.acme.example/orders?x=1") == "shop.acme.example"
    assert exits.normalize_host("*.acme.example") == "*.acme.example"
    assert exits.normalize_host("bücher.example") == "xn--bcher-kva.example"
    for bad in ("localhost", "10.0.0.1", "a.*.example", "*.", "", "-x.example", "x_y.example", "*"):
        assert exits.normalize_host(bad) is None, bad
    sites = [{"host": "*.acme.example", "exit": "computer:a"}, {"host": "*.shop.acme.example", "exit": "computer:b"},
             {"host": "shop.acme.example", "exit": "direct"}]
    assert exits.route(sites, "shop.acme.example")["exit"] == "direct"           # the exact name wins
    assert exits.route(sites, "eu.shop.acme.example")["exit"] == "computer:b"    # then the longest wildcard
    assert exits.route(sites, "www.acme.example")["exit"] == "computer:a"
    assert exits.route(sites, "acme.example") is None                            # a wildcard never covers the name itself
    assert exits.route(sites, "evilacme.example") is None
    assert exits.parse_exit("provider:static-1") is None                          # room left, nothing built


def test_only_the_operator_or_an_admin_turns_an_exit_on_and_only_on_a_runner_that_offers_it(api, world):
    bot_box, mac = world
    post(api, f"runners/{bot_box['runner_id']}/exit", {"on": True, "bots": ["ops"]}, token="cara-test",
         expected=403)                                                            # a member, not its operator
    assert post(api, f"runners/{bot_box['runner_id']}/exit", {"on": True, "bots": ["ops"]},
                expected=409)["error"]["code"] == "exit_unavailable"              # it doesn't offer to be one
    turn_on(api, mac, bots=("nobody",), expected=404)
    assert turn_on(api, mac) == {"exit": {"on": True, "bots": ["ops"]}, "exit_available": True}
    row = next(m for m in get(api, "computers")["computers"] if m["id"] == mac["runner_id"])
    assert row["exit"] == {"on": True, "bots": ["ops"]} and "exit_json" not in row
    beat = post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": [exits.CAPABILITY]},
                token=mac["token"])
    assert beat["exit"] is True                                                   # the runner opens its exit link
    assert post(api, f"runners/{mac['runner_id']}/exit", {"on": False, "bots": ["ops"]}, token="ben-test") == {
        "exit": {"on": False, "bots": []}, "exit_available": True}                 # an admin turns it off


def test_a_site_picks_an_exit_only_when_that_computer_takes_the_bot_and_runs_get_only_exit_routes(api, world):
    bot_box, mac = world
    exit_ = f"computer:{mac['runner_id']}"
    sites = {"sites": [{"host": "shop.acme.example", "exit": exit_}]}
    assert put(api, "bots/ops/sites", sites, expected=409)["error"]["code"] == "exit_not_allowed"
    turn_on(api, mac)
    put(api, "bots/ops/sites", sites, token="cara-test", expected=403)            # the exit's owner cannot manage ops
    put(api, "bots/ops/sites", {"sites": [{"host": "a.example", "exit": "direct"}, {"host": "A.example"}]},
        expected=422)                                                              # listed twice
    view = put(api, "bots/ops/sites", {"sites": [{"host": "Shop.acme.example", "exit": exit_},
                                                 {"host": "*.cdn.example"}]})
    assert view["sites"] == [{"host": "*.cdn.example", "exit": "direct", "exit_name": "", "exit_takes_bot": True},
                             {"host": "shop.acme.example", "exit": exit_, "exit_name": "Home Mac", "exit_takes_bot": True}]
    assert view["exits"] == [{"exit": exit_, "name": "Home Mac"}]
    _, attempt = task_attempt(api, bot_box)
    routes = get(api, f"runners/me/exit/routes?attempt={attempt['id']}", token=bot_box["token"])["routes"]
    assert routes == [{"host": "shop.acme.example", "exit": exit_, "name": "Home Mac"}]      # direct stays out
    get(api, f"runners/me/exit/routes?attempt={attempt['id']}", token=mac["token"], expected=403)   # not its run


def test_every_stream_is_authorized_against_the_run_the_site_list_and_the_exits_own_list(api, world):
    bot_box, mac = world
    exit_ = f"computer:{mac['runner_id']}"
    turn_on(api, mac)
    put(api, "bots/ops/sites", {"sites": [{"host": "*.acme.example", "exit": exit_}, {"host": "news.example"}]})
    _, attempt = task_attempt(api, bot_box)
    private(api, {"kind": "open"}, key="wrong" * 10, expected=403)

    def open_(host, port=443, rid=bot_box["runner_id"], aid=attempt["id"]):
        return private(api, {"kind": "open", "runner_id": rid, "attempt": aid, "host": host, "port": port})

    allowed = open_("WWW.acme.example")
    assert allowed == {"allowed": True, "bot": "ops", "task": allowed["task"], "host": "www.acme.example", "exit": exit_,
                       "exit_runner": mac["runner_id"], "name": "Home Mac", "limit": exits.STREAMS_PER_BOT}
    assert open_("acme.example")["code"] == "not_listed"           # the wildcard covers names under it only
    assert open_("news.example")["code"] == "not_listed"           # direct is never an exit's
    assert open_("10.0.0.5")["code"] == "not_listed"
    assert open_("www.acme.example", port=25)["code"] == "port"
    assert open_("www.acme.example", rid=mac["runner_id"])["code"] == "forbidden"   # another computer's run
    turn_on(api, mac, bots=("cpo",))                                 # the exit's owner takes ops off
    assert open_("www.acme.example")["code"] == "exit_not_allowed"
    turn_on(api, mac)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='succeeded' WHERE id=?", (attempt["id"],))
    assert open_("www.acme.example")["code"] == "stale_lease"

    attached = private(api, {"kind": "attach", "authorization": "Bearer " + mac["token"]})
    assert attached == {"runner_id": mac["runner_id"], "name": "Home Mac", "serving": True}
    private(api, {"kind": "attach", "authorization": "Bearer ana-test"}, expected=403)   # a person is no computer


def test_an_offline_exit_makes_the_task_wait_on_a_card_and_its_return_wakes_it_once(api, world):
    bot_box, mac = world
    exit_ = f"computer:{mac['runner_id']}"
    turn_on(api, mac)
    put(api, "bots/ops/sites", {"sites": [{"host": "shop.acme.example", "exit": exit_}]})
    task, attempt = task_attempt(api, bot_box)
    at = H.now()
    done = private(api, {"kind": "report", "offline": [{"attempt": attempt["id"], "host": "shop.acme.example",
                                                         "port": 443, "exit": exit_, "at": at}]})
    assert done == {"usage": 0, "offline": 1, "cards": 1}
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        card = c.execute("SELECT * FROM task_reconnects WHERE task_id=?", (task["id"],)).fetchone()
        message = c.execute("SELECT body FROM messages WHERE id=?", (card["message_id"],)).fetchone()
        session = c.execute("SELECT refused,streams,last_error FROM exit_sessions").fetchone()
    assert row["status"] == "waiting" and row["note"] == "Waiting for exit computer Home Mac"
    assert (card["key"], card["person"]) == (f"exit:{mac['runner_id']}", "human:cara")   # its owner turns it back on
    assert message["body"] == "ops is waiting for exit computer Home Mac."
    assert tuple(session) == (1, 0, "exit offline")
    # A second refusal reuses the card.
    private(api, {"kind": "report", "offline": [{"attempt": attempt["id"], "host": "shop.acme.example", "port": 443,
                                                  "exit": exit_, "at": H.now()}]})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_reconnects").fetchone()[0] == 1
    private(api, {"kind": "attach", "authorization": "Bearer " + mac["token"]})   # the exit is back
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
        card = c.execute("SELECT ended FROM task_reconnects WHERE task_id=?", (task["id"],)).fetchone()
    assert row["status"] != "waiting" and card["ended"] == "fixed"
    # A refusal from before the exit came back opens no new card.
    assert private(api, {"kind": "report", "offline": [{"attempt": attempt["id"], "host": "shop.acme.example",
                                                         "port": 443, "exit": exit_, "at": at}]})["cards"] == 0


def test_usage_is_summed_per_run_host_and_exit_and_the_history_is_its_owners(api, world):
    bot_box, mac = world
    exit_ = f"computer:{mac['runner_id']}"
    turn_on(api, mac)
    put(api, "bots/ops/sites", {"sites": [{"host": "shop.acme.example", "exit": exit_}]})
    _, attempt = task_attempt(api, bot_box)
    use = {"attempt": attempt["id"], "host": "shop.acme.example", "port": 443, "exit": exit_}
    private(api, {"kind": "report", "usage": [{**use, "opened": 3, "open": 2, "up": 1000, "down": 50000}]})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT ended FROM exit_sessions").fetchone()[0] is None   # streams still open
    private(api, {"kind": "report", "usage": [{**use, "opened": 1, "open": 0, "up": 24, "down": 976},
                                              {**use, "exit": "direct", "opened": 1}]})   # never recorded as an exit
    history = get(api, "exit-sessions?bot=ops")["sessions"]
    assert len(history) == 1
    s = history[0]
    assert (s["bot"], s["host"], s["exit"], s["exit_name"], s["streams"], s["bytes_up"], s["bytes_down"]) == (
        "ops", "shop.acme.example", exit_, "Home Mac", 4, 1024, 50976)
    assert s["ended"] and s["started"] <= s["ended"]
    get(api, "exit-sessions?bot=ops", token="cara-test", expected=403)
    get(api, f"exit-sessions?computer={bot_box['runner_id']}", token="cara-test", expected=403)
    assert len(get(api, f"exit-sessions?computer={mac['runner_id']}", token="cara-test")["sessions"]) == 1   # her own
