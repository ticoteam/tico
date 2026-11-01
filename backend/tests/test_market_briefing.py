"""The weekly briefing: who posts, one row a week, which bets an item touches, and the event the Strategy Bot waits
on."""

import json

import pytest

from backend import automations
from backend.store import H
from backend.tests.test_api import api, get, post, setup_attempt  # noqa: F401
from backend.tests.test_market import _librarian, _seed
from backend.tests.test_strategy import add_bet, publish, write

WEEK = "2026-W44"


def brief(api, items=None, week=WEEK, title="Week 44", body="Northwind changed its pricing.", token="ana-test",
          expected=200):
    return post(api, "market/briefings", {"week": week, "title": title, "body": body, "items": items or []},
                token=token, expected=expected)


def item(*entities, text="Northwind raised its price.", kind="change"):
    return {"text": text, "kind": kind, "entity_ids": list(entities)}


@pytest.fixture
def seeded(api, tmp_path):
    _seed(api, tmp_path)
    return api


@pytest.fixture
def events(monkeypatch):
    """The `market.briefing` events the automations hear (H.hook), with their payloads."""
    seen, heard = [], automations.on_event

    def listen(c, type_, subject, data, actor):
        if type_ == "market.briefing":
            seen.append((type_, subject, data() if callable(data) else data))
        return heard(c, type_, subject, data, actor)
    monkeypatch.setattr(automations, "on_event", listen)
    return seen


def publish_bets(api):
    write(api, summary="Acme runs rentals with bots.", sections={"not": "No enterprise deals."})
    cited = add_bet(api, "Small landlords pay for bots", cites=["company/northwind"])["bet"]
    thesis = add_bet(api, "Mid-size managers want one tool", wrong_if={"kind": "thesis", "market_id": "company/brightline"})["bet"]
    add_bet(api, "Enterprise first", cites=["company/pricewise"], status="proposed")      # not active: never touched
    publish(api)
    return cited, thesis


def test_only_the_owner_and_the_librarian_post_and_a_reader_reads(seeded):
    brief(seeded, token="ben-test", expected=403)
    _librarian(seeded)
    _, _, librarian = setup_attempt(seeded, "librarian")
    made = brief(seeded, token=librarian["token"])["briefing"]
    assert made["created_by"] == "bot:librarian"
    assert get(seeded, "market/briefings/latest", token="ben-test")["briefing"]["week"] == WEEK
    assert "body" not in get(seeded, "market/briefings", token="ben-test")["briefings"][0]


def test_a_week_that_is_not_an_iso_week_is_refused_and_nothing_is_written(seeded, events):
    for week in ("2026-44", "2026-W54", "2026-W00", "last week"):
        brief(seeded, week=week, expected=422)
    brief(seeded, items=[{"text": "  ", "entity_ids": []}], expected=422)
    assert get(seeded, "market/briefings")["briefings"] == [] and events == []


def test_a_week_has_one_row_a_replacement_keeps_created_and_rewrites_the_page(seeded, events):
    first = brief(seeded, body="First draft.")["briefing"]
    again = brief(seeded, title="Week 44, final", body="Final text.")["briefing"]
    assert again["id"] == first["id"] and again["created_at"] == first["created_at"] and again["title"] == "Week 44, final"
    assert len(get(seeded, "market/briefings")["briefings"]) == 1
    with seeded.app.state.store.read() as c:
        rows = c.execute("SELECT body FROM market_pages WHERE id=?", ("market/briefings/" + WEEK,)).fetchall()
    assert len(rows) == 1 and rows[0]["body"] == "Final text."
    assert get(seeded, "market/briefings/2026-W01", expected=404)["error"]["code"] == "not_found"


def test_the_bets_touched_are_the_active_published_ones_that_cite_or_watch_an_item_entity(seeded, events):
    cited, thesis = publish_bets(seeded)
    got = brief(seeded, items=[item("company/northwind"), item("company/brightline", kind="signal"),
                               item("company/pricewise")])["briefing"]
    assert got["bets"] == [cited["id"], thesis["id"]]
    assert [(t["n"], t["id"]) for t in got["touched"]] == [(1, cited["id"]), (2, thesis["id"])]
    none = brief(seeded, week="2026-W45", items=[item("company/harborly")])["briefing"]
    assert none["bets"] == [] and none["touched"] == []


def test_a_draft_bet_is_not_touched_until_it_is_published(seeded, events):
    write(seeded, summary="Acme runs rentals with bots.", sections={"not": "No enterprise deals."})
    add_bet(seeded, cites=["company/northwind"])
    assert brief(seeded, items=[item("company/northwind")])["briefing"]["bets"] == []


def test_the_event_goes_out_once_a_week_with_the_touched_bets(seeded, events):
    cited, _ = publish_bets(seeded)
    brief(seeded, items=[item("company/northwind")])
    assert events == [("market.briefing", WEEK, {"week": WEEK, "bets": [cited["id"]]})]
    brief(seeded, items=[item("company/northwind")], body="Edited.")      # same bets: no second event
    assert len(events) == 1
    brief(seeded, items=[item("company/northwind", "company/brightline")], body="Edited again.")
    assert len(events) == 2 and events[1][1].startswith(WEEK + ".") and events[1][1] != WEEK


def test_a_replacement_that_touches_no_bet_sends_no_new_event(seeded, events):
    publish_bets(seeded)
    brief(seeded, items=[item("company/northwind")])
    brief(seeded, items=[], body="Nothing this week after all.")
    assert len(events) == 1
    with seeded.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM market_briefings").fetchone()[0] == 1


def test_an_automation_post_without_items_keeps_the_weeks_items_and_bets(seeded, events):
    """The daily run posts with `items=None`: its title and page replace the week's, the items the Librarian posted by
    hand and the bets they touch stay, and no second event goes out for the same bets. A week with no row gets []."""
    from types import SimpleNamespace

    from backend import market_briefing as MB
    cited, _ = publish_bets(seeded)
    hand = brief(seeded, items=[item("company/northwind")])["briefing"]
    assert hand["bets"] == [cited["id"]] and len(events) == 1
    with seeded.app.state.store.transaction() as c:
        MB.post(c, SimpleNamespace(actor="human:ana"), WEEK, "Daily", "Run text.", None,
                source={"automation": "auto_t", "run": "r"})
    kept = get(seeded, "market/briefings/" + WEEK)["briefing"]
    assert (kept["title"], kept["body"]) == ("Daily", "Run text.")
    assert (kept["items"], kept["bets"]) == (hand["items"], [cited["id"]])
    assert kept["source"] == {"automation": "auto_t", "run": "r"} and len(events) == 1
    with seeded.app.state.store.transaction() as c:
        MB.post(c, SimpleNamespace(actor="human:ana"), "2026-W45", "Empty", "Body.", None)
    empty = get(seeded, "market/briefings/2026-W45")["briefing"]
    assert (empty["items"], empty["bets"]) == ([], [])


def test_market_labels_a_briefing_by_how_often_it_is_posted(seeded):
    """A briefing by hand is the week's; one an automation posts each day (the built-in, every morning) is the day's;
    one posted by a once-a-week schedule is the week's again."""
    from types import SimpleNamespace

    from backend import market_briefing as MB
    from backend.tests.test_automation_api import NOTE_STEP
    assert brief(seeded)["briefing"]["cadence"] == "weekly"
    made = {cron: post(seeded, "automations", {"name": "Briefing " + cron, "owner_id": "person:ana", "steps": [NOTE_STEP],
                                               "trigger": {"type": "schedule", "cron": cron, "tz": "UTC"}})["id"]
            for cron in ("0 7 * * *", "0 7 * * 1-5", "0 7 * * 1", "0 7 * * MON")}
    want = {"0 7 * * *": "daily", "0 7 * * 1-5": "daily", "0 7 * * 1": "weekly", "0 7 * * MON": "weekly"}
    for cron, aid in made.items():
        with seeded.app.state.store.transaction() as c:
            MB.post(c, SimpleNamespace(actor="human:ana"), WEEK, "Today", "Body.", [], source={"automation": aid, "run": "r"})
        latest = get(seeded, "market/briefings/latest")["briefing"]
        assert latest["cadence"] == want[cron], cron
        assert get(seeded, "market/briefings")["briefings"][0]["cadence"] == want[cron]
    assert brief(seeded)["briefing"]["cadence"] == "weekly"          # a post by hand clears the source
