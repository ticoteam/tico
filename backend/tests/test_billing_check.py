"""Tico HQ's nightly billing check on the Health page (backend/billing_check.py, Billing 1b, spec test 15), against a
fake HQ answering in the shape of ticoteam/tico-services `GET /v1/staff/billing/check` (billing/ledger.py `last_check`)."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend import billing_check as B
from backend.scheduler import Scheduler
from backend.tests.test_api import api, get  # noqa: F401

KEY = "hq-read-synthetic-fixture-key-0042"
INSTALL = "5d0c1a52-3f4e-4b6a-9c1d-2e7f8a9b0c1d"
OTHER = "7e1f2b63-4a5f-4c7b-8d2e-3f8a9b0c1d2e"


def stamp(hours_ago=1):
    """A time in HQ's shape (billing/ledger.py `stamp`): UTC, microseconds, Z."""
    at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def check(findings=(), hours_ago=1):
    return {"check": {"checked_at": stamp(hours_ago), "accounts": 2,
                      "findings": [dict(f, severity="red") for f in findings]}}


def mismatch(account=INSTALL, kind="balance", subject=None, difference=400_000):
    return {"account_id": account, "kind": kind, "subject_id": subject, "difference_micros": difference}


class FakeHQ:
    """What HQ answers next (`answer`: a JSON body, a status code, or an exception) and every request it got."""

    def __init__(self, monkeypatch):
        self.answer, self.requests = check(), []
        monkeypatch.setattr(B, "TRANSPORT", httpx.MockTransport(self.handle))
        monkeypatch.setenv("TICO_HQ_URL", "https://hq.test")

    def handle(self, request):
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        if isinstance(self.answer, int):
            return httpx.Response(self.answer, json={"error": "unauthorized"})
        return httpx.Response(200, content=json.dumps(self.answer).encode(),
                              headers={"Content-Type": "application/json"})


@pytest.fixture
def hq(monkeypatch):
    monkeypatch.setenv(B.ENV_KEY, KEY)
    return FakeHQ(monkeypatch)


def read(api):
    return B.read_now(api.app.state.store, api.app.state.store.settings)


def billing(api, token="ana-test"):
    return [r for r in get(api, "health", token)["checks"] if r["id"].startswith("billing_check")]


def age(api, hours):
    """Move the stored failure back `hours`, as if HQ had not been readable that long."""
    with api.app.state.store.transaction() as c:
        stored = B._row(c)
        stored["failing_since"] = stamp(hours)
        c.execute("UPDATE registry_metadata SET value=? WHERE key=?", (json.dumps(stored), B.KEY))


def test_15_a_seeded_mismatch_is_a_red_health_item_naming_the_company_and_the_dollar_difference(api, hq):
    hq.answer = check([mismatch(difference=400_000),
                       mismatch(OTHER, "debit", "d-41", -12_500_001)])
    assert read(api) == "ok"
    items = billing(api)
    assert [i["status"] for i in items] == ["bad", "bad"]
    assert f"Company {INSTALL}: its cached balance is off by +$0.40" in items[0]["summary"]
    assert f"Company {OTHER}: debit d-41 is off by -$12.500001" in items[1]["summary"]
    assert all("Nothing was changed" in i["summary"] for i in items)
    # One read-only GET to HQ's staff route with the read key; nothing else was sent.
    (request,) = hq.requests
    assert (request.method, str(request.url)) == ("GET", "https://hq.test/v1/staff/billing/check")
    assert request.headers["Authorization"] == "Bearer " + KEY and request.content == b""
    # The owner's only: an admin is not told, and the key is in no answer.
    assert billing(api, "ben-test") == []
    assert KEY not in json.dumps(get(api, "health"))


def test_a_clean_check_shows_nothing_and_a_mismatch_fixed_later_clears_on_the_next_clean_read(api, hq):
    hq.answer = check()
    read(api)
    assert billing(api) == []
    hq.answer = check([mismatch()])
    read(api)
    assert len(billing(api)) == 1
    hq.answer = check()
    read(api)
    assert billing(api) == []


@pytest.mark.parametrize("answer,words", [
    ({"check": None}, "has no nightly billing check yet"),
    (check(hours_ago=B.STALE_HOURS + 1), "has missed a night, so the ledgers are not known to be clean"),
])
def test_a_check_that_never_ran_or_missed_a_night_is_red(api, hq, answer, words):
    hq.answer = answer
    read(api)
    (item,) = billing(api)
    assert item["status"] == "bad" and words in item["summary"]


@pytest.mark.parametrize("answer,words", [
    (401, "HQ refused the read-only key"),
    (httpx.ConnectError("down"), "HQ did not answer"),
    ({"check": {"checked_at": stamp(), "accounts": 1, "findings": [mismatch(difference=0.4)]}},
     "HQ's answer was not a billing check"),
])
def test_hq_not_readable_for_three_hours_is_red_and_a_blip_keeps_the_last_check(api, hq, answer, words):
    hq.answer = check([mismatch()])
    read(api)
    hq.answer = answer
    assert read(api) != "ok"
    (kept,) = billing(api)                                 # one failed read: the last good check still shows
    assert INSTALL in kept["summary"]
    age(api, B.UNREAD_HOURS)
    unread, kept = billing(api)
    assert unread["status"] == "bad" and words in unread["summary"] and "Could not read" in unread["summary"]
    assert INSTALL in kept["summary"]
    hq.answer = check()                                    # read again and clean: nothing left
    read(api)
    assert billing(api) == []


def test_past_twenty_mismatches_twenty_are_listed_and_one_item_counts_the_rest(api, hq):
    hq.answer = check([mismatch(subject="d-%02d" % n, kind="debit", difference=n) for n in range(1, 24)])
    assert read(api) == "ok"
    items = billing(api)
    assert len(items) == B.SHOWN + 1 == 21
    assert [i["summary"].split(" is off")[0] for i in items[:20]] == [f"Company {INSTALL}: debit d-%02d" % n
                                                                      for n in range(1, 21)]
    assert items[20]["id"] == "billing_check:more" and items[20]["summary"].startswith("3 more billing mismatches")
    assert not any("d-21" in i["summary"] or "d-23" in i["summary"] for i in items)


def test_an_overlapping_read_sends_no_second_get_and_hq_is_asked_outside_any_transaction(api, hq, monkeypatch):
    store, open_tx, seen = api.app.state.store, [], {}
    transaction = store.transaction

    def watched():
        open_tx.append(1)
        try:
            with transaction() as c:
                yield c
        finally:
            open_tx.pop()
    monkeypatch.setattr(store, "transaction", __import__("contextlib").contextmanager(watched))
    answer = hq.handle

    def handle(request):
        seen["in_transaction"] = bool(open_tx)
        seen["overlap"] = read(api)                         # a second read while the first waits on HQ
        return answer(request)
    monkeypatch.setattr(B, "TRANSPORT", httpx.MockTransport(handle))
    assert read(api) == "ok"
    assert seen == {"in_transaction": False, "overlap": None}
    assert len(hq.requests) == 1 and hq.requests[0].method == "GET"
    assert read(api) == "ok" and len(hq.requests) == 2      # the guard is let go once the read ends


def test_the_missed_and_unread_limits_fall_exactly_on_26_and_3_hours(api, hq):
    hq.answer = check()
    read(api)
    with api.app.state.store.transaction() as c:
        at = B._parse_at(B._row(c)["check"]["checked_at"])
        assert B.health(c, now=at + timedelta(hours=B.STALE_HOURS, seconds=-1)) == []
        (missed,) = B.health(c, now=at + timedelta(hours=B.STALE_HOURS))
        assert missed["id"] == "billing_check:missed" and B.STALE_HOURS == 26
        stored = B._row(c)
        B.record(c, "unreachable", None, stored["check"]["checked_at"])
        assert B.health(c, now=at + timedelta(hours=B.UNREAD_HOURS, seconds=-1)) == []
        (unread,) = B.health(c, now=at + timedelta(hours=B.UNREAD_HOURS))
        assert unread["id"] == "billing_check:unread" and B.UNREAD_HOURS == 3


def test_without_the_key_nothing_is_sent_and_nothing_is_shown(api, monkeypatch):
    monkeypatch.delenv(B.ENV_KEY, raising=False)
    calls = []
    monkeypatch.setattr(B, "TRANSPORT", httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)))
    assert read(api) is None
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert calls == [] and billing(api) == []


def test_the_hourly_tick_reads_hq_on_its_own_thread(api, hq, monkeypatch):
    threads = []
    monkeypatch.setattr(B, "read_later", lambda store, settings=None: threads.append(store))
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    at = datetime.now(timezone.utc)
    scheduler.tick(at)
    scheduler.tick(at + timedelta(minutes=30))
    assert threads == [api.app.state.store]                # once an hour, not every tick
    scheduler.tick(at + timedelta(hours=1))
    assert len(threads) == 2


@pytest.mark.parametrize("micros,text", [
    (0, "$0.00"), (1, "$0.000001"), (1_234_567_890_000, "$1,234,567.89"), (-12_500_001, "-$12.500001"),
])
def test_dollars_come_from_integer_micro_dollars(micros, text):
    assert B.dollars(micros) == text
    assert B.dollars(micros, sign=True) == ("+" + text if micros > 0 else text)


@pytest.mark.parametrize("bad", [0.4, True])
def test_dollars_refuse_anything_but_an_int(bad):
    with pytest.raises(TypeError):
        B.dollars(bad)
