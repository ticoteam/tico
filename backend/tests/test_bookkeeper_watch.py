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
    assert task["title"] == "Rules digest (1)" and task["private"] == 1 and "rul_1" in task["body"]
    # Private: the company owner reads the digest (the guest), and a teammate who is not the owner does not.
    with store.read() as c:
        row = H.task(c, task["id"])
        assert H.task_private_readable(c, BK._owner(c, api.app.state.auth), row)
        assert not H.task_private_readable(c, "human:nobody", row)


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


def test_a_reviewed_finding_is_not_news_again_after_a_day_but_is_when_its_facts_change(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(FINDING)])
    with store.transaction() as c:
        assert BK.watch(c, api.app.state.auth, H.now())["items"] == 1
    later = H.shift(H.now(), hours=25)
    with store.transaction() as c:
        assert BK.watch(c, api.app.state.auth, later)["words"] == "Nothing new to watch"
    assert len(checks(api)) == 1
    changed = {**FINDING, "stamp": {"amount": 1500, "gap": 1}}
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(changed)])
    with store.transaction() as c:
        assert BK.watch(c, api.app.state.auth, H.shift(later, hours=1))["items"] == 1


NEWER = {"kind": "duplicate_charge", "key": "duplicate_charge:f10", "stamp": {"amount": 500, "gap": 2},
         "label": "Beta: 5.00 charged again 2 day(s) after"}


def test_a_finished_watch_task_is_not_listed_again_when_a_new_finding_arrives(environment, monkeypatch):
    """Finding A is on a Watch task that is finished. When B arrives, the new task lists only B: A was reviewed."""
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(FINDING)])
    with store.transaction() as c:
        assert BK.watch(c, api.app.state.auth, H.now())["items"] == 1
    (first,) = checks(api)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (first["id"],))
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(FINDING), dict(NEWER)])
    with store.transaction() as c:
        made = BK.watch(c, api.app.state.auth, H.shift(H.now(), hours=1))
    assert made["items"] == 1 and made["state"] == "opened", made
    (task,) = [t for t in checks(api) if t["id"] != first["id"]]
    assert task["title"] == "Watch (1)" and "duplicate_charge:f10" in task["body"], task
    assert "duplicate_charge:f9" not in task["body"], task["body"]


def test_an_open_watch_task_keeps_what_it_lists_and_adds_the_new_finding(environment, monkeypatch):
    """Still open, the task lists its own items (A) with the new one (B): an open item is not dropped, and a finding it
    never listed is not added."""
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(FINDING)])
    with store.transaction() as c:
        BK.watch(c, api.app.state.auth, H.now())
    (opened,) = checks(api)
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [dict(FINDING), dict(NEWER)])
    with store.transaction() as c:
        made = BK.watch(c, api.app.state.auth, H.shift(H.now(), hours=1))
    assert made["state"] == "updated", made
    (task,) = checks(api)
    assert task["id"] == opened["id"] and task["title"] == "Watch (2)", task
    assert "duplicate_charge:f9" in task["body"] and "duplicate_charge:f10" in task["body"], task["body"]


def _long(kind: str, n: int) -> dict[str, object]:
    """A finding with a long id and label, as a real one can be: fifty of them fill the body's bytes."""
    return {"kind": kind, "key": f"{kind}:{'7' * 60}-{n:03d}", "stamp": {"n": n}, "label": "L" * 160}


def _seen(api) -> set[str]:
    with api.app.state.store.read() as c:
        return {str(row[0]) for row in c.execute("SELECT key FROM bookkeeper_seen")}


def test_the_most_important_finding_is_named_first_and_one_the_body_cannot_hold_is_not_stamped_seen(environment,
                                                                                                    monkeypatch):
    """Grok's repro: 25 long duplicate charges and 25 long price rises fill the body's bytes before runway, the most
    important. Runway is still named, and a finding the body cannot hold is not stamped seen: it is news again once
    there is room for it."""
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    runway = {"kind": "runway", "key": "runway:acme", "stamp": {"cash": 9}, "label": "Acme: cash covers 9 days of burn"}
    rest = [_long("duplicate_charge", n) for n in range(25)] + [_long("price_increase", n) for n in range(25)]
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [runway] + rest)
    with store.transaction() as c:
        BK.watch(c, api.app.state.auth, H.now())
    (task,) = checks(api)
    assert "runway:acme" in task["body"]
    dropped = [d for d in rest if d["key"] not in task["body"]]
    assert dropped, "the body must not hold them all, or this does not test the cut"
    assert "runway:acme" in _seen(api)
    assert not any(d["key"] in _seen(api) for d in dropped)
    # The others clear: what the body dropped is listed on the next pass.
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: list(dropped))
    with store.transaction() as c:
        BK.watch(c, api.app.state.auth, H.shift(H.now(), hours=24))
    (task,) = checks(api)
    assert all(d["key"] in task["body"] for d in dropped)


def test_a_finding_past_a_kinds_line_limit_is_not_stamped_seen_and_is_listed_once_there_is_room(environment,
                                                                                               monkeypatch):
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    spend = [{"kind": "unusual_spend", "key": f"unusual_spend:{n:02d}", "stamp": {"n": n}, "label": f"Acme spend {n}"}
             for n in range(BK.PER_KIND + 1)]
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: list(spend))
    with store.transaction() as c:
        BK.watch(c, api.app.state.auth, H.now())
    (task,) = checks(api)
    over = spend[-1]["key"]
    assert over not in task["body"] and over not in _seen(api)
    monkeypatch.setattr(FR, "watch_findings", lambda c, viewer, at: [spend[-1]])
    with store.transaction() as c:
        BK.watch(c, api.app.state.auth, H.shift(H.now(), hours=24))
    (task,) = checks(api)
    assert over in task["body"]
