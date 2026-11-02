"""The brand page (backend/brand.py): admins write a draft and publish numbered versions, bots propose, and the brand
check finds avoided words, wrong names and never-made claims. Examples use Acme."""

import json

from backend import brand as B
from backend.tests.test_api import api, get, post, put  # noqa: F401
from backend.tests.test_company import bot

BRAND = {
    "positioning": "Home cleaning you book in a minute and never think about again",
    "voice": [{"trait": "warm", "do": "Say we", "dont": "Say the company"}, {"trait": "plain"}],
    "words": {"use": [{"word": "clean", "not": "sanitize"}],
              "avoid": [{"word": "cheap", "why": "We are fair, not cheap", "level": "block"},
                        {"word": "maid", "level": "warn"}]},
    "naming": [{"name": "Acme Clean", "rule": "Two words, both capitalized", "wrong": ["AcmeClean", "acme-clean"]}],
    "claims": {"allowed": [{"claim": "Insured cleaners", "proof": "Policy on file", "status": "proven"}],
               "never": ["eco-friendly", "best in town"]},
}


def draft(api, sections=None, revision=None, token="ana-test", expected=200):
    body = {"sections": sections or {}}
    if revision is not None:
        body["revision"] = revision
    return put(api, "brand/draft", body, token=token, expected=expected)


def test_draft_merges_sections_publish_versions_and_stale_revision_is_409(api):
    empty = get(api, "brand")
    assert empty == {"published": None, "draft": None, "versions": [], "may_edit": True}
    assert get(api, "brand/summary") == {"n": 0, "text": ""}
    saved = draft(api, sections={"positioning": BRAND["positioning"], "voice": BRAND["voice"]}, revision=0)
    assert saved["draft"]["revision"] == 1 and set(saved["draft"]["changed"]) == {"positioning", "voice"}
    stale = draft(api, sections={"tone": []}, revision=0, expected=409)
    assert stale["error"]["code"] == "stale_revision"
    draft(api, sections={"positioning": "x" * 121}, expected=422)
    draft(api, sections={"slogan": "Hi"}, expected=422)
    published = post(api, "brand/publish", {"note": "First"})
    assert published["published"]["n"] == 1 and published["draft"] is None
    assert published["versions"][0]["note"] == "First"
    # The next draft starts from the published body; null removes a section.
    again = draft(api, sections={"voice": None, "words": BRAND["words"]})["draft"]
    assert again["body"]["positioning"] == BRAND["positioning"] and "voice" not in again["body"]
    assert set(again["changed"]) == {"voice", "words"}
    post(api, "brand/publish", {"note": "Words"})
    assert [v["trait"] for v in get(api, "brand/versions/1")["body"]["voice"]] == ["warm", "plain"]
    get(api, "brand/versions/9", expected=404)
    summary = get(api, "brand/summary")
    assert summary["n"] == 2 and "Brand v2" in summary["text"] and "cheap" in summary["text"]
    assert len(summary["text"]) <= B.SUMMARY_MAX
    post(api, "brand/publish", {"note": "Nothing"}, expected=409)


def test_brand_name_differs_from_the_company_and_leads_the_summary(api):
    draft(api, sections={"name": "x" * 61}, expected=422)
    draft(api, sections={"name": "Acme Clean", "positioning": BRAND["positioning"]})
    post(api, "brand/publish", {"note": "Named"})
    assert get(api, "brand")["published"]["body"]["name"] == "Acme Clean"
    assert get(api, "brand/summary")["text"].startswith("Brand v1: Acme Clean. Home cleaning")
    draft(api, sections={"name": None})
    post(api, "brand/publish", {"note": "Unnamed"})
    assert get(api, "brand/summary")["text"].startswith("Brand v2. Home cleaning")


def test_bots_propose_members_read_only_published_and_admins_confirm(api):
    ops = bot(api, "ops")
    draft(api, sections={"positioning": "Acme cleans homes"})
    post(api, "brand/publish", {"note": "First"})
    draft(api, sections={"positioning": "Acme cleans homes well"})
    refused = draft(api, sections={"positioning": "Mine"}, token=ops, expected=403)
    assert refused["error"]["code"] == "propose_instead" and "--kind brand_draft" in refused["error"]["detail"]
    draft(api, sections={"positioning": "Mine"}, token="cara-test", expected=403)
    post(api, "brand/publish", {}, token="cara-test", expected=403)
    member = get(api, "brand", token="cara-test")
    assert member["published"]["n"] == 1 and member["draft"] is None and member["may_edit"] is False
    assert get(api, "brand", token=ops)["draft"] is None
    assert get(api, "brand", token="ben-test")["draft"]["body"]["positioning"] == "Acme cleans homes well"

    # Filed under a goal, its unpublished words would show on that goal's page to members.
    goal = post(api, "goals", {"title": "More bookings", "owner_id": "ana"})["goal"]
    refused = post(api, "goal-proposals", {"kind": "brand_draft", "goal_id": goal["id"], "payload": {
        "sections": {"positioning": "Secret"}}}, token=ops, expected=422)
    assert refused["error"]["detail"].startswith("a brand draft proposal is about the page")
    proposal = post(api, "goal-proposals", {"kind": "brand_draft", "reason": "Plain words sell better.", "payload": {
        "sections": {"voice": [{"trait": "plain"}]}}}, token=ops)["proposal"]
    # The proposal carries unpublished words: admins and the bot that made it read it; a member does not.
    assert not [p for p in get(api, "goal-proposals", token="cara-test")["proposals"] if p["kind"] == "brand_draft"]
    assert [p["id"] for p in get(api, "goal-proposals", token="ben-test")["proposals"]] == [proposal["id"]]
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="cara-test", expected=403)
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="ben-test")
    assert get(api, "brand")["published"]["n"] == 1
    assert [v["trait"] for v in get(api, "brand")["draft"]["body"]["voice"]] == ["plain"]


def test_clip_keeps_abbreviations_and_whole_words():
    assert B._clip("U.S. homeowners with 1-3 units. Next: managers.", 90, first=True) == "U.S. homeowners with 1-3 units"
    assert B._clip("Dr. Smith and other dentists", 90, first=True) == "Dr. Smith and other dentists"
    assert B._clip("Owners (Oct 9). Not now: big operators", 90, first=True) == "Owners (Oct 9)"
    assert B._clip("one two three four", 12) == "one two…"


def test_check_finds_avoided_words_wrong_names_and_never_claims_at_word_boundaries():
    hits = B.check(BRAND, "Cheap and eco-friendly! Book AcmeClean today; cheapest maids aren't a maid.")
    assert [(h["kind"], h["match"], h["level"]) for h in hits] == [
        ("avoid", "Cheap", "block"), ("never_claim", "eco-friendly", "block"), ("naming", "AcmeClean", "warn"),
        ("avoid", "maid", "warn")]
    assert hits[0]["index"] == 0 and hits[0]["rule"] == "We are fair, not cheap"


def test_check_route_reads_the_published_brand(api):
    assert post(api, "brand/check", {"text": "cheap"}) == {"n": 0, "hits": []}
    draft(api, sections={"words": BRAND["words"]})
    post(api, "brand/publish", {})
    ops = bot(api, "ops")
    found = post(api, "brand/check", {"text": "A cheap clean"}, token=ops)
    assert found["n"] == 1 and [h["match"] for h in found["hits"]] == ["cheap"]


def test_growth_facing_bots_get_the_brand_in_their_turn(api):
    from backend import learned
    draft(api, sections={"positioning": "Acme cleans homes"})
    post(api, "brand/publish", {})
    settings = api.app.state.store.settings
    with api.app.state.store.read() as c:
        assert learned.for_turn(c, settings, None, "growth")["brand"].startswith("Brand v1.")
        assert "brand" not in (learned.for_turn(c, settings, None, "ops") or {})


def test_the_hub_tools_reach_every_brand_route(api):
    """Each MCP tool's path and body match its route, run through the server as the MCP endpoint runs them."""
    from backend.tests.test_mcp import call
    ok = lambda name, args=None, token="ana-test": call(api, name, args, token)  # noqa: E731
    err, out = ok("hub_brand_draft", {"section": "words", "body": '{"avoid": [{"word": "cheap", "level": "block"}]}'})
    assert not err and out["draft"]["revision"] == 1
    assert not ok("hub_brand_publish", {"note": "First"})[0]
    err, out = ok("hub_brand_check", {"text": "So cheap"}, token="cara-test")
    assert not err and [h["match"] for h in out["hits"]] == ["cheap"]
    err, out = ok("hub_brand_show", {"section": "words"}, token="cara-test")
    assert not err and out["n"] == 1 and out["value"]["avoid"][0]["word"] == "cheap"
    assert "cheap" in ok("hub_brand_summary")[1]["text"]
    assert not ok("hub_brand_show", {"version": 1})[0]
