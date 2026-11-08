"""BotOps acting for a person on the Company page: drafting and settings are the person's to do through BotOps, and what
publishes, approves, overrides or takes an edition back stays the person's own click in the app. Acme is the fictional
company; ana-test owns it, ben-test administers it and cara-test is a member (the base fixtures)."""
import pytest

from backend.botops_act import classify, default_delegable, person_only
from backend.tests.test_api import api, headers  # noqa: F401  (fixture)
from backend.tests.test_botops_confirm_cards import card_in_chat, confirm
from backend.tests.test_member_bots import botops, turn  # noqa: F401  (fixture)


def act(api, attempt, method, path, body=None):
    """One request BotOps makes for the person it works for (`on_behalf_of: turn`)."""
    kwargs = {"headers": {**headers(attempt["token"]), "X-Tico-On-Behalf-Of": "turn"}}
    if body is not None:
        kwargs["json"] = body
    return getattr(api, method.lower())("/api/v2/" + path, **kwargs)


# The person's own click in the app: BotOps is refused whatever the person said in chat. Each one is a real way an
# edition reaches readers or leaves them, so none is delegated.
REFUSED = [
    ("POST", "editions/ed-1/publish"), ("POST", "editions/ed-1/override"), ("POST", "editions/ed-1/approval"),
    ("POST", "editions/ed-1/retract"), ("POST", "editions/ed-1/correct"),
    ("POST", "audiences/aud-1/stats/approval"),
]


@pytest.mark.parametrize("method,path", REFUSED)
def test_botops_is_not_offered_what_publishes_approves_or_takes_an_edition_back(method, path):
    assert classify(method, "/api/v2/" + path) is None
    assert not default_delegable(method, "/api/v2/" + path)


def test_what_the_page_shows_or_grants_asks_the_person_and_taking_access_away_is_direct():
    assert classify("PATCH", "/api/v2/company-page") == "confirm"
    assert classify("PUT", "/api/v2/audiences/aud-1/disclosure") == "confirm"
    assert classify("POST", "/api/v2/readers") == "confirm"
    assert classify("POST", "/api/v2/readers/import") == "confirm"
    assert classify("DELETE", "/api/v2/audiences/aud-1") == "confirm"
    assert classify("DELETE", "/api/v2/readers/rdr-1") == "do"
    assert classify("DELETE", "/api/v2/audiences/aud-1/invites/inv-1") == "do"


def test_turning_an_audience_on_is_the_owners_card_and_turning_it_off_is_direct():
    # Turning one on serves what its approved content says, so it is the person's own tap; a way out stays direct.
    assert classify("PATCH", "/api/v2/audiences/aud-1", {"enabled": True}) == "confirm"
    assert classify("PATCH", "/api/v2/audiences/aud-1", {"enabled": False}) == "do"
    assert classify("PATCH", "/api/v2/audiences/aud-1", {"name": "Board"}) == "do"


def test_botops_cannot_reopen_an_audience_without_the_owners_tap(api, botops):  # noqa: F811
    ana = turn(api, botops, person="ana-test", text="Set up the board audience")
    made = act(api, ana, "POST", "audiences", {"name": "Board"})
    assert made.status_code == 200, made.text
    audience = made.json()["id"]
    on = act(api, ana, "PATCH", f"audiences/{audience}", {"enabled": True})
    assert on.status_code == 200 and on.json()["needs_confirm"], on.text
    card_in_chat(api, on.json(), "ana")
    assert _enabled(api, audience) is False
    assert confirm(api, on.json(), "ana-test")["action"]["status"] == "done"
    assert _enabled(api, audience) is True
    off = act(api, ana, "PATCH", f"audiences/{audience}", {"enabled": False})
    assert off.status_code == 200 and "needs_confirm" not in off.json(), off.text
    assert _enabled(api, audience) is False


def _enabled(api, audience):
    listed = api.get("/api/v2/audiences", headers=headers("ana-test")).json()["audiences"]
    return next(a["enabled"] for a in listed if a["id"] == audience)


def test_a_reader_invite_link_is_handed_to_the_person_and_never_to_botops():
    # The link is the only copy of its token, so it is a secret for this purpose: refused, with where to make it.
    assert "invite link" in person_only("POST", "/api/v2/audiences/aud-1/invites")


def test_an_owner_drafts_through_botops_and_the_approval_click_stays_theirs(api, botops):  # noqa: F811
    ana = turn(api, botops, person="ana-test", text="Set up the board audience")
    made = act(api, ana, "POST", "audiences", {"name": "Board"})
    assert made.status_code == 200, made.text
    for path, body, status, why in [
        ("editions/ed-1/publish", {}, 403, "own click"),
        ("editions/ed-1/override", {"note": "Checked by hand"}, 403, "own click"),
        ("editions/ed-1/approval", {}, 403, "Only a person"),
        ("editions/ed-1/retract", {"note": "Taking it back"}, 403, "Only a person"),
    ]:
        refused = act(api, ana, "POST", path, body)
        assert refused.status_code == status, (path, refused.text)
        assert why in refused.text, (path, refused.text)


def test_a_reader_invite_is_refused_through_botops_with_the_app_named(api, botops):  # noqa: F811
    ana = turn(api, botops, person="ana-test", text="Invite the board")
    refused = act(api, ana, "POST", "audiences/aud-1/invites", {"days": 7})
    assert refused.status_code == 403, refused.text
    assert "invite link" in refused.text


def test_a_member_cannot_change_the_company_page_through_botops(api, botops):  # noqa: F811
    cara = turn(api, botops, person="cara-test", text="Add the board audience")
    refused = act(api, cara, "POST", "audiences", {"name": "Board"})
    assert refused.status_code == 403, refused.text
