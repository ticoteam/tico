"""The Bookkeeper's Watch and weekly rules digest (backend/bookkeeper.py): silent unless a check finds something new;
news opens one private Watch task, once; the digest's precheck skips a week with no new posting rule. Synthetic fixtures."""
from datetime import timedelta

from backend import bookkeeper as BK
from backend import system_routines as SR
from backend.finance import reads as FR
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_bookkeeper import built, checks, ensure
from backend.tests.test_onboarding import environment  # noqa: F401

FINDING = {"kind": "duplicate_charge", "key": "duplicate_charge:f9", "stamp": {"amount": 1200, "gap": 1},
           "label": "Acme: 12.00 charged again 1 day(s) after"}


def test_the_watch_is_silent_with_nothing_to_watch(environment):
    api = built(environment)
    ensure(api)
    with api.app.state.store.transaction() as c:
        made = BK.watch(c, api.app.state.auth, H.now())
    assert made["items"] == 0 and made["task"] is None
    assert checks(api) == []


def test_news_opens_one_private_watch_task_and_the_same_news_is_silent(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(FINDING)])
    with store.transaction() as c:
        first = BK.watch(c, api.app.state.auth, H.now())
    assert first["items"] == 1 and first["state"] == "opened"
    (task,) = checks(api)
    assert task["title"] == "Watch (1)" and task["private"] == 1 and "duplicate_charge:f9" in task["body"]
    with store.transaction() as c:
        again = BK.watch(c, api.app.state.auth, H.now())
    assert again["words"] == "Nothing new to watch" and len(checks(api)) == 1


def test_the_rules_digest_skips_a_week_with_no_new_rule_and_lists_the_new_ones_as_a_visible_task(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    with store.transaction() as c:
        assert BK.rules_digest(c, api.app.state.auth, H.now())["words"] == "No new posting rules"
    assert checks(api) == []
    rule = {"id": "rul_1", "name": "Acme Supplies to Supplies", "created_by": "bot:bookkeeper", "active": True}
    monkeypatch.setattr(FR, "new_posting_rules", lambda c, since: [rule])
    with store.transaction() as c:
        made = BK.rules_digest(c, api.app.state.auth, H.now())
    assert made["items"] == 1 and made["state"] == "opened"
    (task,) = checks(api)
    assert task["title"] == "Rules digest (1)" and task["private"] == 0 and "rul_1" in task["body"]


def test_the_watch_runs_daily_and_the_digest_weekly_on_their_own_cadence_and_neither_is_a_switch(environment,
                                                                                               monkeypatch):
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    scheduler = Scheduler(store, api.app.state.execution)
    calls: dict[str, int] = {"watch": 0, "digest": 0}
    real_watch, real_digest = BK.watch, BK.rules_digest

    def counted(name, real):
        def run(c, auth, at):
            calls[name] += 1
            return real(c, auth, at)
        return run
    monkeypatch.setattr(BK, "watch", counted("watch", real_watch))
    monkeypatch.setattr(BK, "rules_digest", counted("digest", real_digest))
    at = H.parse_ts(H.now())
    scheduler.tick(at)                                  # both run once: never run before
    scheduler.tick(at + timedelta(seconds=30))          # neither is due again
    scheduler.tick(at + timedelta(days=1, minutes=1))   # the watch is due a day on; the digest is a week on
    assert calls == {"watch": 2, "digest": 1}
    assert "bookkeeper_watch" not in SR.CONTROLS and "bookkeeper_rules_digest" not in SR.CONTROLS
