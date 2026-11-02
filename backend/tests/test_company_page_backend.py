"""The Company page's backend seams: what the mission discloses, the edge's route allowlist, and the switch's side effects.
Acme is the fictional company. Each test stands for a way something could leave the install that the owner did not allow."""
import json
from datetime import datetime, timezone

from backend import company
from backend.company_page import audiences as A, disclosure as D, release, render, settings as S
from backend.tests.test_api import api, headers  # noqa: F401
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
