"""The brand and go-to-market pages (backend/brand_gtm.py): admins write a draft and publish numbered versions, bots
propose, and the brand check finds avoided words, wrong names and never-made claims. Examples use Acme."""

from backend import brand_gtm as B
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


def draft(api, kind="brand", sections=None, revision=None, token="ana-test", expected=200):
    body = {"sections": sections or {}}
    if revision is not None:
        body["revision"] = revision
    return put(api, f"{kind}/draft", body, token=token, expected=expected)


def test_draft_merges_sections_publish_versions_and_stale_revision_is_409(api):
    empty = get(api, "brand")
    assert empty == {"published": None, "draft": None, "versions": []}
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
    assert member["published"]["n"] == 1 and member["draft"] is None
    assert get(api, "brand", token=ops)["draft"] is None
    assert get(api, "brand", token="ben-test")["draft"]["body"]["positioning"] == "Acme cleans homes well"

    post(api, "goal-proposals", {"kind": "gtm_draft", "payload": {"sections": {"motion": {"primary": "door"}}}},
         token=ops, expected=422)
    proposal = post(api, "goal-proposals", {"kind": "gtm_draft", "reason": "Referrals bring most bookings.", "payload": {
        "sections": {"motion": {"primary": "local", "secondary": "self_serve"}}}}, token=ops)["proposal"]
    # The proposal carries unpublished words: admins and the bot that made it read it; a member does not.
    assert not [p for p in get(api, "goal-proposals", token="cara-test")["proposals"] if p["kind"] == "gtm_draft"]
    assert [p["id"] for p in get(api, "goal-proposals", token="ben-test")["proposals"]] == [proposal["id"]]
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="cara-test", expected=403)
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token="ben-test")
    gtm = get(api, "gtm")
    assert gtm["published"] is None and gtm["draft"]["body"]["motion"] == {"primary": "local", "secondary": "self_serve"}


def test_publish_emits_the_event_and_gtm_summary_names_motion_and_channels(api):
    draft(api, "gtm", {"motion": {"primary": "local"}, "buyer": {"who_buys": "Homeowners", "deal_size": "$90/month"}})
    post(api, "gtm/publish", {"note": "First"})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM events WHERE action='gtm.publish' AND target='v1'").fetchone()
        c.execute("INSERT INTO growth_channels (id, slug, name, stage, created_at) VALUES "
                  "('c1','google-search','Google Search','scaling','2026-10-01')")
        text = B.summary_text(c, "gtm")
    assert text.startswith("GTM v1.") and "Motion: local" in text and "Homeowners, $90/month" in text
    assert "Scaling: Google Search" in text


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
