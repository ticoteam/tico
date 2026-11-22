"""The outbound gate (backend/outbound_actions.py, docs/outbound-actions.md): one queue over the outbox leases, a fenced
attempt before any provider call, unknown never retried on its own, safe rejections requeued, one policy checked at
enqueue and again at dispatch, and one OK from the person asked.

No channel ships an adapter yet: a fake `webhook` adapter stands in for a provider."""

import json

import pytest

from backend import outbound_actions as OA
from backend import sleep_idle
from backend import task_asks
from backend.store import H
from backend.tests.helpers.tasks_board import api, get, headers  # noqa: F401  (fixture)


class Crash(BaseException):
    """The worker process dies mid-call: nothing records an answer."""


class Fake:
    """A provider: `inside:` destinations are internal, anything else external. Answers come from `answers`."""

    def __init__(self):
        self.calls, self.answers, self.found = [], [], None

    def prepare(self, c, settings, actor, destination, payload):
        return ("internal" if destination.startswith("inside:") else "external"), destination

    def dispatch(self, store, row):
        self.calls.append(row["id"])
        answer = self.answers.pop(0) if self.answers else {"provider_id": "p-" + row["id"][:4]}
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def reconcile(self, store, row):
        return self.found


@pytest.fixture
def fake(monkeypatch):
    provider = Fake()
    adapter = OA.Adapter("webhook", provider.prepare, provider.dispatch, provider.reconcile)
    monkeypatch.setitem(OA.ADAPTERS, "webhook", adapter)
    return provider


@pytest.fixture
def store(api):
    return api.app.state.store


def add(store, key="k1", destination="inside:ops", payload=None, actor="bot:ops"):
    with store.transaction() as c:
        return OA.enqueue(
            c,
            store.settings,
            actor=actor,
            channel="webhook",
            operation="post",
            destination=destination,
            occurrence_key=key,
            payload=payload or {"n": 1},
            metadata={"for": "the test"},
        )


def row_of(store, action_id):
    with store.read() as c:
        return OA.get(c, action_id)


def set_policy(store, settings):
    with store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (OA.POLICY_KEY, OA._dump(settings)),
        )


def later(seconds):
    return H.shift(H.now(), seconds=seconds)


def test_a_worker_whose_claim_lapsed_cannot_call_the_provider(store, fake):
    row, _ = add(store)
    first = OA.claim(store)
    future = later(OA.STALE + 1)
    assert OA.recover(store, future) == 1  # never attempted: back in line
    second = OA.claim(store, future)
    assert second.id == first.id and second.token != first.token
    assert OA.attempt(store, first) == "lost" and fake.calls == []
    assert OA.attempt(store, second) == "done" and fake.calls == [row["id"]]


def test_the_same_claim_replayed_after_its_attempt_calls_nothing(store, fake):
    row, _ = add(store)
    claimed = OA.claim(store)
    fake.answers = [Crash()]
    with pytest.raises(Crash):
        OA.attempt(store, claimed)
    assert OA.attempt(store, claimed) == "lost"
    assert fake.calls == [row["id"]]


def test_a_crash_after_the_attempt_is_unknown_and_never_requeued(store, fake):
    row, _ = add(store)
    fake.answers = [Crash()]
    with pytest.raises(Crash):
        OA.attempt(store, OA.claim(store))
    future = later(OA.STALE + 1)
    OA.recover(store, future)
    after = row_of(store, row["id"])
    assert (after["state"], after["claim_token"]) == ("unknown", None) and after["provider_attempted_at"]
    OA.reconcile_pass(store)  # the provider cannot tell: still unknown
    assert row_of(store, row["id"])["state"] == "unknown"
    assert OA.claim(store, H.shift(future, days=1)) is None and fake.calls == [row["id"]]


def test_a_reconcile_that_finds_it_never_went_puts_it_back_in_line(store, fake):
    row, _ = add(store)
    fake.answers = [RuntimeError("connection reset")]
    assert OA.attempt(store, OA.claim(store)) == "unknown"
    fake.found = OA.Settled("queued")
    OA.reconcile_pass(store)
    after = row_of(store, row["id"])
    assert (after["state"], after["provider_attempted_at"]) == ("queued", None)
    assert OA.attempt(store, OA.claim(store, later(1))) == "done"


def test_a_crash_before_the_attempt_is_requeued(store, fake):
    row, _ = add(store)
    OA.claim(store)  # the worker dies before the fenced write
    future = later(OA.STALE + 1)
    OA.recover(store, future)
    after = row_of(store, row["id"])
    assert (after["state"], after["provider_attempted_at"]) == ("queued", None)
    assert OA.attempt(store, OA.claim(store, future)) == "done"


def test_a_rate_limit_requeues_with_the_marker_cleared_and_its_wait_kept(store, fake):
    row, _ = add(store)
    fake.answers = [OA.SafeRejection(30, "ratelimited")]
    assert OA.attempt(store, OA.claim(store)) == "requeued"
    after = row_of(store, row["id"])
    assert (after["state"], after["provider_attempted_at"], after["attempts"]) == ("queued", None, 1)
    assert after["last_error"] == "ratelimited" and after["next_attempt_at"] > later(29)
    assert OA.claim(store) is None
    assert OA.attempt(store, OA.claim(store, later(31))) == "done"
    done = row_of(store, row["id"])
    assert done["payload"] == "" and json.loads(done["metadata"]) == {"for": "the test"}  # wiped, kept


def test_the_same_occurrence_key_answers_the_row_it_made(store, fake):
    first, created = add(store, key="release-42:jira")
    again, created_again = add(store, key="release-42:jira", payload={"n": 2})
    assert (created, created_again, again["id"]) == (True, False, first["id"])
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM outbound_actions").fetchone()[0] == 1


def test_the_floor_beats_the_default_and_is_checked_again_at_dispatch(store, fake):
    set_policy(store, {"webhook:external": "never"})
    blocked, _ = add(store, key="out", destination="https://partner.example/hook")
    assert blocked["state"] == "blocked"  # Never beats the outside default of Ask first
    row, _ = add(store)  # internal: on its own, queued
    assert row["state"] == "queued"
    set_policy(store, {"webhook:internal": "never"})  # the company says Never before it goes
    assert OA.attempt(store, OA.claim(store)) == "blocked"
    after = row_of(store, row["id"])
    assert fake.calls == [] and after["state"] == "blocked" and json.loads(after["payload"]) == {"n": 1}


def make_ben_ops_manager(store):
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")


def test_the_person_asked_sees_the_action_and_one_ok_survives_a_safe_retry(api, store, fake):
    make_ben_ops_manager(store)
    row, _ = add(store, destination="https://partner.example/hook")  # outside: Ask first, of Ops's manager
    assert row["state"] == "waiting_approval"
    get(api, "outbound-actions/" + row["id"], token="priya-test", expected=404)
    shown = get(api, "outbound-actions/" + row["id"], token="ben-test")
    assert shown["action"]["payload"] == {"n": 1} and shown["shown_hash"]
    approved = api.post(
        f"/api/v2/outbound-actions/{row['id']}/approve",
        json={"shown_hash": shown["shown_hash"]},
        headers=headers("ben-test"),
    ).json()
    assert approved["action"]["state"] == "queued"
    fake.answers = [OA.SafeRejection(0, "ratelimited")]
    assert OA.attempt(store, OA.claim(store)) == "requeued"
    assert OA.attempt(store, OA.claim(store, later(1))) == "done"  # the OK was for this action, which never happened
    with store.read() as c:
        assert task_asks.standing(c, row["approval_id"]) == task_asks.USED
    listed = get(api, "outbound-actions")["items"]
    assert [(i["id"], i["state"]) for i in listed] == [(row["id"], "done")]
    assert "payload" not in listed[0] and "metadata" not in listed[0] and listed[0]["approval_task_id"]


def test_a_used_ok_whose_giver_left_does_not_carry_a_retry(store, fake):
    make_ben_ops_manager(store)
    row, _ = add(store, destination="https://partner.example/hook")
    with store.transaction() as c:
        rid = OA.get(c, row["id"])["approval_id"]
        shown = task_asks.show(c, "human:ben", rid, OA.content(row), own_click=True)
        task_asks.give_ok(c, "human:ben", rid, shown, own_click=True, ctx=task_asks.Context())
    fake.answers = [OA.SafeRejection(0, "ratelimited")]
    assert OA.attempt(store, OA.claim(store)) == "requeued"
    with store.transaction() as c:  # Ben leaves the company
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in people["people"]:
            if person["id"] == "ben":
                person["hidden"] = True
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))
    assert OA.attempt(store, OA.claim(store, later(1))) == "waiting_approval"
    assert fake.calls == [row["id"]]


def test_only_a_bot_queues_and_only_an_admin_sets_the_policy(api, fake):
    body = {"channel": "webhook", "destination": "inside:x", "occurrence_key": "k", "payload": {}}
    assert api.post("/api/v2/outbound-actions", json=body, headers=headers()).status_code == 403
    assert get(api, "outbound-actions/policy")["effective"]["webhook:internal"] == "on_their_own"
    put = api.put(
        "/api/v2/outbound-actions/policy", json={"settings": {"webhook:external": "never"}}, headers=headers()
    )
    assert put.status_code == 200 and put.json()["effective"]["webhook:external"] == "never"
    member = api.put(
        "/api/v2/outbound-actions/policy", json={"settings": {"webhook:external": None}}, headers=headers("ben-test")
    )
    assert member.status_code == 403


def test_sleep_waits_for_a_held_action_and_stays_up_for_one_in_flight(store, fake):
    with store.transaction() as c:
        OA.enqueue(
            c,
            store.settings,
            actor="bot:ops",
            channel="webhook",
            operation="post",
            destination="inside:ops",
            occurrence_key="held",
            payload={},
            send_after=later(3600),
        )
    now = H.parse_ts(H.now()).timestamp()
    with store.read() as c:
        assert sleep_idle.busy(c, now) != "send_pending" and sleep_idle.later_sends(c, now)
    assert 3500 < OA.next_due(store) <= 3600
    add(store, key="now")
    OA.claim(store)
    with store.read() as c:
        assert sleep_idle.busy(c, now) == "send_pending"
