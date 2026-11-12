"""The Gateway self cap and usage meter belong to the key and address they were read or set for (SM-07): a result
for one Gateway target is no evidence about another. Real connection saves, rotation and budget actions; a fake
Gateway that keeps each key's self cap apart and sends nothing."""

import datetime
import io
import json

import pytest

from backend import gateway_connection as GC
from backend.auth import Identity
from backend.credentials import Vault
from backend.finance import gateway_sync as GS
from backend.finance import jobs
from backend.tests.helpers.finance_fixtures import OWNER, act, books, budget, finance_template, spend, store  # noqa: F401

KEY_A, KEY_B = "synthetic-gateway-key-A", "synthetic-gateway-key-B"
WHO = Identity(OWNER, "owner")
T1, T2, T3, T4, T5 = (f"2026-10-15T1{h}:00:00.000000Z" for h in range(3, 8))


class Gateway:
    """Keeps a self cap per (address, key). Every key has spent 2 dollars of a 100 dollar cap."""

    def __init__(self):
        self.caps, self.sent, self.fail, self.during = {}, [], set(), None

    def __call__(self, request, timeout):
        key = request.headers.get("Authorization", "").removeprefix("Bearer ")
        where = (request.full_url.split("/key")[0], key)
        self.sent.append((request.get_method(), where, request.full_url))
        if self.during:
            hook, self.during = self.during, None
            hook()
        if key in self.fail:
            response = io.BytesIO(b"{}")
            response.status = 503
            return response
        if request.get_method() == "POST" and request.full_url.endswith("/key/cap"):
            self.caps[where] = json.loads(request.data)["self_cap_usd"]
        if "/key/usage" in request.full_url:
            body = {"month": "2026-10", "rows": [], "as_of": "2026-10-15T00:00:00Z"}
        else:
            body = {"spent_usd": 2, "cap_usd": 100, "self_cap_usd": self.caps.get(where)}
        response = io.BytesIO(json.dumps(body).encode())
        response.status = 200
        return response

    def to(self, key):
        return [s for s in self.sent if s[1][1] == key]

    def cap(self, key, base="https://gateway.tico.team"):
        return self.caps.get((base, key))


@pytest.fixture
def gw(books, monkeypatch):  # noqa: F811
    monkeypatch.delenv(GC.ENV_KEY, raising=False)
    monkeypatch.delenv(GC.ENV_URL, raising=False)
    monkeypatch.setattr(books.settings, "gateway_key", "", raising=False)
    monkeypatch.setattr(books.settings, "gateway_url", "", raising=False)
    vault = Vault(books)
    with books.transaction() as c:
        ai = budget(c, "AI services")
        assert act(c, OWNER, "budget_update", {"budget_id": ai, "amount": 100}).status == "committed"
    fake = Gateway()

    class Run:
        gateway, budget_id = fake, ai

        def save(self, **body):
            with books.transaction() as c:
                GC.save(c, vault, WHO, {**body, "expected_revision": GC.revision(c)}, T1, books.settings)

        def cap(self, now):
            return GS.cap_step(books, vault, books.settings, opener=fake, now=now)

        def usage(self, now):
            return GS.usage_step(books, vault, books.settings, opener=fake, now=now)

        def budget(self, amount):
            with books.transaction() as c:
                assert act(c, OWNER, "budget_update", {"budget_id": ai, "amount": amount}).status == "committed"

        def cutoff(self):
            with books.transaction() as c:
                spend(c, "5100", 100)
                assert jobs.cap_wanted(c, T1)[0] == "cut"

    run = Run()
    run.vault, run.books = vault, books
    return run


def _cap_state(gw):
    with gw.books.read() as c:
        return jobs.cap_state(c)


def _fetch_state(gw):
    with gw.books.read() as c:
        return GS.fetch_state(c)


def _base():
    return GS._gateway().base_url("")


def test_a_key_saved_after_the_cutoff_is_capped_too(gw):
    gw.save(key=KEY_A)
    gw.cutoff()
    assert gw.cap(T1) == "pushed" and gw.gateway.cap(KEY_A, _base()) == 2
    gw.save(key=KEY_B)
    assert gw.cap(T2) == "pushed"
    assert gw.gateway.cap(KEY_B, _base()) == 2
    assert gw.cap(T3) == "steady"


def test_a_key_that_comes_back_after_a_raise_is_cleared(gw):
    gw.save(key=KEY_A)
    gw.cutoff()
    assert gw.cap(T1) == "pushed"
    gw.save(key=KEY_B)
    assert gw.cap(T2) == "pushed"
    gw.budget(1_000)
    assert gw.cap(T3) == "pushed" and gw.gateway.cap(KEY_B, _base()) is None
    gw.save(key=KEY_A)
    assert gw.cap(T4) == "pushed"
    assert gw.gateway.cap(KEY_A, _base()) is None
    assert gw.cap(T5) == "steady"


def test_a_key_change_sends_nothing_when_no_cap_was_ever_set(gw):
    gw.save(key=KEY_A)
    assert gw.cap(T1) == "steady"
    gw.save(key=KEY_B)
    assert gw.cap(T2) == "steady" and gw.gateway.sent == []


def test_a_new_address_for_the_same_key_is_capped_there(gw):
    gw.save(key=KEY_A)
    gw.cutoff()
    assert gw.cap(T1) == "pushed"
    gw.save(url="https://gateway-b.example.com", credentials="keep")
    assert gw.cap(T2) == "pushed"
    assert gw.gateway.cap(KEY_A, "https://gateway-b.example.com") == 2


def test_an_automatic_rotation_caps_the_new_key(gw, books, monkeypatch):  # noqa: F811
    monkeypatch.setattr(GC, "check_later", lambda *a, **k: None)
    gw.save(key=KEY_A)
    gw.cutoff()
    assert gw.cap(T1) == "pushed"
    with books.read() as c:
        record = GC._row(c)
    now = datetime.datetime(2026, 10, 15, 13, tzinfo=datetime.timezone.utc)
    assert GC._keep(books, gw.vault, record, {"key": KEY_B, "key_id": "gk_b"}, now, "gi_x", "gis_x", "rotated")
    assert gw.cap(T2) == "pushed"
    assert gw.gateway.cap(KEY_B, _base()) == 2


def test_a_new_environment_key_after_a_restart_is_capped(gw, monkeypatch):
    monkeypatch.setenv(GC.ENV_KEY, KEY_A)
    gw.cutoff()
    assert gw.cap(T1) == "pushed"
    monkeypatch.setenv(GC.ENV_KEY, KEY_B)  # the restart: same revision, another key
    assert gw.cap(T2) == "pushed"
    assert gw.gateway.cap(KEY_B, _base()) == 2


def test_a_reply_for_a_key_replaced_meanwhile_does_not_satisfy_the_new_key(gw):
    gw.save(key=KEY_A)
    gw.cutoff()
    gw.gateway.during = lambda: gw.save(key=KEY_B)  # B is saved while A's cap is on the wire
    assert gw.cap(T1) == "pushed" and gw.gateway.cap(KEY_A, _base()) == 2
    assert gw.cap(T2) == "pushed"
    assert gw.gateway.cap(KEY_B, _base()) == 2


def test_an_old_claims_late_reply_does_not_overwrite_a_newer_claims_result(gw):
    gw.save(key=KEY_A)
    gw.cutoff()

    def newer():  # A's claim goes stale; B is saved and capped before A's reply lands
        gw.save(key=KEY_B)
        assert gw.cap(T3) == "pushed"

    gw.gateway.during = newer
    assert gw.cap(T1) != "pushed"
    state = _cap_state(gw)
    assert state["pushed"] == "cut" and state["for"] == "settings:2@" + _base()  # B's result, not A's
    before = len(gw.gateway.sent)
    assert gw.cap(T4) == "steady" and len(gw.gateway.sent) == before


def test_a_failed_keys_wait_does_not_hold_up_its_replacement(gw):
    gw.save(key=KEY_A)
    gw.cutoff()
    gw.gateway.fail.add(KEY_A)
    assert gw.cap(T1) == "failed"
    assert gw.cap("2026-10-15T13:00:30.000000Z") == "waiting"
    gw.save(key=KEY_B)
    assert gw.cap("2026-10-15T13:00:40.000000Z") == "pushed"
    assert gw.gateway.cap(KEY_B, _base()) == 2


def test_a_failed_meter_reads_wait_does_not_hold_up_a_new_key_and_keeps_past_reads(gw):
    gw.save(key=KEY_A)
    assert gw.usage(T1) == "fetched"
    read_before = dict(_fetch_state(gw)["months"])
    gw.gateway.fail.add(KEY_A)
    later = "2026-10-16T13:00:00.000000Z"
    assert gw.usage(later) == "failed"
    assert gw.usage("2026-10-16T13:00:30.000000Z") == "steady"  # A waits out its retry
    gw.save(key=KEY_B)
    assert gw.usage("2026-10-16T13:00:40.000000Z") == "fetched"
    assert gw.gateway.to(KEY_B)
    assert set(read_before) <= set(_fetch_state(gw)["months"])


def test_a_new_keys_first_failure_waits_the_first_retry_not_the_old_keys_count(gw):
    gw.save(key=KEY_A)
    gw.cutoff()
    gw.gateway.fail.update({KEY_A, KEY_B})
    assert gw.cap("2026-10-15T13:00:00.000000Z") == "failed"
    assert gw.cap("2026-10-15T13:02:00.000000Z") == "failed"
    assert gw.cap("2026-10-15T13:08:00.000000Z") == "failed"  # A has failed three times: a 30 minute wait
    gw.save(key=KEY_B)
    assert gw.cap("2026-10-15T13:09:00.000000Z") == "failed"
    state = _cap_state(gw)
    assert state["tries"] == 1 and state["next_try_at"] == "2026-10-15T13:10:00.000000Z"


def test_a_new_environment_address_for_a_saved_key_is_capped_there(gw, monkeypatch):
    gw.save(key=KEY_A)
    gw.cutoff()
    assert gw.cap(T1) == "pushed"
    # A restart with a new address, same saved key. The trailing slash pins that the claim's target and the steady
    # read build the address alike: if they differed, every pass would send the cap again.
    monkeypatch.setenv(GC.ENV_URL, "https://gateway-b.example.com/")
    assert gw.cap(T2) == "pushed"
    assert gw.gateway.cap(KEY_A, "https://gateway-b.example.com") == 2
    assert gw.cap(T3) == "steady"
