"""The brand and go-to-market pages (backend/brand_gtm.py): admins write a draft and publish numbered versions, bots
propose, and the brand check finds avoided words, wrong names and never-made claims. Examples use Acme."""

import json

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

    post(api, "goal-proposals", {"kind": "gtm_draft", "payload": {"sections": {"motion": {"primary": "door"}}}},
         token=ops, expected=422)
    # Filed under a goal, its unpublished words would show on that goal's page to members.
    goal = post(api, "goals", {"title": "More bookings", "owner": "ana"})["goal"]
    assert post(api, "goal-proposals", {"kind": "brand_draft", "goal_id": goal["id"], "payload": {
        "sections": {"positioning": "Secret"}}}, token=ops, expected=422)["error"]["detail"].startswith("a brand or GTM")
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


def test_summary_keeps_north_star_and_channels_when_a_field_is_long(api):
    who = ("First: self-managing Airbnb owners across the US with fewer than 3 units (Thomaz, Oct 9, 2026). "
           "Next: short-term rental owners paying a manager 20-35%. Not now: operators with 21+ units, owners who want "
           "a tool to run the whole business.")
    draft(api, "gtm", {"motion": {"primary": "sales_led", "secondary": "partner"}, "buyer": {"who_buys": who}})
    post(api, "gtm/publish", {"note": "First"})
    kpi = post(api, "kpis", {"name": "Active customers"})["kpi"]
    post(api, f"kpis/{kpi['id']}", {"growth_role": "north_star"})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO growth_channels (id, slug, name, stage, created_at) VALUES "
                  "('c2','partner-referrals','Partner referrals','testing','2026-10-01')")
    gtm = get(api, "gtm/summary")["text"]
    assert gtm.startswith("GTM v1. Motion: sales led (then partner). Buyer: First: self-managing Airbnb owners")
    assert "North Star: Active customers." in gtm and "Testing: Partner referrals." in gtm
    assert len(gtm) <= B.SUMMARY_MAX
    avoid = ["cheap", "industry-leading same-day emergency repairs", "world-class certified deep-clean specialists",
             "guaranteed lowest prices in the entire country", "revolutionary AI-powered home care platform",
             "unbeatable once-in-a-lifetime bargain deals"]
    draft(api, sections={"words": {"avoid": [{"word": w, "level": "block" if w == "cheap" else "warn"} for w in avoid]}})
    post(api, "brand/publish", {"note": "Words"})
    brand = get(api, "brand/summary")["text"]
    assert len(brand) <= B.SUMMARY_MAX
    listed = brand.split("Avoid: ")[1].rstrip(".").split(", ")
    # The block word leads; the words that would overflow the part are dropped, not cut.
    assert listed[0] == "cheap" and 1 < len(listed) < len(avoid)
    assert all(item in avoid for item in listed)


def test_gtm_summary_names_served_and_tested_segments_after_the_buyer_within_the_cap(api, monkeypatch):
    draft(api, "gtm", {"motion": {"primary": "local"}, "buyer": {"who_buys": "Homeowners", "deal_size": "$90/month"}})
    post(api, "gtm/publish", {"note": "First"})
    names = {"Homeowners": "serving", "Rental owners": "serving", "Property managers": "testing",
             "Offices": "next", "Hotels": "dropped"}
    for name, status in names.items():
        post(api, "growth/segments", {"name": name, "status": status})
    text = get(api, "gtm/summary")["text"]
    assert "Buyer: Homeowners, $90/month. Segments: serving Homeowners, Rental owners; testing Property managers." \
        in text
    assert "Offices" not in text and "Hotels" not in text
    for i in range(12):
        post(api, "growth/segments", {"name": f"Long-tail landlord segment number {i}", "status": "testing"})
    text = get(api, "gtm/summary")["text"]
    shown = "Segments: " + text.split("Segments: ")[1].split("…")[0]
    assert len(text) <= B.SUMMARY_MAX and len(shown) < B.SEGMENTS_CHARS
    monkeypatch.setattr(B, "SEGMENTS_CHARS", 10_000)
    with api.app.state.store.read() as c:
        full = B._segments_line(c)[0]
    assert full.startswith(shown) and full[len(shown)] in " ,;"                   # cut at a word, never inside one


def test_a_row_written_with_the_removed_wishes_section_reads_without_it(api):
    """Rows saved before the wishes section was removed still hold it: reads drop it, the summary never names it, and a
    write goes through as it would on a row that never had it."""
    draft(api, "gtm", {"motion": {"primary": "local"}, "buyer": {"who_buys": "Homeowners"}})
    post(api, "gtm/publish", {"note": "First"})
    legacy = {"motion": {"primary": "local"}, "buyer": {"who_buys": "Homeowners"},
              "wishes": [{"text": "No cold SMS", "level": "block"}]}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE gtm_versions SET body_json=? WHERE n=1", (json.dumps(legacy),))
        c.execute("UPDATE gtm_drafts SET body_json=?, revision=revision+1 WHERE id=1", (json.dumps(legacy),))
    page = get(api, "gtm")
    assert page["published"]["body"] == {"motion": {"primary": "local"}, "buyer": {"who_buys": "Homeowners"}}
    assert page["draft"]["body"] == page["published"]["body"] and page["draft"]["changed"] == []
    assert get(api, "gtm/versions/1")["body"] == page["published"]["body"]
    assert "No cold SMS" not in get(api, "gtm/summary")["text"]
    draft(api, "gtm", {"wishes": []}, expected=422)
    draft(api, "gtm", {"playbooks": ["Outbound list"]})
    post(api, "gtm/publish", {"note": "Playbooks"})
    published = get(api, "gtm/versions/2")["body"]
    assert set(published) == {"motion", "buyer", "playbooks"} and published["playbooks"] == ["Outbound list"]


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


def test_growth_facing_bots_get_the_brand_and_gtm_in_their_turn(api):
    from backend import learned
    draft(api, sections={"positioning": "Acme cleans homes"})
    post(api, "brand/publish", {})
    settings = api.app.state.store.settings
    with api.app.state.store.read() as c:
        assert learned.for_turn(c, settings, None, "growth")["brand"].startswith("Brand v1.")
        assert "brand" not in (learned.for_turn(c, settings, None, "ops") or {})


def test_the_hub_tools_reach_every_brand_gtm_and_growth_route(api):
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
    assert not ok("hub_gtm_draft", {"sections": {"motion": {"primary": "local"}}})[0]
    assert not ok("hub_growth_source_add", {"truth_for": "bookings", "system": "Stripe", "reach_kind": "mcp"})[0]
    assert ok("hub_gtm_sources")[1]["sources"][0]["system"] == "Stripe"
    assert not ok("hub_growth_channel_add", {"slug": "google-search", "name": "Google Search", "stage": "scaling"})[0]
    assert not ok("hub_growth_channel_update", {"slug": "google-search", "budget_month": 3400})[0]
    assert ok("hub_growth_channel_show", {"slug": "google-search"})[1]["budget_month"] == 3400
    err, made = ok("hub_growth_idea_create", {"title": "Referral card", "channels": ["google-search"],
                                              "ice_i": 7, "ice_c": 5, "ice_e": 8, "assignee": "me"})
    assert not err and made["ice"] and made["channels"] == ["google-search"]
    tid = made["task_id"]
    assert ok("hub_growth_idea_score", {"id": tid, "i": 3, "c": 3, "e": 3})[1]["ice_i"] == 3
    err, started = ok("hub_growth_idea_start", {"id": tid, "test_to": "2026-11-15", "change": "Card in every box",
                                                "stop_if": "Complaints"})
    assert not err and started["status"] == "testing"
    assert ok("hub_growth_idea_verdict", {"id": tid, "status": "lost", "learning": "Nobody reads the box"})[1]["status"] == "lost"
    assert [i["task_id"] for i in ok("hub_growth_idea_list", {"status": "lost"})[1]["ideas"]] == [tid]
    err, _ = ok("hub_growth_cohort_set", {"channel": "google-search", "month": "2026-10", "spend": 3400,
                                         "bot_cost": 260, "new_customers": 71, "source": ["spend=Google Ads"]})
    assert not err and ok("hub_growth_economics", {"month": "2026-10"})[1]["rows"][0]["cac"]
    err, seg = ok("hub_growth_segment_create", {"name": "Property managers", "status": "serving", "motion": "sales_led"})
    assert not err and seg["status"] == "serving" and seg["segment_id"] == "segment/property-managers"
    assert ok("hub_growth_segment_update", {"id": seg["segment_id"], "deal_size": "$400/month"})[1]["deal_size"] \
        == "$400/month"
    assert ok("hub_growth_segment_list")[1]["segments"][0]["motion"] == "sales_led"
    assert ok("hub_growth_channel_update", {"slug": "google-search", "segments": [seg["id"]]})[1]["segments"] == [seg["id"]]
    assert not ok("hub_growth_cohort_set", {"channel": "google-search", "month": "2026-10", "segment_id": seg["id"],
                                            "new_customers": 5})[0]
    assert ok("hub_growth_economics", {"segment": seg["id"]})[1]["total"]["new_customers"] == 5
    assert ok("hub_growth_cohort_list", {"segment": seg["id"]})[1]["cohorts"][0]["new_customers"] == 5
    assert ok("hub_growth_idea_list", {"segment": seg["id"]})[1]["ideas"] == []
    assert not ok("hub_growth_settings", {"set": ["revenue_month=90", "margin_pct=40"]})[0]
    assert ok("hub_growth_settings")[1]["revenue_month"] == 90
    for name in ("hub_growth_week", "hub_growth_evidence", "hub_growth_working", "hub_growth_kpis",
                 "hub_growth_cohort_list", "hub_growth_channel_list", "hub_brand_summary", "hub_gtm_show"):
        assert not ok(name)[0], name
