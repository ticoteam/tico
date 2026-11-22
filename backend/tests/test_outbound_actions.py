"""The outbound gate (backend/outbound_actions.py, docs/outbound-actions.md): one queue over the outbox leases, a fenced
attempt before any provider call, unknown never retried on its own, safe rejections requeued, one policy checked at
enqueue and again at dispatch, and Slack posts handed to the gateway and settled from its answer.

A fake `webhook` adapter stands in for a provider; the Slack tests use the real gateway with a fake Slack."""

from types import SimpleNamespace

import pytest

from backend import outbound_actions as OA
from backend import slack_channels as SC
from backend import task_asks
from backend.store import H
from backend.tests.helpers.slack_gateway import MARKETING, gateway, hub, rows  # noqa: F401  (fixtures)
from backend.tests.helpers.tasks_board import api, get, headers  # noqa: F401  (fixture)


class Crash(BaseException):
    """The worker process dies mid-call: nothing records an answer."""


class Fake:
    """A provider: `inside:` destinations are internal, anything else external. Answers come from `answers`."""

    def __init__(self):
        self.calls, self.answers = [], []

    def prepare(self, c, settings, actor, destination, payload):
        return ("internal" if destination.startswith("inside:") else "external"), destination

    def dispatch(self, store, row):
        self.calls.append(row["id"])
        answer = self.answers.pop(0) if self.answers else {"provider_id": "p-" + row["id"][:4]}
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def fake(monkeypatch):
    provider = Fake()
    monkeypatch.setitem(OA.ADAPTERS, "webhook", OA.Adapter("webhook", provider.prepare, provider.dispatch))
    return provider


def add(store, key="k1", destination="inside:ops", payload=None):
    with store.transaction() as c:
        return OA.enqueue(
            c,
            store.settings,
            actor=H.TICO_ACTOR,
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


def test_a_worker_whose_claim_lapsed_cannot_call_the_provider(hub, fake):
    row, _ = add(hub)
    first = OA.claim(hub)
    future = later(OA.STALE + 1)
    assert OA.recover(hub, future) == 1  # never attempted: back in line
    second = OA.claim(hub, future)
    assert second.id == first.id and second.token != first.token
    assert OA.attempt(hub, first) == "lost" and fake.calls == []
    assert OA.attempt(hub, second) == "done" and fake.calls == [row["id"]]


def test_a_crash_after_the_attempt_is_unknown_and_never_requeued(hub, fake):
    row, _ = add(hub)
    fake.answers = [Crash()]
    claimed = OA.claim(hub)
    with pytest.raises(Crash):
        OA.attempt(hub, claimed)
    assert row_of(hub, row["id"])["provider_attempted_at"]
    future = later(OA.STALE + 1)
    OA.recover(hub, future)
    after = row_of(hub, row["id"])
    assert (after["state"], after["claim_token"]) == ("unknown", None)
    assert OA.claim(hub, H.shift(future, days=1)) is None
    assert fake.calls == [row["id"]]
    meta = OA._load(after["metadata"], {})
    assert meta["unknown_noted"] and "unknown_task" not in meta  # internal: a log line, no ask


def test_an_outside_unknown_gets_one_owner_ask(hub, fake):
    set_policy(hub, {"webhook:external": "on_their_own"})
    row, _ = add(hub, destination="https://partner.example/hook")
    fake.answers = [RuntimeError("connection reset")]
    assert OA.attempt(hub, OA.claim(hub)) == "unknown"
    meta = OA._load(row_of(hub, row["id"])["metadata"], {})
    with hub.read() as c:
        task = H.task(c, meta["unknown_task"])
    assert task and task["status"] == "open"
    OA.reconcile_pass(hub)  # nothing reconciles it, and the ask is not repeated
    assert OA._load(row_of(hub, row["id"])["metadata"], {})["unknown_task"] == meta["unknown_task"]


def test_a_crash_before_the_attempt_is_requeued(hub, fake):
    row, _ = add(hub)
    OA.claim(hub)  # the worker dies before the fenced write
    future = later(OA.STALE + 1)
    OA.recover(hub, future)
    after = row_of(hub, row["id"])
    assert (after["state"], after["provider_attempted_at"]) == ("queued", None)
    assert OA.attempt(hub, OA.claim(hub, future)) == "done"


def test_a_rate_limit_requeues_with_the_marker_cleared_and_its_wait_kept(hub, fake):
    row, _ = add(hub)
    fake.answers = [OA.SafeRejection(30, "ratelimited")]
    assert OA.attempt(hub, OA.claim(hub)) == "requeued"
    after = row_of(hub, row["id"])
    assert (after["state"], after["provider_attempted_at"], after["attempts"]) == ("queued", None, 1)
    assert after["last_error"] == "ratelimited" and after["next_attempt_at"] > later(29)
    assert OA.claim(hub) is None
    assert OA.attempt(hub, OA.claim(hub, later(31))) == "done"


def test_the_same_occurrence_key_answers_the_row_it_made(hub, fake):
    first, created = add(hub, key="release-42:slack")
    again, created_again = add(hub, key="release-42:slack", payload={"n": 2})
    assert (created, created_again, again["id"]) == (True, False, first["id"])
    assert rows(hub, "SELECT count(*) AS n FROM outbound_actions")[0]["n"] == 1


def test_the_floor_beats_the_default_and_is_checked_again_at_dispatch(hub, fake):
    set_policy(hub, {"email:external": "on_their_own"})
    with hub.transaction() as c:
        c.execute(
            "INSERT INTO email_suppressed_recipients VALUES(?,?,?,?)",
            ("ops@acme.example", "gone@out.example", "bounce", H.now()),
        )
        bounced = OA.decide(c, actor=H.TICO_ACTOR, channel="email", audience="external", destination="gone@out.example")
        fine = OA.decide(c, actor=H.TICO_ACTOR, channel="email", audience="external", destination="ok@out.example")
    assert (bounced["choice"], bounced["floor"], fine["choice"]) == ("never", True, "on_their_own")

    row, _ = add(hub)  # internal: on its own, queued
    assert row["state"] == "queued"
    set_policy(hub, {"webhook:internal": "never"})  # the company says Never before it goes
    assert OA.attempt(hub, OA.claim(hub)) == "blocked"
    after = row_of(hub, row["id"])
    assert fake.calls == [] and after["state"] == "blocked" and OA._load(after["payload"], {}) == {"n": 1}


def test_an_outside_action_waits_for_one_ok_that_survives_a_safe_retry(api, fake):
    store = api.app.state.store
    body = {"channel": "webhook", "destination": "https://partner.example/hook", "occurrence_key": "api-1"}
    made = api.post("/api/v2/outbound-actions", json={**body, "payload": {"n": 1}}, headers=headers()).json()
    action = made["action"]
    assert made["created"] and action["state"] == "waiting_approval" and action["approval_id"]
    again = api.post("/api/v2/outbound-actions", json={**body, "payload": {"n": 9}}, headers=headers()).json()
    assert (again["created"], again["action"]["id"]) == (False, action["id"])
    shown = get(api, "outbound-actions/" + action["id"])
    assert shown["action"]["payload"] == {"n": 1} and shown["shown_hash"]
    get(api, "outbound-actions/" + action["id"], token="ben-test", expected=404)
    approved = api.post(
        f"/api/v2/outbound-actions/{action['id']}/approve", json={"shown_hash": shown["shown_hash"]}, headers=headers()
    ).json()
    assert approved["action"]["state"] == "queued"
    fake.answers = [OA.SafeRejection(0, "ratelimited")]
    assert OA.attempt(store, OA.claim(store)) == "requeued"
    assert OA.attempt(store, OA.claim(store, later(1))) == "done"  # the OK was for this action, which never happened
    with store.read() as c:
        assert task_asks.standing(c, action["approval_id"]) == task_asks.USED
    listed = get(api, "outbound-actions")["items"]
    assert [(i["id"], i["state"]) for i in listed] == [(action["id"], "done")] and "payload" not in listed[0]
    refused = api.post(f"/api/v2/outbound-actions/{action['id']}/cancel", json={}, headers=headers())
    assert refused.status_code == 409


def test_the_company_policy_is_read_by_all_and_set_by_an_admin(api, fake):
    assert get(api, "outbound-actions/policy")["effective"]["slack:internal"] == "on_their_own"
    put = api.put(
        "/api/v2/outbound-actions/policy", json={"settings": {"webhook:external": "never"}}, headers=headers()
    )
    assert put.status_code == 200, put.text
    put = put.json()
    assert put["settings"] == {"webhook:external": "never"} and put["effective"]["webhook:external"] == "never"
    member = api.put(
        "/api/v2/outbound-actions/policy", json={"settings": {"webhook:external": None}}, headers=headers("ben-test")
    )
    assert member.status_code == 403
    made = api.post(
        "/api/v2/outbound-actions",
        json={"channel": "webhook", "destination": "https://x.example", "occurrence_key": "k", "payload": {}},
        headers=headers("ben-test"),
    ).json()["action"]
    assert made["state"] == "blocked"
    canceled = api.post(f"/api/v2/outbound-actions/{made['id']}/cancel", json={}, headers=headers("ben-test")).json()
    assert canceled["action"]["state"] == "canceled"


# ----------------------------------------------------------------------------------------------------- Slack
def slack_post(store, key="slack:1", text="Shipped in v1.2"):
    with store.transaction() as c:
        SC.add(c, store.settings, SimpleNamespace(actor="human:ana"), MARKETING, name="marketing")
        row, _ = OA.enqueue(
            c,
            store.settings,
            actor=H.TICO_ACTOR,
            channel="slack",
            operation="reply",
            destination=MARKETING,
            occurrence_key=key,
            payload={"bot": "cmo", "text": text},
            metadata={"release": "v1.2"},
        )
    return row


def test_an_internal_slack_post_is_handed_to_the_gateway_and_settles_from_its_answer(gateway):
    store = gateway.store
    row = slack_post(store)
    assert (row["audience"], row["state"]) == ("internal", "queued")
    assert OA.attempt(store, OA.claim(store)) == "handed_off"
    handed = row_of(store, row["id"])
    mid = OA._load(handed["receipt"], {})["message_id"]
    with store.read() as c:
        refs = H.message(c, mid)["refs"]
    assert (refs["outbound_action"], refs["occurrence_key"]) == (row["id"], "slack:1")
    OA.recover(store, later(OA.STALE + 1))  # handed off: recovery leaves it to the gateway
    assert row_of(store, row["id"])["state"] == "attempting"
    gateway.deliver()
    done = row_of(store, row["id"])
    assert done["state"] == "done" and OA._load(done["receipt"], {})["slack_ts"]
    assert done["payload"] == "" and OA._load(done["metadata"], {})["release"] == "v1.2"  # payload wiped, metadata kept
    assert [p["text"].split("\n")[0] for p in gateway.slack.posts] == ["Shipped in v1.2"]


def test_an_uncertain_slack_post_is_unknown_and_logged_without_an_ask(gateway):
    store = gateway.store
    row = slack_post(store)
    OA.attempt(store, OA.claim(store))
    gateway.slack.failures = [RuntimeError("socket closed")]
    gateway.deliver()
    after = row_of(store, row["id"])
    meta = OA._load(after["metadata"], {})
    assert after["state"] == "unknown" and meta["unknown_noted"] and "unknown_task" not in meta


def test_a_lapsed_claim_hands_nothing_to_the_gateway(gateway):
    store = gateway.store
    row = slack_post(store)
    claimed = OA.claim(store)
    with store.transaction() as c:  # the fenced write, then the worker pauses past its claim
        c.execute("UPDATE outbound_actions SET provider_attempted_at=? WHERE id=?", (H.now(), row["id"]))
    OA.recover(store, later(OA.STALE + 1))
    stale = {**row_of(store, row["id"]), "claim_token": claimed.token}
    with pytest.raises(OA.LostClaim):
        OA.ADAPTERS["slack"].dispatch(store, stale)
    assert rows(store, "SELECT count(*) AS n FROM slack_posts")[0]["n"] == 0
    OA.reconcile_pass(store)  # nothing was handed off, so it may go again
    assert row_of(store, row["id"])["state"] == "queued"
