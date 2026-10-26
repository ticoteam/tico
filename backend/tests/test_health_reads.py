"""The health page reads each table once for every bot, and says exactly what it said when it read them one by one."""
import contextlib

import pytest

from backend import health as health_module, read_cache
from backend.store import H, encode
from backend.tests.test_api import api, assign, headers, ready  # noqa: F401
from backend.tests.test_run_backoff import hold_bot, setup
from backend.tests.test_status_reads import busy


@pytest.fixture(autouse=True)
def fixed_minutes(monkeypatch):
    """The ages in the page's words come from the wall clock; the two readings compared must not straddle a minute."""
    monkeypatch.setattr(health_module, "quiet_minutes", lambda stamp: 2715)


def strain(api, slugs):
    """The bots' work waits long enough to count as stuck, and some are limited, over budget or backing off."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created='2026-10-24T10:00:00.000000Z' WHERE state='queued'")
        for n, slug in enumerate(slugs):
            if n % 3 == 0:
                H.status_set(c, H.KEEPER, slug, state="limited", focus="Hit its limit")
            if n % 4 == 1:
                c.execute("INSERT INTO bot_budget(bot,held_at,window,runs,budget) VALUES(?,?,'hour',301,300)",
                          (slug, H.now()))
            if n % 4 == 2:
                c.execute("INSERT INTO run_backoff(bot,fails,wait_until) VALUES(?,2,?)",
                          (slug, H.shift(H.now(), seconds=600)))
            if n % 5 == 3:
                c.execute("INSERT INTO usage_limits(bot,daily_usd) VALUES(?,0.000001)", (slug,))
            if n % 6 == 4:
                c.execute("INSERT INTO bot_mail_settings(bot,outbound_send,forward_to_json,updated,updated_by) "
                          "VALUES(?,1,'[\"a@acme.example\"]',?,'human:ana')", (slug, H.now()))


def ask_for_mail(api, slugs):
    """Their bot.yaml asks to send without approval, and some to forward to more addresses than a person allowed."""
    with api.app.state.store.transaction() as c:
        for row in c.execute("SELECT id,readiness_json FROM runners").fetchall():
            document = H._json(row["readiness_json"], {}) or {}
            for n, slug in enumerate(slugs):
                document.setdefault("bots", {}).setdefault(slug, {})["mail_request"] = {
                    "outbound_send": n % 2 == 0, "forward_to": ["a@acme.example", f"{slug}@acme.example"]}
            c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (encode(document), row["id"]))


def team(api, monkeypatch, count):
    """One computer with ops held after five failed runs, a live run, and `count` active bots with waiting work."""
    machine = setup(api, monkeypatch, tasks=4)
    hold_bot(api, machine, monkeypatch)
    busy(api, machine, 0, count, live=2)
    strain(api, [f"busy-{n}" for n in range(count)])
    ask_for_mail(api, [f"busy-{n}" for n in range(count)])
    return machine


def idle(api, machine, first, count):
    """`count` more active bots on the computer with nothing queued, whose computer reports them ready."""
    store = api.app.state.store
    slugs = [f"idle-{n}" for n in range(first, first + count)]
    with store.transaction() as c:
        bots = {row["slug"]: {"name": row["slug"], "runtime": "fake", "status": row["state"]}
                for row in c.execute("SELECT slug,state FROM bots")}
        H.sync_registry(c, {**bots, **{s: {"name": s, "runtime": "fake", "status": "active"} for s in slugs}},
                        {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
        for slug in slugs:
            c.execute("INSERT INTO bot_config(bot,config_json,operator) VALUES(?,?,?)",
                      (slug, encode({"name": slug, "runtime": "fake"}), "ana"))
    for slug in slugs:
        assign(api, machine, slug)
    with store.read() as c:
        everyone = [row[0] for row in c.execute("SELECT slug FROM bots")]
    ready(api, machine, everyone)
    ask_for_mail(api, [s for s in everyone if s.startswith("busy-")])


def page(api, monkeypatch, token="ana-test"):
    """The page as `token` sees it, and the statements it took."""
    store, seen = api.app.state.store, []
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get("/api/v2/health", headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def unprimed(monkeypatch):
    """The page as it read before: no row of any table kept for the next lookup of the same row."""
    monkeypatch.setattr(read_cache, "cache", lambda c: None)


def test_health_shows_what_it_showed_before_and_reads_each_table_once_for_every_bot(api, monkeypatch):
    machine = team(api, monkeypatch, 12)
    primed, together = page(api, monkeypatch)
    # The fixture reaches the per-bot paths: waiting work the claim holds, a stopped bot, limits, budgets and mail asks.
    assert len(primed["stuck"]) >= 10 and primed["limited"] and primed["failures"] and primed["computers"]
    reasons = {row["reason"] for row in primed["stuck"]}
    assert {"run_hold", "budget", "backoff", "cooling_off"} <= reasons, reasons
    assert any(check["id"] == "mail_sending" for check in primed["checks"])
    with monkeypatch.context() as patched:
        unprimed(patched)
        plain, each = page(api, monkeypatch)
    assert primed == plain
    # What is left is the claim's own per-job rules for the bots whose work waits, not a read of each table per bot.
    assert together * 2 < each, (together, each)
    # Bots with nothing queued add no statements at all, where each used to add several.
    idle(api, machine, 0, 1)
    one = page(api, monkeypatch)[1]
    idle(api, machine, 1, 25)
    assert page(api, monkeypatch)[1] == one
    with monkeypatch.context() as patched:
        unprimed(patched)
        assert page(api, monkeypatch)[1] > each + 26


def test_health_for_a_person_who_manages_no_bot_matches_the_old_way_too(api, monkeypatch):
    team(api, monkeypatch, 8)
    for token in ("ben-test", "cara-test"):
        primed = page(api, monkeypatch, token)[0]
        with monkeypatch.context() as patched:
            unprimed(patched)
            assert page(api, monkeypatch, token)[0] == primed
