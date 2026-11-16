"""A usage limit, seen from outside: what the claim hands out and what the bot shows, whatever stores the limit.

One table per entry point (a limit met, Try now, a connection change), each row a kind of limit: a provider's named
reset, a quota with no reset (the backoff), a model at capacity, and the Tico AI Gateway's cap, rate limit, credit and
outage. Every row checks the same contract: nothing starts before the limit's end, exactly one turn (the probe) starts
after it, and that turn's success lets the rest of the queue move and the bot stop showing Rate limited.
"""

import pytest

from backend import bot_settings as BS
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
