"""The Company page's backend seams: what the mission discloses, the edge's route allowlist, and the switch's side effects.
Acme is the fictional company. Each test stands for a way something could leave the install that the owner did not allow."""
import json
from datetime import datetime, timezone

from backend import company
from backend.company_page import audiences as A, disclosure as D, editions as E, release, render, settings as S, stats as ST
from backend.tests.test_company_page_core import numbers, setup, shown
from backend.tests.test_company_page_editions import plain_sections
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_company_page_editions import page  # noqa: F401
from backend.tests.test_finance_fixtures import OWNER, finance_template, store  # noqa: F401

VISION = "Every landlord runs on Acme."


def _settings(store):  # noqa: F811
    return store.settings


def _page_text(view):
    return json.dumps(view) + render.page(view) + render.feed(view) + render.signed_out(view)


def test_a_hidden_mission_is_on_no_page_no_feed_and_no_data(store):  # noqa: F811
    """The company profile holds the vision; the audience's mission rule decides whether it leaves. Hidden by default."""
    with store.transaction() as c:
        A.seed(c)
        company.update(c, OWNER, vision=VISION)
        investors = {"id": "investors", "kind": "investors", "name": "Investors"}
        view = release.view_model(c, investors, [], [], None, "private", _settings(store))
        assert view["company"]["mission"] == ""
        assert VISION not in _page_text(view)
        # The signed-out door has no audience, so no mission either.
        door = release.view_model(c, {"kind": "private", "name": ""}, [], [], None, "private", _settings(store))
        assert door["company"]["mission"] == "" and VISION not in _page_text(door)
        # Shown to this audience only: the other audience still gets none.
        D.set_rule(c, OWNER, "investors", "mission", mode="show")
        shown = release.view_model(c, investors, [], [], None, "private", _settings(store))
        assert shown["company"]["mission"] == VISION and VISION in _page_text(shown)
        board = {"id": "board", "kind": "board", "name": "Board"}
        assert release.view_model(c, board, [], [], None, "private", _settings(store))["company"]["mission"] == ""


def test_going_open_turns_public_on_in_the_same_step(store):  # noqa: F811
    """Open is a public page: the Public audience is on the moment the company goes Open, with nothing else to switch."""
    with store.transaction() as c:
        A.seed(c)
        S.set_transparency(c, OWNER, "private")
        assert c.execute("SELECT enabled FROM audiences WHERE kind='public'").fetchone()[0] == 0
        S.set_transparency(c, OWNER, "open")
        assert c.execute("SELECT enabled FROM audiences WHERE kind='public'").fetchone()[0] == 1
        # Going back doesn't have to switch it off; a page is served only while the company is Open.
        S.set_transparency(c, OWNER, "closed")
        assert release.public_view(c, datetime.now(timezone.utc)) == {}


def test_a_tightening_says_how_many_published_editions_it_leaves_up(page):  # noqa: F811
    """Published editions stay when a rule tightens: they're dated statements a person approved. The response says so
    instead of changing the page in silence. A loosening doesn't carry the count."""
    page.published()
    tighter = page.api.put("/api/v2/audiences/board/disclosure", headers=headers(),
                           json={"rules": [{"data_class": "kpi", "mode": "hidden"}]})
    assert tighter.status_code == 200 and tighter.json()["published_editions"] == 1
    looser = page.api.put("/api/v2/audiences/board/disclosure", headers=headers(),
                          json={"rules": [{"data_class": "kpi", "mode": "show"}]})
    assert looser.status_code == 200 and "published_editions" not in looser.json()
    assert page.rows("SELECT status FROM editions WHERE status='published'") != []


def test_a_held_label_is_not_in_the_draft_and_its_word_is_not_allowed_in_the_text(store):  # noqa: F811
    """A held stat's label goes nowhere near the model: the draft's facts leave it out, so the leak check doesn't allow
    the word either, and a draft that names it is flagged."""
    setup(store, shown("cash_runway"))
    with store.transaction() as c:
        numbers(c, ("Northwind", "cash_runway", "months"), ("Harbor", "cash_runway", "$"))
        facts = ST.facts(c, "investors", "2026-10-01", "2026-10-31")
    assert "held" not in facts and facts["stats"] == []
    prompt = E.draft_input(facts, [], "2026-10-01", "2026-10-31", {})
    assert "Northwind" not in prompt and "Harbor" not in prompt and "12,000" not in prompt
    found = E.leak_findings("Update", plain_sections("Northwind was 12,000 in October."), facts, "2026-10-01", "2026-10-31")
    assert found["numbers"] == ["12,000"] and found["names"] == ["Northwind"]


def test_a_public_board_yes_puts_the_public_page_up_when_it_is_recorded(page):  # noqa: F811
    """The Public page shows what the board approved, so the yes rebuilds it at once instead of at the next day's refresh.
    A Private board's yes leaves the Public page alone."""
    with page.store.transaction() as c:
        A.seed(c)
        S.set_transparency(c, OWNER, "open")
        D.set_rule(c, "human:ana", "public", "kpi", mode="show")
    page.decide(post(page.api, "audiences/board/stats/approval", {})["approval"])
    assert "board approved" not in page.rebuilds
    page.decide(post(page.api, "audiences/public/stats/approval", {})["approval"])
    assert page.rebuilds.count("board approved") == 1
    served = release._served(page.store.settings)
    assert [s["label"] for s in served["stats"]] == ["Revenue"]
