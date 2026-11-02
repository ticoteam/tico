"""Page monitors: the guard, who may watch, what a real change files, and that a fetch holds no transaction."""

import pytest

from backend import market_watch as W
from backend.store import H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401
from backend.tests.test_company import with_strategy_bot
from backend.tests.test_market import _librarian, _seed
from backend.tests.test_strategy import add_bet, publish, write

PAGE = "https://northwind.example/pricing"
BASE = "Plans\n" + "\n".join(f"Plan {n} includes feature number {n} for every team" for n in range(8))
CHANGED = BASE + "\nThe Pro plan now costs far more per seat each month"


def watch(api, url=PAGE, entity="company/northwind", kind="pricing", token="ana-test", expected=200):
    return post(api, "market/watches", {"entity_id": entity, "url": url, "kind": kind}, token=token, expected=expected)


def pages(monkeypatch, *texts):
    """Each read of any page returns the next text (the last one repeats)."""
    seen = list(texts)
    monkeypatch.setattr(W, "fetch_page", lambda url: seen.pop(0) if len(seen) > 1 else seen[0])


def check(api):
    return W.run_checks(api.app.state.store)


def counts(api):
    with api.app.state.store.read() as c:
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("market_insights", "market_evidence")} | {
            "page_events": c.execute("SELECT count(*) FROM market_events WHERE field LIKE 'page.%'").fetchone()[0]}


@pytest.fixture
def seeded(api, tmp_path):
    _seed(api, tmp_path)
    return api


@pytest.mark.parametrize("url", ["http://127.0.0.1/pricing", "http://localhost/x", "http://169.254.169.254/latest",
                                 "http://[::1]/", "ftp://northwind.example/x", "file:///etc/passwd",
                                 "https://user:pw@northwind.example/x"])
def test_an_internal_or_odd_address_is_refused_when_it_is_added(seeded, url):
    watch(seeded, url=url, expected=422)
    assert get(seeded, "market/watches")["watches"] == []


def test_a_name_that_resolves_to_a_private_address_is_blocked_when_read(seeded, monkeypatch):
    monkeypatch.setattr(W, "RESOLVER", lambda host, port: ["10.0.0.5"])
    watch(seeded)
    assert check(seeded) == {"read": 1, "changed": 0, "errors": 1}
    row = get(seeded, "market/watches")["watches"][0]
    assert row["last_status"] == "blocked" and row["checked"]
    assert counts(seeded)["market_insights"] == 0


def test_only_the_owner_and_the_librarian_watch_a_page(seeded):
    watch(seeded, token="ben-test", expected=403)
    _librarian(seeded)
    _, _, librarian = setup_attempt(seeded, "librarian")
    made = watch(seeded, token=librarian["token"])["watch"]
    assert made["created_by"] == "bot:librarian" and made["active"]
    post(seeded, "market/watches/check", {}, token="ben-test", expected=403)
    post(seeded, f"market/watches/{made['id']}/stop", {}, token="ben-test", expected=403)
    assert get(seeded, "market/watches", token="ben-test")["watches"][0]["id"] == made["id"]


def test_the_first_read_is_a_baseline_and_a_small_change_files_nothing(seeded, monkeypatch):
    watch(seeded)
    pages(monkeypatch, BASE, BASE + "\nNew!", BASE + "\nNew!")
    before = counts(seeded)
    assert check(seeded)["changed"] == 0
    assert check(seeded)["changed"] == 0           # fewer than 40 characters
    assert counts(seeded) == before


def test_small_edits_that_add_up_file_one_insight_on_the_check_that_crosses_the_line(seeded, monkeypatch):
    watch(seeded)
    first, second = BASE + "\nPro plan changes soon", BASE + "\nPro plan changes soon\nSeats cost more money"
    pages(monkeypatch, BASE, first, second)
    before = counts(seeded)
    assert check(seeded)["changed"] == 0
    assert check(seeded)["changed"] == 0           # 21 characters: under the line, and not the new baseline
    assert check(seeded)["changed"] == 1           # 44 against the baseline
    assert counts(seeded)["market_insights"] - before["market_insights"] == 1


def test_a_real_change_files_one_insight_one_evidence_and_one_event_once(seeded, monkeypatch):
    watch(seeded)
    pages(monkeypatch, BASE, CHANGED)
    before = counts(seeded)
    assert check(seeded)["changed"] == 0
    assert check(seeded)["changed"] == 1
    assert check(seeded)["changed"] == 0           # same text again: nothing new
    after = counts(seeded)
    assert {k: after[k] - before[k] for k in after} == {"market_insights": 1, "market_evidence": 1, "page_events": 1}
    with seeded.app.state.store.read() as c:
        evidence = H._rows(c.execute("SELECT source_kind, source_url, quote FROM market_evidence"))[-1]
        event = H._rows(c.execute("SELECT subject_kind, subject_id, field FROM market_events WHERE field LIKE 'page.%'"))[0]
        insight = H._rows(c.execute("SELECT kind, about, status FROM market_insights WHERE about = 'Northwind'"))[0]
    assert evidence["source_kind"] == "page-change" and evidence["source_url"] == PAGE
    assert "Pro plan now costs" in evidence["quote"]
    assert (event["subject_kind"], event["subject_id"], event["field"]) == ("entity", "company/northwind", "page.pricing")
    assert insight["kind"] == "property-change" and insight["status"] == "new"


def test_a_page_is_read_with_no_transaction_open(seeded, monkeypatch):
    watch(seeded)
    store = seeded.app.state.store
    open_at_fetch = []

    def fetch(url):
        open_at_fetch.append(len(store.writer.queue))
        return BASE
    monkeypatch.setattr(W, "fetch_page", fetch)
    check(seeded)
    assert open_at_fetch == [0]


def test_a_failing_page_marks_its_watch_and_the_others_still_run(seeded, monkeypatch):
    watch(seeded)
    watch(seeded, url="https://brightline.example/pricing", entity="company/brightline")

    def fetch(url):
        if "northwind" in url:
            raise RuntimeError("boom")
        return BASE
    monkeypatch.setattr(W, "fetch_page", fetch)
    assert check(seeded) == {"read": 2, "changed": 0, "errors": 1}
    by = {w["url"]: w for w in get(seeded, "market/watches")["watches"]}
    assert by[PAGE]["last_status"] == "error" and by["https://brightline.example/pricing"]["last_status"] == "ok"


def test_stopping_keeps_the_row_and_a_stopped_page_is_not_read(seeded, monkeypatch):
    made = watch(seeded)["watch"]
    stopped = post(seeded, f"market/watches/{made['id']}/stop", {})["watch"]
    assert stopped["active"] is False
    pages(monkeypatch, BASE)
    assert check(seeded)["read"] == 0
    assert [w["id"] for w in get(seeded, "market/watches")["watches"]] == [made["id"]]
    watch(seeded)                                   # the same page can be watched again once stopped


def test_the_same_page_twice_and_the_cap_on_active_watches_are_409(seeded):
    watch(seeded)
    assert watch(seeded, expected=409)["error"]["code"] == "exists"
    store = seeded.app.state.store
    with store.transaction() as c:
        for n in range(W.MAX_ACTIVE - 1):
            c.execute("INSERT INTO market_watches (id, entity_id, url, kind, active, created_at) VALUES (?,?,?,?,1,?)",
                      (f"w{n}", "company/northwind", f"https://northwind.example/p{n}", "other", H.now()))
    assert watch(seeded, url="https://northwind.example/over", expected=409)["error"]["code"] == "limit"


def test_a_page_change_trips_a_cited_bet_only_once_the_insight_is_applied(seeded, monkeypatch):
    with_strategy_bot(seeded)
    write(seeded, summary="Acme runs rentals with bots.", sections={"not": "No enterprise deals."})
    add_bet(seeded, cites=["company/northwind"], wrong_if={"kind": "thesis", "market_id": "company/northwind"})
    publish(seeded)
    watch(seeded)
    pages(monkeypatch, BASE, CHANGED)
    check(seeded)
    check(seeded)

    def bet_tasks():
        with seeded.app.state.store.read() as c:
            return [(t["title"], t["owner_id"]) for t in H._rows(c.execute("SELECT title, owner_id FROM tasks WHERE title LIKE 'Bet %'"))]
    assert bet_tasks() == []                       # a raw diff may be layout or wording: no fit check yet
    with seeded.app.state.store.read() as c:
        insight_id = c.execute("SELECT id FROM market_insights WHERE source_ref LIKE 'watch:%'").fetchone()[0]
    post(seeded, f"market/insights/{insight_id}/apply",
         {"evidence": {"source_kind": "page-change", "source_url": PAGE, "quote": "Pro plan now $12", "our_read": "A price rise."},
          "entity_id": "company/northwind", "summary": "Raised the Pro plan to $12."})
    assert bet_tasks() == [("Bet 1: may be wrong", "bot:goal-manager")]
