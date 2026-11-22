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
        if destination.startswith("long:"):  # the provider's own spelling of it is much longer
            destination = "inside:" + "x" * 600
        return ("internal" if destination.startswith("inside:") else "external"), destination

    def dispatch(self, store, row):
        self.calls.append(row["id"])
        answer = self.answers.pop(0) if self.answers else {"provider_id": "p-" + row["id"][:4]}
        if callable(answer):  # what happens while the provider is being called
            answer = answer()
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


def test_an_unknown_row_never_goes_back_in_line_and_a_person_settles_it(api, store, fake):
    """Sol S12: the worker that made the attempt may still reach the provider, so nothing requeues an unknown row: a
    reconcile settles only from the provider's record, and otherwise a person says what they found."""
    row, _ = add(store)
    fake.answers = [RuntimeError("connection reset")]
    assert OA.attempt(store, OA.claim(store)) == "unknown"
    fake.found = OA.Settled("queued")  # "no record of it" proves nothing about a paused worker
    OA.reconcile_pass(store)
    assert row_of(store, row["id"])["state"] == "unknown" and OA.claim(store, later(3600)) is None
    cancel = api.post(f"/api/v2/outbound-actions/{row['id']}/cancel", json={}, headers=headers())
    assert cancel.status_code == 409  # it may have gone: nobody says it did not
    path = f"/api/v2/outbound-actions/{row['id']}/confirm-sent"
    assert api.post(path, json={}, headers=headers("ben-test")).status_code == 404  # not his to say
    settled = api.post(path, json={}, headers=headers()).json()["action"]
    assert settled["state"] == "done" and fake.calls == [row["id"]]


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


def leaves(store, pid):
    with store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in people["people"]:
            if person["id"] == pid:
                person["hidden"] = True
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))


def ben_leaves(store):
    leaves(store, "ben")


def approved_by_ben(store):
    """An outside action, OK'd by Ops's manager Ben."""
    make_ben_ops_manager(store)
    row, _ = add(store, destination="https://partner.example/hook")
    with store.transaction() as c:
        rid = OA.get(c, row["id"])["approval_id"]
        shown = task_asks.show(c, "human:ben", rid, OA.content(row), own_click=True)
        task_asks.give_ok(c, "human:ben", rid, shown, own_click=True, ctx=task_asks.Context())
    return row, rid


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


def test_a_used_ok_whose_giver_left_does_not_carry_a_retry_and_its_task_closes(store, fake):
    row, rid = approved_by_ben(store)
    fake.answers = [OA.SafeRejection(0, "ratelimited")]
    assert OA.attempt(store, OA.claim(store)) == "requeued"
    ben_leaves(store)
    assert OA.attempt(store, OA.claim(store, later(1))) == "waiting_approval"
    assert fake.calls == [row["id"]]
    with store.read() as c:
        assert OA.get(c, row["id"])["approval_id"] != rid
        assert H.task(c, task_asks.task_of(c, rid))["status"] != "open"  # the superseded OK's task ended


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


def test_an_offset_send_after_is_stored_in_utc(store, fake):
    with store.transaction() as c:
        row, _ = OA.enqueue(
            c,
            store.settings,
            actor="bot:ops",
            channel="webhook",
            operation="post",
            destination="inside:ops",
            occurrence_key="tz",
            payload={},
            send_after="2026-11-22T09:00:00-08:00",
        )
    assert row["send_after"] == "2026-11-22T17:00:00.000000Z"


def test_an_ok_whose_giver_left_before_the_attempt_is_asked_again_of_someone_here(store, fake):
    make_ben_ops_manager(store)
    row, _ = add(store, destination="https://partner.example/hook")
    with store.transaction() as c:
        rid = OA.get(c, row["id"])["approval_id"]
        shown = task_asks.show(c, "human:ben", rid, OA.content(row), own_click=True)
        task_asks.give_ok(c, "human:ben", rid, shown, own_click=True, ctx=task_asks.Context())
    ben_leaves(store)
    assert OA.attempt(store, OA.claim(store)) == "waiting_approval" and fake.calls == []
    with store.read() as c:
        again = OA.get(c, row["id"])["approval_id"]
        assert again != rid and task_asks.grant(c, again)["asked_of_id"] == "human:ana"


def test_a_list_row_carries_a_bounded_error(api, store, fake):
    row, _ = add(store)
    fake.answers = [OA.SafeRejection(3600, "x" * 5000)]
    OA.attempt(store, OA.claim(store))
    item = get(api, "outbound-actions")["items"][0]
    assert item["id"] == row["id"] and len(item["last_error"]) == 300


def stalled(store, answer):
    """The worker stalls in the provider call and is taken for dead (the row goes unknown), then the answer comes."""

    def meanwhile():
        OA.recover(store, later(OA.STALE + 1))
        return answer

    return meanwhile


def test_a_late_receipt_settles_an_unknown_row_done(store, fake):
    row, _ = add(store)
    fake.answers = [stalled(store, {"provider_id": "p-late"})]
    assert OA.attempt(store, OA.claim(store)) == "done"
    after = row_of(store, row["id"])
    assert after["state"] == "done" and json.loads(after["receipt"]) == {"provider_id": "p-late", "late": True}


def test_a_late_certain_refusal_settles_an_unknown_row_failed(store, fake):
    row, _ = add(store)
    fake.answers = [stalled(store, OA.Refused("no such issue"))]
    assert OA.attempt(store, OA.claim(store)) == "failed"
    assert row_of(store, row["id"])["state"] == "failed"


def test_the_destination_the_adapter_resolves_is_bounded_too(store, fake):
    with pytest.raises(OA.Problem) as refused:
        add(store, destination="long:x")
    assert refused.value.status == 422


def test_an_unknown_outcome_leaves_its_ok_task_open_until_it_is_settled(api, store, fake):
    row, rid = approved_by_ben(store)
    fake.answers = [RuntimeError("connection reset")]
    assert OA.attempt(store, OA.claim(store)) == "unknown"
    with store.read() as c:
        task = task_asks.task_of(c, rid)
        assert H.task(c, task)["status"] == "open"
    api.post(f"/api/v2/outbound-actions/{row['id']}/confirm-sent", json={}, headers=headers())
    with store.read() as c:
        assert H.task(c, task)["status"] == "done"


def test_an_ok_nobody_here_can_renew_blocks_the_action(store, fake):
    row, _ = approved_by_ben(store)
    ben_leaves(store)
    leaves(store, "ana")  # the default person is gone too
    assert OA.attempt(store, OA.claim(store)) == "blocked" and fake.calls == []
    assert row_of(store, row["id"])["last_error"] == OA.NOBODY_TO_ASK


def test_enqueue_bounds_what_a_list_shows(store, fake):
    with pytest.raises(OA.Problem) as refused:
        add(store, destination="inside:" + "x" * 600)
    assert refused.value.status == 422 and "destination" in refused.value.detail


def test_no_channel_ships_yet_so_every_action_is_refused(store):
    assert OA.ADAPTERS == {}
    with pytest.raises(OA.Problem) as refused:
        add(store)
    assert refused.value.status == 422 and "no sender" in refused.value.detail


def test_a_late_safe_rejection_ends_an_unknown_row_failed(store, fake):
    row, _ = add(store)
    fake.answers = [stalled(store, OA.SafeRejection(30, "ratelimited"))]
    assert OA.attempt(store, OA.claim(store)) == "failed"
    assert row_of(store, row["id"])["state"] == "failed"


def test_only_the_person_themselves_confirms_an_unknown_action_went(api, store, fake):
    row, _ = approved_by_ben(store)
    fake.answers = [RuntimeError("connection reset")]
    assert OA.attempt(store, OA.claim(store)) == "unknown"
    path = f"/api/v2/outbound-actions/{row['id']}/confirm-sent"
    assert api.post(path, json={}, headers=headers("ben-via-agent-ops")).status_code == 403
    assert api.post(path, json={}, headers=headers("ben-test")).json()["action"]["state"] == "done"
