"""Page monitors: the guard, who may watch, what a real change files, that a fetch holds no transaction, and the
Gateway's Signals (HQ is faked: no call leaves the test)."""

import json

import pytest

from backend import market_watch as W
from backend.store import H
from clients import gateway as GW
from backend.tests.helpers.api import get, post, setup_attempt  # noqa: F401
from backend.tests.helpers.company import with_strategy_bot
from backend.tests.helpers.market import _librarian, _seed
from backend.tests.helpers.strategy import add_bet, publish, write

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
        return {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("market_insights", "market_evidence")
        } | {
            "page_events": c.execute(
                "SELECT count(*) FROM history_events WHERE subject_type IN ('market_entity','market_edge','market_evidence','market_document') AND kind LIKE 'page.%'"
            ).fetchone()[0]
        }


@pytest.fixture
def seeded(api, tmp_path):
    _seed(api, tmp_path)
    return api


def test_an_internal_or_odd_address_is_refused_when_it_is_added(seeded):
    for url in [
        "http://127.0.0.1/pricing",
        "http://localhost/x",
        "http://169.254.169.254/latest",
        "http://[::1]/",
        "ftp://northwind.example/x",
        "file:///etc/passwd",
        "https://user:pw@northwind.example/x",
    ]:
        watch(seeded, url=url, expected=422)
        assert get(seeded, "market/watches")["watches"] == [], url


def test_a_name_that_resolves_to_a_private_address_is_blocked_when_read(seeded, monkeypatch):
    monkeypatch.setattr(W, "RESOLVER", lambda host, port: ["10.0.0.5"])
    watch(seeded)
    assert check(seeded) == {"read": 1, "changed": 0, "errors": 1}
    row = get(seeded, "market/watches")["watches"][0]
    assert row["last_status"] == "blocked" and row["checked_at"]
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
    assert check(seeded)["changed"] == 0  # fewer than 40 characters
    assert counts(seeded) == before


def test_small_edits_that_add_up_file_one_insight_on_the_check_that_crosses_the_line(seeded, monkeypatch):
    watch(seeded)
    first, second = BASE + "\nPro plan changes soon", BASE + "\nPro plan changes soon\nSeats cost more money"
    pages(monkeypatch, BASE, first, second)
    before = counts(seeded)
    assert check(seeded)["changed"] == 0
    assert check(seeded)["changed"] == 0  # 21 characters: under the line, and not the new baseline
    assert check(seeded)["changed"] == 1  # 44 against the baseline
    assert counts(seeded)["market_insights"] - before["market_insights"] == 1


def test_a_real_change_files_one_insight_one_evidence_and_one_event_once(seeded, monkeypatch):
    watch(seeded)
    pages(monkeypatch, BASE, CHANGED)
    before = counts(seeded)
    assert check(seeded)["changed"] == 0
    assert check(seeded)["changed"] == 1
    assert check(seeded)["changed"] == 0  # same text again: nothing new
    after = counts(seeded)
    assert {k: after[k] - before[k] for k in after} == {"market_insights": 1, "market_evidence": 1, "page_events": 1}
    with seeded.app.state.store.read() as c:
        evidence = H._rows(c.execute("SELECT source_kind, source_url, quote FROM market_evidence"))[-1]
        event = H._rows(
            c.execute(
                "SELECT substr(subject_type, 8) AS subject_kind, subject_id, kind AS field FROM history_events "
                "WHERE subject_type LIKE 'market%' AND kind LIKE 'page.%'"
            )
        )[0]
        insight = H._rows(c.execute("SELECT kind, about, status FROM market_insights WHERE about = 'Northwind'"))[0]
    assert evidence["source_kind"] == "page-change" and evidence["source_url"] == PAGE
    assert "Pro plan now costs" in evidence["quote"]
    assert (event["subject_kind"], event["subject_id"], event["field"]) == (
        "entity",
        "company/northwind",
        "page.pricing",
    )
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


def test_a_page_that_cannot_be_reached_logs_its_category_errno_and_reason(seeded, monkeypatch, caplog):
    def unreachable(ip, port, tls, host, timeout):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(W, "RESOLVER", lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(W, "OPENER", unreachable)
    watch(seeded)
    with caplog.at_level("WARNING", logger="tico.market_watch"):
        assert check(seeded) == {"read": 1, "changed": 0, "errors": 1}
    row = get(seeded, "market/watches")["watches"][0]
    assert row["last_status"] == "error" and "[Errno 101] Network is unreachable" in row["error"]
    logged = [r.getMessage() for r in caplog.records if r.name == "tico.market_watch"]
    assert any("northwind.example" in m and "network" in m and "[Errno 101]" in m for m in logged), logged


def test_stopping_keeps_the_row_and_a_stopped_page_is_not_read(seeded, monkeypatch):
    made = watch(seeded)["watch"]
    stopped = post(seeded, f"market/watches/{made['id']}/stop", {})["watch"]
    assert stopped["active"] is False
    pages(monkeypatch, BASE)
    assert check(seeded)["read"] == 0
    assert [w["id"] for w in get(seeded, "market/watches")["watches"]] == [made["id"]]
    watch(seeded)  # the same page can be watched again once stopped


def test_the_same_page_twice_and_the_cap_on_active_watches_are_409(seeded):
    watch(seeded)
    assert watch(seeded, expected=409)["error"]["code"] == "exists"
    store = seeded.app.state.store
    with store.transaction() as c:
        for n in range(W.MAX_ACTIVE - 1):
            c.execute(
                "INSERT INTO entity_watches (id, entity_id, url, kind, active, created_at) VALUES (?,?,?,?,1,?)",
                (f"w{n}", "company/northwind", f"https://northwind.example/p{n}", "other", H.now()),
            )
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
            return [
                (t["title"], t["owner_id"])
                for t in H._rows(c.execute("SELECT title, owner_id FROM tasks WHERE title LIKE 'Bet %'"))
            ]

    assert bet_tasks() == []  # a raw diff may be layout or wording: no fit check yet
    with seeded.app.state.store.read() as c:
        insight_id = c.execute("SELECT id FROM market_insights WHERE source_ref LIKE 'watch:%'").fetchone()[0]
    post(
        seeded,
        f"market/insights/{insight_id}/apply",
        {
            "evidence": {
                "source_kind": "page-change",
                "source_url": PAGE,
                "quote": "Pro plan now $12",
                "our_read": "A price rise.",
            },
            "entity_id": "company/northwind",
            "summary": "Raised the Pro plan to $12.",
        },
    )
    assert bet_tasks() == [("Bet 1: may be wrong", "bot:strategy-owner")]


COPYRIGHT = "© 2025 Northwind Analytics, Inc. All rights reserved. Terms and privacy apply to every visitor."


def test_a_copyright_year_change_files_nothing(seeded, monkeypatch):
    watch(seeded)
    # fetch_page returns normalized text; the copyright line is dropped there.
    pages(
        monkeypatch,
        W.normalize(BASE + "\n" + COPYRIGHT),
        W.normalize(BASE + "\n" + COPYRIGHT.replace("2025", "2026")),
    )
    before = counts(seeded)
    assert check(seeded)["changed"] == 0
    assert check(seeded)["changed"] == 0
    assert counts(seeded) == before


class Hq:
    """Tico HQ's Signals, faked: the watches it holds, the changes it has found and every call made to it."""

    def __init__(self):
        self.watches, self.changes, self.calls, self.refuse, self.n = {}, [], [], False, 0

    def install(self, monkeypatch):
        monkeypatch.setattr(GW, "signals_watches", self.listed)
        monkeypatch.setattr(GW, "signals_watch", self.register)
        monkeypatch.setattr(GW, "signals_end", self.end)
        monkeypatch.setattr(GW, "signals_changes", self.found)

    def listed(self, base, key, **_):
        self.calls.append(("list",))
        return 200, {"watches": [dict(w) for w in self.watches.values()]}, ""

    def register(self, base, key, kind, target, **_):
        self.calls.append(("register", kind, target))
        if self.refuse:
            return 422, {}, "invalid"
        for held in self.watches.values():
            if (held["kind"], held["target"]) == (kind, target):
                return 200, dict(held), ""  # the same watch again is the one HQ has
        self.n += 1
        ident = f"sw_{self.n:016x}"
        self.watches[ident] = {
            "id": ident,
            "kind": kind,
            "target": target,
            "label": None,
            "created_at": "2026-11-06T09:00:00Z",
            "checked_at": None,
            "changed_at": None,
            "status": "pending",
        }
        return 201, dict(self.watches[ident]), ""

    def end(self, base, key, watch_id, **_):
        self.calls.append(("end", watch_id))
        return (204, {}, "") if self.watches.pop(watch_id, None) else (404, {}, "not_found")

    def found(self, base, key, since="", limit=200, **_):
        self.calls.append(("changes", since))
        start = int(since[3:]) if since else 0
        rows = self.changes[start : start + limit]
        return 200, {"changes": [dict(r) for r in rows], "next": f"v1.{start + len(rows)}"}, ""

    def read(self, ident, status="ok"):
        self.watches[ident].update(status=status, checked_at="2026-11-06T09:30:00Z")

    def change(self, ident, removed="", added="", title="Pricing", url=PAGE, kind="page"):
        self.changes.append(
            {
                "id": f"sc_{len(self.changes) + 1:016x}",
                "watch_id": ident,
                "kind": kind,
                "target": url,
                "found_at": "2026-11-06T10:00:00Z",
                "title": title,
                "removed": removed,
                "added": added,
                "url": url,
            }
        )


@pytest.fixture
def signals(seeded, monkeypatch):
    """The Gateway connected to a faked HQ, with each sync run on the spot."""
    settings = seeded.app.state.store.settings
    settings.gateway_key, settings.gateway_url = "gw-synthetic", "https://hq.example/v1/gateway"
    hq = Hq()
    hq.install(monkeypatch)
    monkeypatch.setattr(W, "spawn", lambda work: work())
    return hq


def cursor_of(api):
    with api.app.state.store.read() as c:
        return json.loads(c.execute("SELECT value FROM registry_metadata WHERE key=?", (W.CURSOR_KEY,)).fetchone()[0])


def test_with_the_gateway_a_watch_is_registered_once_and_its_change_files_like_a_local_one(
    signals, seeded, monkeypatch
):
    watch(seeded)
    [ident] = signals.watches
    assert ("register", "page", PAGE) in signals.calls
    with seeded.app.state.store.read() as c:
        assert c.execute("SELECT signal_watch_id FROM entity_watches").fetchone()[0] == ident
    monkeypatch.setattr(W, "fetch_page", lambda url: pytest.fail("the Gateway reads the page, not the server"))
    signals.read(ident)
    assert check(seeded) == {"read": 1, "changed": 0, "errors": 0}
    assert signals.calls.count(("register", "page", PAGE)) == 1
    signals.change(ident, removed="Pro plan costs 29 a seat", added="Pro plan now costs 39 a seat")
    before = counts(seeded)
    assert check(seeded) == {"read": 1, "changed": 1, "errors": 0}
    after = counts(seeded)
    assert {k: after[k] - before[k] for k in after} == {"market_insights": 1, "market_evidence": 1, "page_events": 1}
    with seeded.app.state.store.read() as c:
        evidence = H._rows(c.execute("SELECT source_url, quote FROM market_evidence"))[-1]
    assert evidence["source_url"] == PAGE and "Pro plan now costs 39" in evidence["quote"]
    assert cursor_of(seeded)["cursor"] == "v1.1"
    assert check(seeded)["changed"] == 0  # the cursor is past the change: it is not filed twice
    assert ("changes", "v1.1") in signals.calls


def test_ending_a_watch_ends_it_at_hq_on_the_spot(signals, seeded):
    made = watch(seeded)["watch"]
    [ident] = signals.watches
    post(seeded, f"market/watches/{made['id']}/stop", {})
    assert signals.watches == {} and ("end", ident) in signals.calls
    with seeded.app.state.store.read() as c:
        assert c.execute("SELECT signal_watch_id FROM entity_watches").fetchone()[0] is None


def test_without_the_gateway_the_pages_are_read_here_as_before(seeded, monkeypatch):
    hq = Hq()
    hq.install(monkeypatch)
    watch(seeded)
    pages(monkeypatch, BASE, CHANGED)
    assert check(seeded)["changed"] == 0
    assert check(seeded)["changed"] == 1
    assert hq.calls == []


def test_a_watch_the_gateway_refuses_is_read_here(signals, seeded, monkeypatch):
    signals.refuse = True
    watch(seeded)
    pages(monkeypatch, BASE, CHANGED)
    assert check(seeded) == {"read": 1, "changed": 0, "errors": 0}
    assert check(seeded) == {"read": 1, "changed": 1, "errors": 0}


def test_the_gateways_status_for_a_watch_shows_on_it(signals, seeded):
    watch(seeded)
    [ident] = signals.watches
    signals.read(ident, status="blocked")
    assert check(seeded) == {"read": 1, "changed": 0, "errors": 1}
    row = get(seeded, "market/watches")["watches"][0]
    assert row["last_status"] == "blocked" and row["error"] == "Read by the Gateway: blocked" and row["checked_at"]


def test_a_change_that_will_not_file_is_skipped_and_the_cursor_still_moves(signals, seeded, monkeypatch):
    watch(seeded)
    [ident] = signals.watches
    signals.read(ident)
    signals.change(ident, removed="Plan A", added="Plan A costs more now")
    signals.change(ident, removed="Plan B", added="Plan B costs more now")
    real, filed = W.file_change, []

    def flaky(c, watch, *args, **kwargs):
        filed.append(1)
        if len(filed) == 1:
            raise RuntimeError("refused")
        return real(c, watch, *args, **kwargs)

    monkeypatch.setattr(W, "file_change", flaky)
    assert check(seeded)["changed"] == 1
    assert cursor_of(seeded)["cursor"] == "v1.2"


def test_a_release_is_watched_only_through_the_gateway_and_each_new_one_files(signals, seeded, monkeypatch):
    made = watch(seeded, url="github:Acme/Rocket", entity="company/northwind", kind="release")["watch"]
    assert made["url"] == "github:acme/rocket"
    assert ("register", "release", "github:acme/rocket") in signals.calls
    monkeypatch.setattr(W, "fetch_page", lambda url: pytest.fail("a release is never read here"))
    [ident] = signals.watches
    signals.read(ident)
    signals.change(
        ident,
        title="v2.0",
        added="Faster builds",
        url="https://github.com/acme/rocket/releases/tag/v2.0",
        kind="release",
    )
    assert check(seeded) == {"read": 1, "changed": 1, "errors": 0}
    with seeded.app.state.store.read() as c:
        insight = H._rows(c.execute("SELECT claim FROM market_insights WHERE source_ref LIKE 'watch:%'"))[-1]
        evidence = H._rows(c.execute("SELECT source_url, quote FROM market_evidence"))[-1]
    assert insight["claim"] == "Release: Northwind"
    assert evidence["source_url"] == "https://github.com/acme/rocket/releases/tag/v2.0"
    assert "Faster builds" in evidence["quote"]


def test_a_release_without_the_gateway_is_refused(seeded):
    watch(seeded, url="github:acme/rocket", entity="company/northwind", kind="release", expected=409)
    assert get(seeded, "market/watches")["watches"] == []
