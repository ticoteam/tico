"""A usage limit, seen from outside: what the claim hands out and what the bot shows, whatever stores the limit.

One table per entry point (a limit met, Try now, a connection change), each row a kind of limit: a provider's named
reset, a quota with no reset (the backoff), a model at capacity, and the Tico AI Gateway's cap, rate limit, credit and
outage. Every row checks the same contract: nothing starts before the limit's end, exactly one turn (the probe) starts
after it, and that turn's success lets the rest of the queue move and the bot stop showing Rate limited.
"""

import pytest

from backend import bot_settings as BS
from backend.migrations import DIRECTORY, statements
from backend.credentials import CredentialCipher
from backend.store import H
from backend.tests.helpers.api import assign, claim, post, runner
from backend.tests.helpers.credentials import FakeKMS
from backend.tests.helpers.login_holds import QUOTA, at, done, limit
from clients import gateway as G

HIT = "2026-10-15T12:00:00.000000Z"
CLAUDE = "You've hit your session limit · resets 12:40pm (UTC)"
CAPACITY = "Selected model is at capacity. Please try a different model."
GATEWAY_RATE = "Tico AI Gateway: rate limited; retrying after the Gateway's requested wait (rate_limited)"
GATEWAY_CAP = "Gateway cap reached: this gateway key has used its monthly spend limit (cap_reached)"
GATEWAY_CREDIT = (
    "Tico models are paused: this company is out of credit. Your bots use their other models until credit is added "
    "(out_of_credit)"
)
GATEWAY_DOWN = "Tico AI Gateway is unavailable, try again (upstream_unavailable)"


def tico(api, monkeypatch):
    """The bot runs on a Tico model, served by the Gateway (as backend/tests/test_gateway_connection.py sets it up)."""
    from backend import gateway_connection

    monkeypatch.setattr(gateway_connection, "_tried", {})
    monkeypatch.setattr(gateway_connection, "check_soon", lambda *args, **kwargs: None)
    monkeypatch.delenv("TICO_GATEWAY_KEY", raising=False)
    monkeypatch.delenv("TICO_GATEWAY_URL", raising=False)
    api.app.state.store.settings.credential_kms_key = "test-key"
    api.app.state.vault.cipher = CredentialCipher("test-key", FakeKMS())
    with api.app.state.store.transaction() as c:
        BS.write(c, "ops", {**BS.read(c, "ops"), "runtime": "pi", "harness": "pi", "model": "tico-medium-1.0"})


def ready(api, r, bots):
    """A computer that runs bots as their own user, the only kind a Tico model runs on, with the bots ready."""
    row = {"ready": True, "runtime": "fake", "repository_present": True, "configuration_valid": True}
    report = {"schema_version": 1, "isolated": True, "runtimes": {}, "bots": {bot: row for bot in bots}}
    return post(
        api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": report}, token=r["token"]
    )


def outage(api, machine, attempt):
    """The Gateway was down: the turn failed at once, having done nothing (not a limit the runner flagged)."""
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "gateway"}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": GATEWAY_DOWN}}]},
        machine["token"],
    )
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 1, "text": ""}, machine["token"])


# (id, on a Tico model, how the turn ends, when the hold ends, the bot shows Rate limited, exactly one probe after the
# end). The Gateway is one login (login_holds.GATEWAY): its cap, its address block and its outage hold it, and after
# them one probe tries it, as for any login.
LIMITS = [
    ("named_reset", False, dict(errors=(CLAUDE,)), "2026-10-15T12:40:00.000000Z", True, True),
    ("quota_backoff", False, dict(errors=(QUOTA,)), H.shift(HIT, seconds=1800), True, True),
    ("capacity", False, dict(errors=(CAPACITY,)), H.shift(HIT, seconds=120), True, True),
    (
        "gateway_rate",
        True,
        dict(errors=(GATEWAY_RATE,), limit_resets_at=H.shift(HIT, seconds=42)),
        H.shift(HIT, seconds=42),
        True,
        True,
    ),
    ("gateway_cap", True, dict(errors=(GATEWAY_CAP,)), "2026-11-01T00:00:01.000000Z", True, True),
    ("gateway_credit", True, dict(errors=(GATEWAY_CREDIT,)), H.shift(HIT, seconds=G.OUT_OF_CREDIT_WAIT), True, True),
    ("gateway_down", True, None, H.shift(HIT, seconds=60), False, True),
]


def limited_bot(api, monkeypatch, on_tico, ending, jobs=3):
    """`ops` with `jobs` tasks queued on a computer that runs many turns at once; its first turn ends as `ending`."""
    at(monkeypatch, HIT)
    if on_tico:
        tico(api, monkeypatch)
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET capacity=16 WHERE runner_id=?", (machine["runner_id"],))
    ready(api, machine, ["ops"])
    for n in range(jobs):
        post(api, "tasks", {"owner": "ops", "title": f"Task {n}", "body": "Please do it."})
    first = claim(api, machine, "ops")
    assert first and first["bot"] == "ops"
    if ending is None:
        outage(api, machine, first)
    else:
        limit(api, machine, first, **ending)
    return machine, first


def state(api, bot="ops"):
    with api.app.state.store.read() as c:
        return (H.status(c, bot) or {}).get("state")


def tick(api, monkeypatch, when, machine):
    """The clock reads `when`, the scheduler's limit sweep has run and the computer has reported in."""
    at(monkeypatch, when, api)
    with api.app.state.store.transaction() as c:
        api.app.state.execution.release_limits(c)
    ready(api, machine, ["ops"])


@pytest.mark.parametrize("name,on_tico,ending,until,shown,one", LIMITS, ids=[row[0] for row in LIMITS])
def test_a_limit_holds_until_its_end_then_one_probe_and_its_success_resumes_the_queue(
    api, monkeypatch, name, on_tico, ending, until, shown, one
):
    machine, first = limited_bot(api, monkeypatch, on_tico, ending)
    assert (state(api) == "limited") is shown
    tick(api, monkeypatch, H.shift(until, seconds=-2), machine)
    assert claim(api, machine, "ops") is None, "nothing starts before the limit ends"
    assert (state(api) == "limited") is shown
    tick(api, monkeypatch, H.shift(until, seconds=2), machine)
    assert state(api) != "limited", "the bot stops showing Rate limited once its limit has ended"
    probe = claim(api, machine, "ops")
    assert probe and probe["bot"] == "ops"
    second = claim(api, machine, "ops")
    assert (second is None) is one, "one probe turn per window, however many jobs wait"
    if second:
        done(api, machine, second)
    done(api, machine, probe)
    ready(api, machine, ["ops"])
    assert claim(api, machine, "ops"), "the probe worked: the queue moves"
    assert state(api) != "limited"


# Try now ends the hold at once: one probe, and the bot shows it is no longer waiting.
TRY_NOW = [row for row in LIMITS if row[0] in ("named_reset", "quota_backoff", "gateway_cap")]


@pytest.mark.parametrize("name,on_tico,ending,until,shown,one", TRY_NOW, ids=[row[0] for row in TRY_NOW])
def test_try_now_starts_one_probe_and_a_second_limit_puts_the_bot_back(
    api, monkeypatch, name, on_tico, ending, until, shown, one
):
    machine, first = limited_bot(api, monkeypatch, on_tico, ending)
    tick(api, monkeypatch, H.shift(HIT, seconds=60), machine)
    assert claim(api, machine, "ops") is None and state(api) == "limited"
    post(api, "bots/ops/limit/retry", {})
    assert state(api) != "limited"
    ready(api, machine, ["ops"])
    probe = claim(api, machine, "ops")
    assert probe and probe["bot"] == "ops"
    second = claim(api, machine, "ops")
    assert (second is None) is one, "Try now adds one turn, not a burst"
    if second:
        limit(api, machine, second, **ending)
    limit(api, machine, probe, **ending)
    assert state(api) == "limited"
    tick(api, monkeypatch, H.shift(HIT, seconds=90), machine)
    assert claim(api, machine, "ops") is None, "still spent: the window is back"


def test_a_bot_moved_to_another_computer_leaves_the_old_logins_limit_behind(api, monkeypatch):
    machine, first = limited_bot(api, monkeypatch, False, dict(errors=(CLAUDE,)))
    assert state(api) == "limited"
    other = runner(api, label="Other Mac")
    assign(api, other, "ops", generation=1)
    assert state(api) != "limited", "another computer is another login"
    ready(api, other, ["ops"])
    moved = claim(api, other, "ops")
    assert moved and moved["bot"] == "ops"
    done(api, other, moved)
    # Back on the first computer, its login is still held until the reset it named, and the bot shows it.
    assign(api, machine, "ops", generation=2)
    assert state(api) == "limited"
    ready(api, machine, ["ops"])
    assert claim(api, machine, "ops") is None


def gateway_env(api, monkeypatch):
    """The Gateway connected, as backend/tests/test_gateway_connection.py sets it up, without moving the bot to it."""
    from backend import gateway_connection

    monkeypatch.setattr(gateway_connection, "_tried", {})
    monkeypatch.setattr(gateway_connection, "check_soon", lambda *args, **kwargs: None)
    monkeypatch.delenv("TICO_GATEWAY_KEY", raising=False)
    monkeypatch.delenv("TICO_GATEWAY_URL", raising=False)
    api.app.state.store.settings.credential_kms_key = "test-key"
    api.app.state.vault.cipher = CredentialCipher("test-key", FakeKMS())


def hold_gateway(api, until):
    from backend import login_holds

    with api.app.state.store.transaction() as c:
        login_holds.record(
            c, "", login_holds.GATEWAY, login_holds.GATEWAY_RUNTIME, H.now(), error=GATEWAY_CAP, until=until
        )


def gateway_hold(api):
    from backend import login_holds

    with api.app.state.store.read() as c:
        return login_holds.get(c, "", login_holds.GATEWAY, login_holds.GATEWAY_RUNTIME)


def handed(api, attempt):
    from backend import gateway_connection

    with api.app.state.store.read() as c:
        return gateway_connection.handed(c, attempt["id"])


def test_a_held_gateway_is_never_handed_out_as_a_fallback_and_a_fallback_turn_is_its_probe(api, monkeypatch):
    # A bot on its own login whose named fallback is a Tico model: the runner would hop to the Gateway mid-turn with
    # the credentials the claim hands it, and an older runner reads no hold, so the claim decides.
    gateway_env(api, monkeypatch)
    at(monkeypatch, HIT)
    with api.app.state.store.transaction() as c:
        BS.write(c, "ops", {**BS.read(c, "ops"), "fallback": {"harness": "pi", "model": "tico-medium-1.0"}})
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET capacity=16 WHERE runner_id=?", (machine["runner_id"],))
    ready(api, machine, ["ops"])
    for n in range(4):
        post(api, "tasks", {"owner": "ops", "title": f"Task {n}", "body": "Please do it."})
    hold_gateway(api, H.shift(HIT, seconds=60))
    first = claim(api, machine, "ops")
    assert first, "the bot's own login is free: it runs"
    assert first["config"]["fallback"].get("off") and not handed(api, first), "no Gateway fallback while it is held"
    done(api, machine, first)
    tick(api, monkeypatch, H.shift(HIT, seconds=61), machine)
    probe = claim(api, machine, "ops")
    named = probe["config"]["fallback"]
    assert (named.get("harness"), named.get("model"), named.get("off")) == ("pi", "tico-medium-1.0", None)
    assert handed(api, probe)
    assert gateway_hold(api)["probe_job"] == probe["job_id"], "a turn that may hop to the Gateway is its one probe"
    other = claim(api, machine, "ops")
    assert other and other["config"]["fallback"].get("off") and not handed(api, other)


def test_a_tico_check_waits_on_the_gateways_hold_and_takes_its_one_probe(api, monkeypatch):
    from backend.tests.helpers.check_models import company, finish, parent_turn

    gateway_env(api, monkeypatch)
    at(monkeypatch, HIT)
    machine, parent, turn = parent_turn(api)
    company(api, "anthropic", "xai", "tico")
    report = {
        "schema_version": 1,
        "isolated": True,
        "runtimes": {
            "claude": {"installed": True, "authenticated": "ready"},
            "grok": {"installed": True, "authenticated": "ready"},
            "pi": {"installed": True, "authenticated": "ready"},
        },
        "bots": {"ops": {"runtime": "claude", "ready": True}},
    }
    beat = {"version": "test", "platform": "test", "capabilities": ["task_lanes_v2", "session_roots_v1"]}
    post(api, "runners/heartbeat", {**beat, "readiness": report}, token=machine["token"])
    for n in range(2):
        post(
            api,
            "tasks",
            {
                "owner": "ops",
                "title": f"Check part {n} with a Tico model",
                "body": "Read the plan.",
                "check_model": "grok",
                "parent": parent["id"],
            },
            turn["token"],
        )
    with api.app.state.store.transaction() as c:  # named as a Tico model, as a company on the Gateway names it
        c.execute("UPDATE task_check_models SET runtime='pi',model='tico-medium-1.0',harness='pi',effort=''")
    finish(api, machine, turn)
    hold_gateway(api, H.shift(HIT, seconds=60))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET capacity=16 WHERE runner_id=?", (machine["runner_id"],))

    def checks():
        got = []
        while (one := claim(api, machine)) is not None:
            got.append(one)
        return [one for one in got if (one.get("task") or {}).get("check")]

    assert checks() == [], "no Tico check starts while the Gateway is held"
    at(monkeypatch, H.shift(HIT, seconds=61), api)
    post(api, "runners/heartbeat", {**beat, "readiness": report}, token=machine["token"])
    started = checks()
    assert len(started) == 1, "one probe on the Gateway, not one per check"
    assert gateway_hold(api)["probe_job"] == started[0]["job_id"]


def test_a_turn_settles_the_gateway_by_what_it_ran_not_the_model_the_bot_moved_to(api, monkeypatch):
    # The bot runs pi on its own keys; while that turn is out it is moved to a Tico model. The turn's success says
    # nothing about the Gateway, whose hold stands.
    gateway_env(api, monkeypatch)
    at(monkeypatch, HIT)
    with api.app.state.store.transaction() as c:
        BS.write(c, "ops", {**BS.read(c, "ops"), "runtime": "pi", "harness": "pi", "model": "kimi-k3"})
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Task", "body": "Please do it."})
    hold_gateway(api, H.shift(HIT, seconds=600))
    turn = claim(api, machine, "ops")
    assert turn and turn["config"]["model"] == "kimi-k3"
    with api.app.state.store.transaction() as c:
        BS.write(c, "ops", {**BS.read(c, "ops"), "model": "tico-medium-1.0"})
    done(api, machine, turn)
    assert gateway_hold(api)["until"] == H.shift(HIT, seconds=600)


def test_an_older_servers_gateway_waits_become_the_gateways_hold(api, monkeypatch):
    # rc.1 kept the Gateway's cap and address block only in the bot's status cooldown, and an outage in a per-bot row.
    import importlib
    import json

    from backend.store import encode

    at(monkeypatch, HIT)
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        aid = H.new_id()
        c.execute(
            "INSERT INTO attempts(id,bot_id,runner_id,state,created_at,started_at,finished_at,result) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (aid, "ops", machine["runner_id"], "failed", HIT, HIT, HIT, encode({"limited": True})),
        )
        c.execute(
            "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,1,'error',?,?)",
            (aid, json.dumps({"error": GATEWAY_CAP, "limit": True}), HIT),
        )
        H.status_set(c, H.TICO_ACTOR, "ops", state="limited", focus="Gateway cap reached")
        c.execute(
            "INSERT INTO registry_metadata VALUES('gateway-unavailable:finance',?)",
            (json.dumps({"since": HIT, "until": H.shift(HIT, seconds=60)}),),
        )
        importlib.import_module("backend.migrations.20261117015737_gateway_waits_to_login_hold").up(c, H.parse_ts(HIT))
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key LIKE 'gateway-unavailable:%'").fetchone()
        # The upgrade's later migration then moves the login holds into `holds`.
        for statement in statements((DIRECTORY / "20261117071838_holds_usage_limit.sql").read_text()):
            c.execute(statement)
    hold = gateway_hold(api)
    # The cap's month, not the outage's minute: the later wait wins, and the month is weeks away.
    assert hold["until"] == "2026-11-01T00:00:01.000000Z" and hold["bot"] == "ops"
    ready(api, machine, ["ops"])
    gateway_env(api, monkeypatch)
    with api.app.state.store.transaction() as c:
        BS.write(c, "ops", {**BS.read(c, "ops"), "runtime": "pi", "harness": "pi", "model": "tico-medium-1.0"})
    post(api, "tasks", {"owner": "ops", "title": "Task", "body": "Please do it."})
    assert claim(api, machine, "ops") is None
