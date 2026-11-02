"""The Company page's backend seams: what the mission discloses, the edge's route allowlist, and the switch's side effects.
Acme is the fictional company. Each test stands for a way something could leave the install that the owner did not allow."""
import json

from backend import company
from backend.company_page import audiences as A, disclosure as D, release, render, settings as S
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
