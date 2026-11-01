"""Browser exits: only a computer's owner shares its internet, and only with the bots they pick; a bot's managers point it
at such a computer; every tunnel is checked against both and fails closed, naming the exit; what went through is
added up per day for the owner. The relay itself is a stand-in here (relay-rs/src/exit.rs has its own tests)."""

import pytest

from backend import browser_exit as BE
from backend.store import Problem
from backend.tests.test_api import api, as_member, assign, get, post, restrict, runner  # noqa: F401  (the api fixture)


class Relay:
    """Stands in for screen_relay.RemoteRegistry's exit operations."""

    def __init__(self):
        self.answers, self.grants, self.pushed, self.closed, self.fenced = [], [], [], [], []
        self.lease = "lease-1"

    def exit_epoch(self):
        return self.lease

    def push_exit_policy(self, rid):
        self.pushed.append(rid)

    def push_bot_generation(self, bot, reason):
        self.fenced.append((bot, reason))

    def exit_grant(self, grant, epoch):
        self.grants.append((grant, epoch))
        return self.answers.pop(0) if self.answers else {"ticket": "a" * 64}

    def exit_close(self, bot=None, exit=None, reason=""):
        self.closed.append((bot, exit, reason))


@pytest.fixture
def relay(api, monkeypatch):
    fake, sessions = Relay(), api.app.state.store.screen_sessions
    for name in ("exit_epoch", "push_exit_policy", "push_bot_generation", "exit_grant", "exit_close"):
        monkeypatch.setattr(sessions, name, getattr(fake, name))
    return fake


def laptop(api):
    """Cara's laptop, and the computer ops runs on (Ana's)."""
    caras = runner(api, operator="cara", label="Cara's MacBook")
    server = runner(api, operator="ana", label="Office server")
    assign(api, server, "ops")
    return caras, server


def share(api, rid, token="cara-test", expected=200, **body):
    return post(api, f"computers/{rid}/browser-exit", {"sharing": True, "bots": ["ops"], **body}, token=token,
                expected=expected)


def tunnel(api, r, expected=200, bot="ops", host="www.acme.example", port=443):
    return post(api, "runners/me/browser-exit/tunnels", {"bot": bot, "host": host, "port": port}, token=r["token"],
                expected=expected)


def test_only_the_computers_owner_shares_its_internet_and_an_admin_may_only_turn_it_off(api, relay):
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    for token in ("ana-test", "ben-test"):                      # the owner of the company, an admin
        share(api, rid, token=token, expected=403)
    assert get(api, f"computers/{rid}/browser-exit", token="cara-test")["sharing"] is False      # off by default
    share(api, rid, token="cara-test", bots=["no-such-bot"], expected=404)
    assert share(api, rid) == {"sharing": True, "bots": ["ops"], "daily_cap_bytes": BE.DEFAULT_CAP}
    assert relay.pushed == [rid]
    seen = get(api, f"computers/{rid}/browser-exit", token="cara-test")
    assert (seen["may_change"], seen["used_today_bytes"]) == (True, 0)
    # An admin turns it off but changes nothing else; heartbeats say so to the runner.
    assert post(api, "runners/heartbeat", {"version": "t", "platform": "t"}, token=caras["token"])["browser_exit"]
    post(api, f"computers/{rid}/browser-exit", {"sharing": True, "bots": []}, token="ben-test", expected=403)
    assert post(api, f"computers/{rid}/browser-exit", {"sharing": False}, token="ben-test")["bots"] == ["ops"]
    assert not post(api, "runners/heartbeat", {"version": "t", "platform": "t"}, token=caras["token"])["browser_exit"]
    share(api, rid)
    as_member(api, "ben@acme.example")
    post(api, f"computers/{rid}/browser-exit", {"sharing": False}, token="ben-test", expected=403)
    row = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == rid)
    assert row["browser_exit"]["sharing"] and "used_today_bytes" not in row["browser_exit"]


def test_a_member_sees_only_the_shared_bots_they_may_see(api, relay):
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    share(api, rid, bots=["ops", "finance"])
    as_member(api, "ben@acme.example")
    with api.app.state.store.transaction() as c:
        restrict(c, "finance", people=["ana"])
    assert get(api, f"computers/{rid}/browser-exit", token="ben-test")["bots"] == ["ops"]
    row = next(m for m in get(api, "operations", token="ben-test")["computers"] if m["id"] == rid)
    assert row["browser_exit"]["bots"] == ["ops"]         # the same as the Computers listing
    assert get(api, f"computers/{rid}/browser-exit", token="cara-test")["bots"] == ["ops", "finance"]


def test_a_bot_browses_only_through_a_computer_that_shares_with_it_and_a_change_closes_its_connections(api, relay):
    caras, server = laptop(api)
    rid = caras["runner_id"]
    assert post(api, "bots/ops/browser-exit", {"runner_id": rid}, expected=409)["error"]["code"] == \
        "browser_exit_not_shared"
    share(api, rid)
    post(api, "bots/ops/browser-exit", {"runner_id": rid}, token="cara-test", expected=403)   # not ops's manager
    choices = get(api, "bots/ops/browser-exit")
    assert [c["runner_id"] for c in choices["choices"]] == [rid] and choices["browser_exit"] is None
    assert post(api, "bots/ops/browser-exit", {"runner_id": rid})["browser_exit"] == {
        "runner_id": rid, "name": "Cara's MacBook"}
    ops = next(e for e in api.get("/api/employees", headers={"Authorization": "Bearer ana-test"}).json()
               if e["name"] == "ops")
    assert ops["browser_exit"] == rid
    assert relay.fenced == [("ops", "browser exit changed")]
    gen = _bot_generation(api, "ops")
    post(api, "bots/ops/browser-exit", {"runner_id": rid})                  # the same exit: nothing changes
    assert (_bot_generation(api, "ops"), len(relay.fenced)) == (gen, 1)
    post(api, "bots/ops/browser-exit", {"runner_id": None})
    assert relay.fenced[-1] == ("ops", "browser exit changed") and _bot_generation(api, "ops") == gen + 1
    assert get(api, "bots/ops/browser-exit")["browser_exit"] is None
    # Moving the bot to another computer fences its grants too, in the same transaction.
    post(api, "bots/ops/browser-exit", {"runner_id": rid})
    gen = _bot_generation(api, "ops")
    spare = runner(api, operator="ana", label="Spare server")
    with api.app.state.store.read() as c:
        moved = c.execute("SELECT generation FROM assignments WHERE bot='ops'").fetchone()[0]
    assign(api, spare, "ops", generation=moved)
    assert _bot_generation(api, "ops") == gen + 1


def test_removing_the_bots_computer_fences_a_grant_read_before_it(api, relay):
    caras, server = laptop(api)
    rid = caras["runner_id"]
    share(api, rid)
    post(api, "bots/ops/browser-exit", {"runner_id": rid})
    read_before = _bot_generation(api, "ops")
    relay.fenced.clear()
    post(api, f"runners/{server['runner_id']}/revoke", {})
    # The relay refuses a grant carrying the older generation (exit_stale) and closes the bot's open tunnels.
    assert _bot_generation(api, "ops") > read_before
    assert relay.fenced == [("ops", "computer removed")]


def test_a_bot_with_an_exit_waits_for_a_runner_that_browses_through_it(api, relay):
    """An older runner would ignore the exit and browse from its own address: none of the bot's work goes to it."""
    from backend import health
    from backend.tests.test_api import claim
    from backend.tests.test_claim_refusals import health_admin, held
    caras, server = laptop(api)
    share(api, caras["runner_id"])
    post(api, "bots/ops/browser-exit", {"runner_id": caras["runner_id"]})
    post(api, "chat/ops", {"text": "Look up the synthetic fixture"})

    def heartbeat(capabilities):
        post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {"ops": True},
                                        "capabilities": capabilities}, token=server["token"])
    heartbeat([])
    rows, _ = held(api, server)
    assert [r["reason"] for r in rows] == ["browser_exit_update"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts WHERE bot_id='ops'").fetchone()[0] == 0
        [row] = health._stuck(c, rows, set(), health_admin(api), True)
    assert "Office server" in row["why"], row
    heartbeat([BE.CAPABILITY])
    assert claim(api, server, "ops")["browser_exit"]["runner_id"] == caras["runner_id"]


def test_the_heartbeat_names_the_bots_whose_proxies_the_runner_keeps(api, relay):
    caras, server = laptop(api)
    share(api, caras["runner_id"])

    def kept():
        return post(api, "runners/heartbeat", {"version": "test", "platform": "test"},
                    token=server["token"])["browser_exit_bots"]
    assert kept() == []
    post(api, "bots/ops/browser-exit", {"runner_id": caras["runner_id"]})
    assert kept() == ["ops"]
    with api.app.state.store.read() as c:      # its runner was told to ask now
        assert c.execute("SELECT 1 FROM changes WHERE topic='runner' AND kind='browser_exit' AND subject_id=?",
                         (server["runner_id"],)).fetchone()
    post(api, "bots/ops/browser-exit", {"runner_id": None})
    assert kept() == []


def test_every_tunnel_fails_closed_with_words_that_name_the_exit(api, relay):
    caras, server = laptop(api)
    rid = caras["runner_id"]

    def refused(expected, code, words=None, **kw):
        error = tunnel(api, server, expected=expected, **kw)["error"]
        assert error["code"] == code, error
        if words:
            assert words in error["detail"], error

    refused(400, "browser_exit_refused", port=22)
    refused(400, "browser_exit_refused", host="www.acme.example/x")
    refused(403, "forbidden", bot="cpo")                             # not a bot on this computer
    refused(409, "browser_exit_none")
    share(api, rid)
    post(api, "bots/ops/browser-exit", {"runner_id": rid})
    tunnel(api, caras, expected=403)                                 # ops is not on Cara's laptop
    post(api, f"computers/{rid}/browser-exit", {"sharing": True, "bots": []}, token="cara-test")
    refused(403, "browser_exit_not_shared", "Cara's MacBook doesn't share its internet with this bot")
    share(api, rid)
    for answer, status, code, words in [
            ("exit_offline", 409, "browser_exit_offline", "Cara's MacBook is offline"),
            ("exit_over_cap", 429, "browser_exit_over_cap", "Cara's MacBook reached its daily limit"),
            ("exit_busy", 429, "browser_exit_busy", "Cara's MacBook"),
            ("unavailable", 503, "browser_exit_unavailable", "Cara's MacBook")]:
        relay.answers = [answer]
        refused(status, code, words)
    # A policy change or a new relay lease between the read and the grant: read again once.
    relay.answers = ["exit_stale"]
    granted = tunnel(api, server)
    assert granted["ticket"] == "a" * 64 and granted["exit"] == {"runner_id": rid, "name": "Cara's MacBook"}
    grant, epoch = relay.grants[-1]
    assert (grant["exit"], grant["runner_id"], grant["bot"], grant["port"]) == (rid, server["runner_id"], "ops", 443)
    # The stored generations: the policy was written three times, the bot's exit set once.
    assert grant["policy"] == {"generation": 3, "sharing": True, "bots": ["ops"], "cap": BE.DEFAULT_CAP}
    assert (grant["bot_generation"], epoch) == (_bot_generation(api, "ops"), "lease-1")
    # Over the day's cap by recorded bytes: refused here, the relay never asked.
    asked = len(relay.grants)
    with api.app.state.store.transaction() as c:
        BE.record(c, [{"exit": rid, "day": BE.today(), "bot": "ops", "host": "www.acme.example",
                       "bytes": BE.DEFAULT_CAP, "tunnels": 1}])
    refused(429, "browser_exit_over_cap", "Cara's MacBook reached its daily limit")
    assert len(relay.grants) == asked
    # A removed exit says so, and its tunnels close.
    post(api, f"runners/{rid}/revoke", {}, token="cara-test")
    refused(409, "browser_exit_removed", "Cara's MacBook was removed")
    assert (None, rid, "computer removed") in relay.closed


def test_the_owner_sees_what_went_through_per_day_and_old_days_go(api, relay):
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    old = "2000-01-01"
    row = {"exit": rid, "runner_id": "bot-computer", "day": BE.today(), "bot": "ops"}
    batch = {"id": "b1", "seq": 1, "rows": [{**row, "host": "WWW.acme.example", "bytes": 700, "tunnels": 0},  # still open
                                  {**row, "host": "www.acme.example", "bytes": 300, "tunnels": 2},
                                  {**row, "host": "cdn.acme.example", "bytes": 5, "tunnels": 1},
                                  {**row, "day": old, "host": "www.acme.example", "bytes": 9, "tunnels": 1},
                                  {"exit": rid, "bot": "ops"}]}                              # malformed: skipped
    for _ in range(2):          # the relay resends a batch until acknowledged: recorded once
        with api.app.state.store.transaction() as c:
            assert BE.record_batch(c, "boot-1", batch) == "b1"
    get(api, f"computers/{rid}/browser-exit/usage", token="ben-test")                       # an admin
    as_member(api, "ben@acme.example")
    get(api, f"computers/{rid}/browser-exit/usage", token="ben-test", expected=403)
    page = get(api, f"computers/{rid}/browser-exit/usage?limit=1", token="cara-test")
    assert page["rows"] == [{"day": BE.today(), "bot": "ops", "host": "www.acme.example", "bytes": 1000, "tunnels": 2}]
    assert page["next"] == 1 and page["days"] == [{"day": BE.today(), "bytes": 1005}]
    assert get(api, f"computers/{rid}/browser-exit/usage?offset=1", token="cara-test")["next"] is None
    with api.app.state.store.transaction() as c:
        BE.prune(c)
        assert not c.execute("SELECT 1 FROM browser_exit_usage WHERE day=?", (old,)).fetchone()


def test_a_usage_batch_resent_after_a_long_outage_is_recorded_once(api, relay):
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    row = {"exit": rid, "runner_id": "bot-computer", "day": BE.today(), "bot": "ops", "host": "www.acme.example",
           "tunnels": 0}
    first, second = {"id": "b1", "seq": 1, "rows": [{**row, "bytes": 100}]}, {"id": "b2", "seq": 2, "rows": [
        {**row, "bytes": 50}]}

    def send(boot, batch):
        with api.app.state.store.transaction() as c:
            return BE.record_batch(c, boot, batch)
    assert send("boot-1", first) == "b1"
    with api.app.state.store.transaction() as c:
        BE.prune(c)                                             # an hourly prune, days of them: the mark stays
    assert send("boot-1", first) == "b1"                        # its acknowledgement was lost, days ago
    assert send("boot-1", second) == "b2"
    assert send("boot-2", {**first, "id": "c1"}) == "c1"        # a restarted relay numbers from 1 again
    with api.app.state.store.read() as c:
        assert BE.used(c, rid) == 250


def test_one_tick_collects_the_whole_usage_backlog_so_none_builds_up(api, monkeypatch):
    sessions = api.app.state.store.screen_sessions
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    row = {"exit": rid, "runner_id": "bot-computer", "day": BE.today(), "bot": "ops", "tunnels": 0, "bytes": 10}
    waiting = [{**row, "host": f"h{i}.acme.example"} for i in range(9)]
    relay, beats = {"batch": None, "seq": 0}, []

    def command(op, **fields):          # the relay's heartbeat, two rows a batch
        beats.append(fields["exit_ack"])
        if relay["batch"] and relay["batch"]["id"] in fields["exit_ack"]:
            relay["batch"] = None
        if relay["batch"] is None and waiting:
            relay["seq"] += 1
            relay["batch"] = {"id": f"b{relay['seq']}", "seq": relay["seq"], "rows": waiting[:2]}
            del waiting[:2]
        in_hand = len(relay["batch"]["rows"]) if relay["batch"] else 0
        return {"reports": [], "exit_usage": relay["batch"], "exit_backlog": len(waiting) + in_hand}
    monkeypatch.setattr(sessions, "_command", command)
    monkeypatch.setattr(sessions, "_exit_reconcile", lambda: None)
    monkeypatch.setattr(sessions, "healthy", True)
    monkeypatch.setattr(sessions, "boot", "boot-1")
    sessions.tick()
    with api.app.state.store.read() as c:
        assert BE.used(c, rid) == 90
    assert len(beats) == 5 and sessions.exit_acks == ["b5"]    # the last one is acknowledged by the next tick
    sessions.tick()
    assert len(beats) == 6 and relay["batch"] is None


def _bot_generation(api, bot):
    with api.app.state.store.read() as c:
        return BE.bot_generation(c, bot)



def test_the_relay_lets_an_exit_link_in_only_while_its_owner_shares(api, relay):
    caras, server = laptop(api)
    state = api.app.state

    def link(r):
        return BE.authorize_link(state.store, state.auth, state.execution, {"authorization": "Bearer " + r["token"]})

    with pytest.raises(Problem):
        link(caras)
    share(api, caras["runner_id"])
    assert link(caras) == {"allowed": True, "runner_id": caras["runner_id"]}
    with pytest.raises(Problem):
        link(server)


def test_a_push_the_relay_missed_or_lost_to_a_new_lease_is_sent_again_and_older_reads_are_refused(api, monkeypatch):
    sessions = api.app.state.store.screen_sessions
    caras, _ = laptop(api)
    rid = caras["runner_id"]
    sent, down = [], [True]

    def command(op, **fields):
        if down[0]:
            raise Problem("browser_exit_unavailable", "The relay is unavailable. Try again shortly.", 503)
        sent.append((op, fields.get("runner_id") or fields.get("bot")))
        return {"closed": 0}
    monkeypatch.setattr(sessions, "_exit_command", command)
    share(api, rid)
    post(api, "bots/ops/browser-exit", {"runner_id": rid})
    assert sent == []                                       # both pushes failed; the writes stand
    down[0] = False
    sessions._exit_reconcile()
    assert sorted(sent) == [("exit_close", "ops"), ("exit_policy", rid)]
    sent.clear()
    sessions._exit_reconcile()
    assert sent == []                                       # nothing new
    # A new lease (what start() does): the relay forgot, so everything goes again, and a grant read before is refused.
    old = sessions.exit_epoch()
    sessions.lease = "b" * 32
    sessions.exit_pushed.clear()
    sessions.bot_pushed.clear()
    assert sessions.exit_grant({}, old) == "exit_stale"
    sessions._exit_reconcile()
    assert sorted(sent) == [("exit_close", "ops"), ("exit_policy", rid)]
