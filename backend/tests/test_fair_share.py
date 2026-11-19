"""Each API client's fair share (backend/fair_share.py): one token's burst gets 429 with Retry-After at once, while
runners and people in the app keep going."""

import pytest

from backend.auth import Identity
from backend.fair_share import Budget, FairShare
from backend.store import Problem
from backend.tests.helpers.api import headers, post, runner

TOKEN = Identity("human:ben", "human", via_token=True, token_label="agents", token_id="t1")
BOT = Identity("bot:ops", "bot", attempt_id="a1")
RUNNER = Identity("runner:r1", "runner", runner_id="r1")
BROWSER = Identity("human:ben", "human")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def share(token=Budget(2, 5), bot=Budget(3, 50)):
    clock = Clock()
    return FairShare({"token": token, "bot": bot}, clock), clock


def refusal(fair, who, path="/api/v2/tasks/t"):
    with pytest.raises(Problem) as caught:
        fair.admit(who, path)
    assert caught.value.status == 429 and caught.value.code == "rate_limited" and caught.value.retryable
    return caught.value


def test_a_token_past_its_requests_at_once_waits_a_second_and_gets_its_slot_back():
    fair, _ = share()
    held = [fair.admit(TOKEN, "/api/v2/tasks/t") for _ in range(2)]
    refused = refusal(fair, TOKEN)
    assert refused.extra["retry_after"] == 1 and refused.extra["window"] == "concurrent"
    assert "agents" in refused.detail
    fair.done(held[0])
    assert fair.admit(TOKEN, "/api/v2/tasks/t") is not None


def test_a_token_past_its_minute_waits_until_its_oldest_request_leaves_the_window():
    fair, clock = share()
    for _ in range(5):
        fair.done(fair.admit(TOKEN, "/api/v2/tasks/t"))
        clock.now += 10
    refused = refusal(fair, TOKEN)
    # The first request started 50 s ago: it leaves the minute in 10 s.
    assert refused.extra == {"limit": 5, "window": "minute", "retry_after": 10}
    clock.now += 10
    fair.done(fair.admit(TOKEN, "/api/v2/tasks/t"))


def test_a_refused_request_counts_toward_nothing():
    fair, clock = share(token=Budget(0, 2))
    fair.admit(TOKEN, "/x"), fair.admit(TOKEN, "/x")
    for _ in range(20):
        refusal(fair, TOKEN)
    clock.now += 60
    fair.admit(TOKEN, "/x")


def test_runners_and_people_in_the_app_have_no_budget_and_bots_have_their_own():
    fair, _ = share(token=Budget(1, 1), bot=Budget(1, 100))
    fair.admit(TOKEN, "/x")
    refusal(fair, TOKEN)
    for _ in range(50):
        assert fair.admit(RUNNER, "/api/v2/runners/heartbeat") is None
        assert fair.admit(BROWSER, "/api/v2/needs-you") is None
    # The bot is another client, and the same person's second token is another client too.
    held = fair.admit(BOT, "/x")
    refusal(fair, BOT)
    fair.done(held)
    other = Identity("human:ben", "human", via_token=True, token_label="laptop", token_id="t2")
    fair.admit(other, "/x")
    # The Assistant acting for Ben is a bot-sized client of its own; a service key is token-sized.
    assistant = Identity("human:ben", "human", via_token=True, token_label="Assistant", via="assistant")
    fair.admit(assistant, "/x")
    assert "Assistant" in refusal(fair, assistant).detail
    service = Identity("service:k1", "service", token_label="crm")
    fair.admit(service, "/x")
    assert "service key" in refusal(fair, service).detail


def test_a_wait_on_another_system_takes_no_slot_but_counts_toward_the_minute():
    fair, _ = share(token=Budget(1, 3))
    fair.admit(TOKEN, "/x")
    assert fair.admit(TOKEN, "/api/v2/browser/commands") is None
    assert fair.admit(TOKEN, "/api/v2/gateway/images") is None
    assert refusal(fair, TOKEN, "/api/v2/browser/commands").extra["window"] == "minute"


def test_an_mcp_call_counts_only_as_the_requests_it_makes():
    fair, _ = share(token=Budget(1, 1))
    assert fair.admit(TOKEN, "/api/v2/mcp") is None
    held = fair.admit(TOKEN, "/api/v2/tasks/t")
    assert held is not None
    refusal(fair, TOKEN)


def test_zero_turns_a_limit_off():
    fair, _ = share(token=Budget(0, 0))
    assert all(fair.admit(TOKEN, "/x") is None for _ in range(500))


def test_a_token_burst_is_refused_with_retry_after_while_runners_and_the_app_go_on(api):
    """Over HTTP: a personal token past its minute gets 429 with Retry-After and the seconds in the body, before the
    route runs; the runner's heartbeat and a person in the app are answered as before."""
    with api.app.state.store.transaction() as c:
        from backend.personal_tokens import mint

        token = mint(c, "ben", "agents", 30, "human:ben")["token"]
    machine = runner(api, "ana")
    api.app.state.fair_share = FairShare({"token": Budget(4, 3), "bot": Budget(0, 0)})
    for _ in range(3):
        assert api.get("/api/v2/me", headers=headers(token)).status_code == 200
    refused = api.get("/api/v2/me", headers=headers(token))
    assert refused.status_code == 429
    wait = int(refused.headers["Retry-After"])
    error = refused.json()["error"]
    assert error["code"] == "rate_limited" and error["retryable"] and error["retry_after"] == wait and 1 <= wait <= 60
    for _ in range(5):
        post(api, "runners/heartbeat", {"version": "test", "platform": "test"}, token=machine["token"])
        assert api.get("/api/v2/me", headers=headers("ben-test")).status_code == 200
    # Every slot came back when its request ended, refused ones included.
    assert not api.app.state.fair_share.running


def test_a_token_with_its_slots_full_is_refused_until_one_ends(api):
    with api.app.state.store.transaction() as c:
        from backend.personal_tokens import mint

        issued = mint(c, "ben", "agents", 30, "human:ben")
    fair = api.app.state.fair_share = FairShare({"token": Budget(1, 0), "bot": Budget(0, 0)})
    who = Identity("human:ben", "human", via_token=True, token_label="agents", token_id=issued["id"])
    held = fair.admit(who, "/api/v2/tasks")
    refused = api.get("/api/v2/me", headers=headers(issued["token"]))
    assert refused.status_code == 429 and refused.headers["Retry-After"] == "1"
    fair.done(held)
    assert api.get("/api/v2/me", headers=headers(issued["token"])).status_code == 200
