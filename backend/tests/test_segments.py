"""Business segments (backend/segments.py, docs/strategy.md#segments): the owner makes, names and orders them; a teammate is
tagged with the ones it is dedicated to, and its faces and leads follow the org chart; a segment's own strategy lives in
the draft and the versions; a dedicated bot's run is handed the published text of its segments. The migration that folds
Next into Us and the one that adds the bet's segment run on the tables they were written for. Examples use Acme."""

import importlib.util
import json
import sqlite3
from pathlib import Path

from backend import lenses as LN
from backend import migrations
from backend import segments as SG
from backend import strategy as S
from backend import strategy_market as SM
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.store import H
from backend.tests.test_api import api, get, post, put, setup_attempt  # noqa: F401
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_market_spend import defined


def ids(api):
    return [s["id"] for s in get(api, "segments")["segments"]]


def segment(api, name, who="", color=None, token="ana-test", expected=200):
    body = {"name": name, "who": who}
    if color is not None:
        body["color"] = color
    return post(api, "segments", body, token=token, expected=expected)


def draft_revision(api):
    return get(api, "strategy")["draft"]["revision"]


def ready(api):
    """A draft that may be published: the company's summary, a won't-do and one active bet."""
    put(
        api,
        "strategy/draft",
        {
            "revision": draft_revision(api),
            "summary": "Acme runs cleaning with bots.",
            "sections": {"not": "No enterprise deals."},
        },
    )
    post(api, "strategy/bets", {"statement": "Small offices pay for bots", "status": "active"})


def publish(api, note="First"):
    return post(api, "strategy/publish", {"note": note, "revision": draft_revision(api)})


def write_segment(api, sid, sections, revision=None, token="ana-test", expected=200):
    if revision is None:
        revision = draft_revision(api)
    return put(
        api,
        "segments/strategy",
        {"id": sid, "revision": revision, "sections": sections},
        token=token,
        expected=expected,
    )


def test_only_the_owner_makes_and_changes_the_segments(api):
    defined(api)
    _, _, bot = setup_attempt(api, "ops")
    segment(api, "Screening", token="ben-test", expected=403)  # a bot admin tags teammates, but makes no segments
    segment(api, "Screening", token=bot["token"], expected=403)
    made = segment(api, "Screening")["segment"]
    assert made["id"] == "segment/screening"
    post(api, "segments/" + made["id"], {"who": "Landlords"}, token="ben-test", expected=403)
    post(api, "segments/remove", {"id": made["id"]}, token=bot["token"], expected=403)
    post(api, "segments/order", {"ids": [made["id"]]}, token="cara-test", expected=403)
    assert ids(api) == [made["id"]]


def test_segments_take_the_next_colour_and_list_in_the_order_the_owner_sets(api):
    defined(api)
    first = segment(api, "Screening", who="Landlords with 1-20 units")["segment"]
    second = segment(api, "Payments")["segment"]
    assert (first["color"], second["color"]) == ("blue", "green")
    assert (first["rank"], second["rank"]) == (0, 1)
    assert first["who"] == "Landlords with 1-20 units"
    assert ids(api) == [first["id"], second["id"]]
    post(api, "segments/order", {"ids": [second["id"], first["id"]]})
    assert ids(api) == [second["id"], first["id"]]
    # Every segment the company plays in, once each: a missing or repeated one is refused and the order stays.
    post(api, "segments/order", {"ids": [second["id"]]}, expected=422)
    post(api, "segments/order", {"ids": [second["id"], second["id"]]}, expected=422)
    assert ids(api) == [second["id"], first["id"]]


def test_a_new_segment_lands_last_when_the_ones_before_it_have_no_rank_yet(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE market_entities SET properties='{}' WHERE id=?", (first,))  # us from before ranks
    made = segment(api, "Payments")["segment"]
    assert [s["id"] for s in get(api, "segments")["segments"]] == [first, made["id"]]
    assert made["rank"] == 1


def test_a_segment_is_named_and_coloured_within_its_limits_and_renamed_in_place(api):
    first, second, sub = defined(api)
    segment(api, "x" * 121, expected=422)
    segment(api, "Screening", who="y" * 201, expected=422)
    segment(api, "Screening", color="black", expected=422)
    made = segment(api, "Screening", color="teal")["segment"]
    assert made["color"] == "teal"
    renamed = post(api, "segments/" + made["id"], {"name": "Tenant screening", "who": "", "color": "red"})["segment"]
    assert (renamed["id"], renamed["name"], renamed["who"], renamed["color"]) == (
        made["id"],
        "Tenant screening",
        "",
        "red",
    )
    # A name another segment of the company has is a 409 that names it: the segment keeps its own id.
    other = segment(api, "Payments")["segment"]
    clash = post(api, "segments/" + made["id"], {"name": "Payments"}, expected=409)
    assert other["id"] in str(clash)
    # A market segment that is not the company's is not renamed through the segments API.
    post(api, "segments/" + sub, {"name": "Credit checks again"}, expected=404)


def test_a_segment_named_like_a_route_keeps_its_own_routes(api):
    defined(api)
    named = segment(api, "Remove")["segment"]
    assert named["id"] == "segment/remove"
    assert post(api, "segments/" + named["id"], {"who": "Keeps its own route"})["segment"]["who"] == (
        "Keeps its own route"
    )
    assert post(api, "segments/remove", {"id": named["id"]}) == {"id": named["id"], "removed": True}


def test_a_market_segment_of_that_name_joins_and_a_stopped_one_joins_again_with_its_id(api):
    first, _, _ = defined(api)
    joined = segment(api, "Screening", who="Landlords with 1-20 units")["segment"]
    assert joined["id"] == first and joined["who"] == "Landlords with 1-20 units" and ids(api) == [first]
    segment(api, "Screening", expected=409)  # already one of the company's
    assert post(api, "segments/remove", {"id": first}) == {"id": first, "removed": True}
    assert ids(api) == []  # the market keeps the segment; the company stops playing in it
    assert segment(api, "Screening")["segment"]["id"] == first


def test_a_merged_segment_name_joins_the_segment_it_merged_into_and_never_reactivates_it(api):
    """The merged name is an alias of the segment it merged into, so creating it reuses that active segment (the company
    plays in it now); the merged one is not reactivated."""
    first, second, _ = defined(api)
    post(api, f"market/entities/{first}/merge", {"into": second})
    joined = segment(api, "Screening")
    assert joined["segment"]["id"] == second
    assert first not in ids(api)
    assert second in ids(api)


def test_a_bet_names_one_of_the_companys_segments_or_none(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    bet = post(api, "strategy/bets", {"statement": "Landlords pay", "segment_id": first})["bet"]
    assert bet["segment_id"] == first
    post(api, "strategy/bets", {"statement": "Late fees", "segment_id": second}, expected=404)
    post(api, f"strategy/bets/{bet['id']}", {"segment_id": second}, expected=404)
    cleared = post(api, f"strategy/bets/{bet['id']}", {"segment_id": ""})["bet"]
    assert cleared["segment_id"] is None


def test_teammates_are_tagged_with_the_companys_segments_by_the_owner_only(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    _, _, bot = setup_attempt(api, "ops")
    post(api, "segments/members", {"member": "bot:ops", "segments": [second]}, expected=404)  # not the company's
    post(api, "segments/members", {"member": "person:ghost", "segments": [first]}, expected=404)
    post(api, "segments/members", {"member": "bot:ops", "segments": [first, "all"]}, expected=422)  # all stands alone
    tagged = post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    assert tagged == {"member": "bot:ops", "segments": [first]}
    post(api, "segments/members", {"member": "person:cara", "segments": [first]}, token="ben-test", expected=403)
    post(api, "segments/members", {"member": "person:cara", "segments": [first]})
    post(api, "segments/members", {"member": "person:cara", "segments": [first]}, token="cara-test", expected=403)
    post(api, "segments/members", {"member": "bot:ops", "segments": []}, token=bot["token"], expected=403)
    listed = get(api, "segments/members")
    assert listed["truncated"] is False
    assert {m["member"]: m["segments"] for m in listed["members"]} == {
        "bot:ops": [first],
        "person:cara": [first],
    }
    post(api, "segments/members", {"member": "bot:ops", "segments": []})
    assert [m["member"] for m in get(api, "segments/members")["members"]] == ["person:cara"]


def test_all_on_its_own_tags_a_teammate_with_every_segment(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    segment(api, "Payments")
    setup_attempt(api, "ops")
    tagged = post(api, "segments/members", {"member": "bot:ops", "segments": ["all"]})
    assert tagged == {"member": "bot:ops", "segments": ["all"]}
    with api.app.state.store.read() as c:
        assert SG.member_segments(c, "bot:ops") == ["all"]
        assert [s["id"] for s in SG.ordered(c)] == [first, second]


def test_faces_are_the_dedicated_teammates_highest_on_the_chart_and_leads_have_no_dedicated_manager(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")  # ops reports to cpo
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    post(api, "segments/members", {"member": "bot:cpo", "segments": [first]})
    detail = get(api, "segments/" + first)
    assert [f["ref"] for f in detail["faces"]] == ["bot:cpo", "bot:ops"]
    assert [f["ref"] for f in detail["leads"]] == ["bot:cpo"]
    assert detail["teammates"] == 2 and detail["faces"][1]["kind"] == "bot"


def test_lead_map_names_for_each_teammate_what_led_by_names(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    segment(api, "Payments")
    setup_attempt(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")  # ops reports to cpo
    post(api, "segments/members", {"member": "bot:ops", "segments": [first, second]})
    post(api, "segments/members", {"member": "bot:cpo", "segments": [first]})
    with api.app.state.store.read() as c:
        mapped = SG.lead_map(c)
        assert mapped == {"bot:cpo": {first}, "bot:ops": {second}}  # ops's manager is dedicated to Screening
        assert all(SG.led_by(c, ref) == sids for ref, sids in mapped.items())


def test_a_cycle_of_dedicated_managers_leads_with_the_highest_face(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")
        c.execute("UPDATE bot_config SET reports_to='ops' WHERE bot='cpo'")  # each manages the other
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    post(api, "segments/members", {"member": "bot:cpo", "segments": [first]})
    detail = get(api, "segments/" + first)
    assert len(detail["leads"]) == 1 and detail["leads"][0] == detail["faces"][0]
    with api.app.state.store.read() as c:
        assert SG.led_by(c, detail["faces"][0]["ref"]) == {first}


def test_an_archived_bot_loses_its_segments_and_leaves_the_faces(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    setup_attempt(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='ops'")  # ops reports to cpo
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    post(api, "segments/members", {"member": "bot:cpo", "segments": [first]})
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='ops'").fetchone()[0]
    post(api, "bots/ops/archive", {"expected_revision": revision})
    assert get(api, "segments/members")["members"] == [{"member": "bot:cpo", "segments": [first]}]
    detail = get(api, "segments/" + first)
    assert [f["ref"] for f in detail["faces"]] == ["bot:cpo"] and detail["teammates"] == 1


def test_forgetting_a_teammate_removes_its_tags_and_only_its_tags(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    segment(api, "Payments")
    post(api, "segments/members", {"member": "bot:ops", "segments": [first, second]})
    post(api, "segments/members", {"member": "bot:cpo", "segments": [second]})
    with api.app.state.store.transaction() as c:
        SG.forget_member(c, "human:ana", "bot:ops")
        assert SG.member_segments(c, "bot:ops") == []
        assert SG.member_segments(c, "bot:cpo") == [second]


def test_a_segment_writes_its_own_text_for_the_sections_it_changes_and_the_company_text_shows_beside_it(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    put(api, "strategy/draft", {"revision": draft_revision(api), "sections": {"serve": "Everyone who rents."}})
    before = draft_revision(api)
    view = write_segment(api, first, {"serve": "Landlords with 1-20 units."})["strategy"]
    fields = {f["key"]: f for f in view["fields"]}
    assert fields["serve"] == {
        "key": "serve",
        "title": S.TITLES["serve"],
        "text": "Landlords with 1-20 units.",
        "company": "Everyone who rents.",
        "changed": True,
        "from": "segment",
    }
    assert fields["sell"]["changed"] is False and fields["sell"]["from"] == "company"
    assert fields["summary"]["title"] == "Summary"
    assert (
        write_segment(api, first, {"sell": "Bots at $99."}, revision=before, expected=409)["error"]["code"]
        == "conflict"
    )
    write_segment(api, first, {"sell": "Bots at $99."}, token="ben-test", expected=403)
    cleared = write_segment(api, first, {"serve": ""})["strategy"]
    serve = {f["key"]: f for f in cleared["fields"]}["serve"]
    assert serve["changed"] is False and serve["text"] == "Everyone who rents." and serve["from"] == "company"
    # Someone who may not edit the draft reads the published version, not the draft.
    reader = get(api, "segments/" + first, token="ben-test")["strategy"]
    assert reader["draft"] is False and reader["fields"] == []


def test_a_segments_text_publishes_with_the_rest_and_the_diff_names_it(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    ready(api)
    publish(api, "First")
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})
    assert publish(api, "Second")["n"] == 2
    keys = [f["key"] for f in get(api, "strategy/diff?from=1&to=2")["fields"]]
    assert f"sections.segments.{first}.serve" in keys
    published = get(api, "segments/" + first, token="ben-test")["strategy"]
    assert published["version"] == 2
    serve = {f["key"]: f for f in published["fields"]}["serve"]
    assert serve["changed"] is True and serve["text"] == "Landlords with 1-20 units."


def test_a_reader_who_may_not_edit_gets_the_published_text_while_the_draft_differs(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    ready(api)
    publish(api, "First")
    write_segment(api, first, {"serve": "Published landlords."})
    publish(api, "Second")
    write_segment(api, first, {"serve": "Draft landlords."})  # the draft only
    owner = {f["key"]: f for f in get(api, "segments/" + first)["strategy"]["fields"]}
    assert owner["serve"]["text"] == "Draft landlords."
    reader = get(api, "segments/" + first, token="ben-test")["strategy"]
    assert reader["draft"] is False and reader["version"] == 2
    assert {f["key"]: f for f in reader["fields"]}["serve"]["text"] == "Published landlords."


def test_the_company_strategy_payload_carries_no_segment_text(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    ready(api)
    publish(api, "First")
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})
    publish(api, "Second")
    owner = get(api, "strategy")
    assert "segments" not in owner["draft"]["sections"] and "segments" not in owner["current"]["sections"]
    reader = get(api, "strategy", token="ben-test")
    assert "segments" not in reader["current"]["sections"]
    # The segment's own text stays where it is read: its own route.
    assert {f["key"]: f for f in get(api, "segments/" + first)["strategy"]["fields"]}["serve"]["changed"] is True


def test_a_segment_only_edit_counts_as_a_change_for_publish_until_it_is_published(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    ready(api)
    publish(api, "First")
    assert get(api, "strategy")["segment_changes"] == 0
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})
    assert get(api, "strategy")["segment_changes"] == 1
    assert get(api, "strategy", token="ben-test")["segment_changes"] == 0  # no draft is shown to a reader
    assert publish(api, "Second")["n"] == 2
    assert get(api, "strategy")["segment_changes"] == 0
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})  # the same text is no change
    assert get(api, "strategy")["segment_changes"] == 0


def test_a_dedicated_bot_is_handed_the_published_text_of_its_segments_and_not_the_draft(api):
    first, _, _ = defined(api)
    segment(api, "Screening", who="Landlords with 1-20 units")
    segment(api, "Payments")
    ready(api)
    publish(api)
    head = "Segment: Screening — Landlords with 1-20 units"
    with api.app.state.store.read() as c:
        ops = H.resolve_bot(c, "ops")["id"]
        assert SG.run_text(c, ops, S.current(c)) == ""  # no tag, no text
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    write_segment(api, first, {"summary": "Small landlords, one building at a time."})  # in the draft only
    with api.app.state.store.read() as c:
        assert SG.run_text(c, ops, S.current(c)) == head
    publish(api, "Second")
    with api.app.state.store.read() as c:
        assert SG.run_text(c, ops, S.current(c)) == head + "\nSmall landlords, one building at a time."
    post(api, "segments/members", {"member": "bot:ops", "segments": ["all"]})
    with api.app.state.store.read() as c:
        assert SG.run_text(c, ops, S.current(c)) == ""  # a tag of all alone hands no segment text


def test_a_run_is_handed_at_most_run_max_characters_of_segment_text(api, monkeypatch):
    first, second, _ = defined(api)
    segment(api, "Screening")
    segment(api, "Payments")
    ready(api)
    write_segment(api, first, {"summary": "A" * 300})
    write_segment(api, second, {"summary": "B" * 300})
    publish(api)
    setup_attempt(api, "ops")
    post(api, "segments/members", {"member": "bot:ops", "segments": [first, second]})
    monkeypatch.setattr(SG, "RUN_MAX", 200)
    with api.app.state.store.read() as c:
        ops = H.resolve_bot(c, "ops")["id"]
        text = SG.run_text(c, ops, S.current(c))
    assert len(text) <= 200 and text.endswith("…") and text.startswith("Segment: Screening")


def test_a_merge_moves_a_segments_tags_bets_and_draft_text_and_stopping_takes_the_tags_off(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    segment(api, "Payments")
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    bet = post(api, "strategy/bets", {"statement": "Landlords pay", "segment_id": first})["bet"]
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})
    post(api, f"market/entities/{first}/merge", {"into": second})
    assert ids(api) == [second]
    assert {m["member"]: m["segments"] for m in get(api, "segments/members")["members"]} == {"bot:ops": [second]}
    assert [b["id"] for b in get(api, "segments/" + second)["bets"]] == [bet["id"]]
    moved = {f["key"]: f for f in get(api, "segments/" + second)["strategy"]["fields"]}
    assert moved["serve"]["text"] == "Landlords with 1-20 units." and moved["serve"]["changed"] is True
    post(api, "segments/remove", {"id": second})
    assert ids(api) == [] and get(api, "segments/members")["members"] == []  # the tags go with it
    kept = next(b for b in get(api, "strategy")["bets"] if b["id"] == bet["id"])
    assert kept["segment_id"] == second  # the bet keeps the segment it names
    assert segment(api, "Payments")["segment"]["id"] == second
    assert get(api, "segments/members")["members"] == []  # joining again does not bring the tags back


def test_an_assistant_card_the_owner_confirms_passes_the_owner_gate_to_make_a_segment(api):
    defined(api)
    _r, attempt = assistant_turn(api, "ana-test")
    operation = {
        "summary": "Make Screening a segment",
        "method": "POST",
        "path": "/api/v2/segments",
        "body": {"name": "Screening", "who": "Landlords with 1-20 units"},
    }
    action = post(api, "assistant/actions", operation, token=attempt["token"])["action"]
    assert action["status"] == "pending"
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done" and done["result"]["status_code"] == 200, done["result"]
    assert ids(api) == ["segment/screening"]
    assert get(api, "segments")["segments"][0]["who"] == "Landlords with 1-20 units"


def test_two_lens_changes_to_one_segment_keep_both_their_keys(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    with api.app.state.store.read() as c:
        merged = LN.merged(
            c,
            [
                {"payload": {"sections": {"segments": {first: {"summary": "Landlords."}}}}},
                {"payload": {"sections": {"segments": {first: {"serve": "Small landlords."}}}}},
                {"payload": {"sections": {"segments": {first: {"summary": "Landlords with 1-20 units."}}}}},
            ],
        )
    assert merged["sections"]["segments"] == {
        first: {"summary": "Landlords with 1-20 units.", "serve": "Small landlords."}
    }


def test_the_public_helpers_list_the_segments_without_faces_and_colour_the_unstamped(api):
    first, second, _ = defined(api)
    segment(api, "Screening", who="Landlords")
    segment(api, "Payments")
    with api.app.state.store.read() as c:
        rows = SG.ordered(c)
    assert [(r["id"], r["color"], r["rank"]) for r in rows] == [(first, "blue", 0), (second, "green", 1)]
    assert set(rows[0]) == {"id", "name", "who", "color", "rank"}
    assert SG.color_of({"properties": {}}, 3) == "red"
    assert SG.color_of({"properties": {"color": "teal"}}, 0) == "teal"


def test_a_bet_from_before_segments_is_no_change_against_one_with_no_segment():
    assert S._same_bet_field({"statement": "x"}, {"segment_id": None, "statement": "x"}, "segment_id")
    assert not S._same_bet_field({"segment_id": "segment/a"}, {"segment_id": None}, "segment_id")


def test_a_change_to_who_is_tagged_moves_the_finance_view_version(api):
    """The finance views key their caches on fin_view_versions: a tag that is set, cleared or forgotten moves it once;
    a write that changes no tag moves nothing."""
    first, _, _ = defined(api)
    segment(api, "Screening")
    setup_attempt(api, "ops")

    def version():
        with api.app.state.store.read() as c:
            return FR.view_version(c)

    before = version()
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    tagged = version()
    assert tagged == before + 1
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})  # the same tags: nothing moved
    assert version() == tagged
    post(api, "segments/members", {"member": "bot:ops", "segments": []})
    assert version() == tagged + 1
    with api.app.state.store.transaction() as c:
        SG.forget_member(c, "human:ana", "bot:ops")  # no tag left to forget: nothing moved
    assert version() == tagged + 1
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    with api.app.state.store.transaction() as c:
        SG.forget_member(c, "human:ana", "bot:ops")  # the archive path: its tags go and the view moves
    assert version() == tagged + 3


def test_the_finance_view_bump_does_nothing_where_the_finance_tables_are_absent():
    conn = sqlite3.connect(":memory:")
    FA.segment_tags_changed(conn, "segment tags")
    assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def _migration(name):
    path = Path(migrations.__file__).parent / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAMP = "2026-01-01T00:00:00.000Z"
FOLD = "20261107005648_segment_next_folds_into_ours.py"


def market_before_segments(company_name="Acme Cleaning"):
    """A database as it stood before segments: the market's tables and the onboarding names, and no company yet."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(H.MARKET_SCHEMA)
    conn.execute("CREATE TABLE registry_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    onboarding = json.dumps({"names": {"company_name": company_name}})
    conn.execute("INSERT INTO registry_metadata VALUES ('onboarding', ?)", (onboarding,))
    return conn


def add_entity(conn, sid, kind, name, props):
    conn.execute(
        "INSERT INTO market_entities (id, type, name, properties, created_at, created_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'test', ?)",
        (sid, kind, name, json.dumps(props), STAMP, STAMP),
    )


def add_us(conn, dst):
    conn.execute(
        "INSERT INTO market_edges (id, src, rel, dst, valid_from_on, created_at, created_by, updated_at) "
        "VALUES (?, 'company/self', 'operates_in', ?, '2026-01-01', ?, 'test', ?)",
        (f"edge-{dst}", dst, STAMP, STAMP),
    )


def props_of(conn):
    return {
        sid: json.loads(raw)
        for sid, raw in conn.execute("SELECT id, properties FROM market_entities WHERE type='segment' ORDER BY id")
    }


def test_next_folds_into_ours_on_a_database_that_already_has_the_market():
    conn = market_before_segments()
    add_entity(conn, "segment/office", "segment", "Office cleaning", {"next": True, "color": "red"})
    add_entity(conn, "segment/home", "segment", "Home cleaning", {"next": True})
    add_entity(conn, "segment/sizes", "segment", "Sizes", {"rank": 2})
    add_entity(conn, "segment/parks", "segment", "Parks", {"next": False})
    _migration(FOLD).up(conn)
    assert conn.execute("SELECT name FROM market_entities WHERE id='company/self'").fetchall() == [("Acme Cleaning",)]
    assert sorted(conn.execute("SELECT src, dst FROM market_edges WHERE rel='operates_in'").fetchall()) == [
        ("company/self", "segment/home"),
        ("company/self", "segment/office"),
    ]
    # Fresh folds land last in name order and take a color no other has; the rest keep what they had.
    assert props_of(conn) == {
        "segment/home": {"color": "blue", "rank": 0},
        "segment/office": {"color": "red", "rank": 1},
        "segment/parks": {"next": False},
        "segment/sizes": {"rank": 2},
    }


def test_a_fold_lands_after_the_ones_that_are_us_takes_a_free_color_and_cites_one_evidence_row():
    conn = market_before_segments()
    add_entity(conn, "company/self", "company", "Acme Cleaning", {})
    add_entity(conn, "segment/a", "segment", "A", {"rank": 0, "color": "blue"})
    add_entity(conn, "segment/b", "segment", "B", {"rank": 1, "color": "green"})
    add_us(conn, "segment/a")
    add_us(conn, "segment/b")
    add_entity(conn, "segment/c", "segment", "C", {"next": True, "color": "blue"})  # taken: the first free one
    add_entity(conn, "segment/d", "segment", "D", {"next": True})
    _migration(FOLD).up(conn)
    props = props_of(conn)
    assert props["segment/c"] == {"color": "amber", "rank": 2}
    assert props["segment/d"] == {"color": "red", "rank": 3}
    assert props["segment/a"] == {"color": "blue", "rank": 0} and props["segment/b"] == {"color": "green", "rank": 1}
    assert conn.execute("SELECT COUNT(*) FROM market_evidence WHERE captured_by='migration'").fetchone() == (1,)
    assert conn.execute("SELECT claim_kind, COUNT(*) FROM market_citations GROUP BY claim_kind").fetchall() == [
        ("edge", 2)
    ]


def test_a_segment_already_us_keeps_its_one_edge_and_loses_only_the_mark():
    conn = market_before_segments()
    add_entity(conn, "company/self", "company", "Acme Cleaning", {})
    add_entity(conn, "segment/home", "segment", "Home cleaning", {"next": True, "color": "teal"})
    add_us(conn, "segment/home")
    _migration(FOLD).up(conn)
    assert conn.execute("SELECT COUNT(*) FROM market_edges").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM market_entities WHERE id='company/self'").fetchone() == (1,)
    # A Us segment with no rank yet is ranked first, in its current order, and loses the mark.
    assert conn.execute("SELECT properties FROM market_entities WHERE id='segment/home'").fetchone() == (
        '{"color": "teal", "rank": 0}',
    )


def test_a_fold_without_a_registry_names_the_company_our_company():
    conn = sqlite3.connect(":memory:")
    conn.executescript(H.MARKET_SCHEMA)  # no registry_metadata table at all
    add_entity(conn, "segment/office", "segment", "Office cleaning", {"next": True})
    _migration(FOLD).up(conn)
    assert conn.execute("SELECT name FROM market_entities WHERE id='company/self'").fetchone() == ("Our company",)
    assert conn.execute("SELECT COUNT(*) FROM market_edges WHERE rel='operates_in'").fetchone() == (1,)


def test_a_new_file_has_no_market_to_fold():
    conn = sqlite3.connect(":memory:")
    _migration(FOLD).up(conn)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone() == (0,)


def test_the_bet_segment_column_is_added_to_a_strategy_table_that_lacks_it_once():
    module = _migration("20261107005649_strategy_bet_segment.py")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE strategy_bets (id TEXT PRIMARY KEY, statement TEXT NOT NULL)")
    module.up(conn)
    module.up(conn)
    assert [row[1] for row in conn.execute("PRAGMA table_info(strategy_bets)")] == ["id", "statement", "segment_id"]
    module.up(sqlite3.connect(":memory:"))  # a new file has no bets table yet


def test_only_the_owner_on_their_click_tags_a_teammate_with_a_segment(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    setup_attempt(api, "ops")
    post(
        api, "segments/members", {"member": "bot:ops", "segments": [first]}, token="ben-test", expected=403
    )  # bot admin
    _, attempt = assistant_turn(api, "ana-test")
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]}, token=attempt["token"], expected=403)
    assert get(api, "segments/members")["members"] == []
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    assert [m["member"] for m in get(api, "segments/members")["members"]] == ["bot:ops"]


def test_a_segment_strategy_write_names_the_draft_revision_it_read(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    put(api, "segments/strategy", {"id": first, "sections": {"serve": "Landlords with 1-20 units."}}, expected=422)
    read = draft_revision(api)
    out = write_segment(api, first, {"serve": "Landlords with 1-20 units."}, revision=read)
    assert out["strategy"]["revision"] == read + 1
    assert [f["text"] for f in out["strategy"]["fields"] if f["key"] == "serve"] == ["Landlords with 1-20 units."]
    write_segment(api, first, {"serve": "Stale."}, revision=read, expected=409)


def test_a_confirmed_card_writes_its_keys_without_a_revision_and_leaves_the_others_alone(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    ready(api)
    write_segment(api, first, {"serve": "Landlords with 1-20 units.", "sell": "Bots at $99."})
    _r, attempt = assistant_turn(api, "ana-test")
    operation = {
        "summary": "Change Screening's serve",
        "method": "PUT",
        "path": "/api/v2/segments/strategy",
        "body": {"id": first, "sections": {"serve": "Small landlords."}},
    }
    action = post(api, "assistant/actions", operation, token=attempt["token"])["action"]
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done" and done["result"]["status_code"] == 200, done["result"]
    text = {f["key"]: f["text"] for f in get(api, "segments/" + first)["strategy"]["fields"]}
    assert text["serve"] == "Small landlords." and text["sell"] == "Bots at $99."
    assert get(api, "strategy")["draft"]["sections"]["not"] == "No enterprise deals."


def test_an_unconfirmed_assistant_call_is_refused_with_or_without_a_revision(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    ready(api)
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})
    _r, attempt = assistant_turn(api, "ana-test")
    body = {"id": first, "sections": {"serve": "Assistant."}}
    put(api, "segments/strategy", {**body, "revision": draft_revision(api)}, token=attempt["token"], expected=403)
    put(api, "segments/strategy", body, token=attempt["token"], expected=403)
    text = {f["key"]: f["text"] for f in get(api, "segments/" + first)["strategy"]["fields"]}
    assert text["serve"] == "Landlords with 1-20 units."


def test_the_owner_without_a_revision_gets_422_and_a_stale_one_409(api):
    first, _, _ = defined(api)
    segment(api, "Screening")
    ready(api)
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})
    refused = put(api, "segments/strategy", {"id": first, "sections": {"serve": "No revision."}}, expected=422)
    assert refused["error"]["code"] == "validation"
    write_segment(api, first, {"serve": "Stale."}, revision=draft_revision(api) - 1, expected=409)
    text = {f["key"]: f["text"] for f in get(api, "segments/" + first)["strategy"]["fields"]}
    assert text["serve"] == "Landlords with 1-20 units."


def test_a_merge_moves_the_draft_text_and_leaves_the_published_version_as_it_was(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    segment(api, "Payments")
    ready(api)
    write_segment(api, first, {"summary": "Small landlords, one building at a time."})
    publish(api)
    with api.app.state.store.read() as c:
        published = S.current(c)
    post(api, f"market/entities/{first}/merge", {"into": second})
    with api.app.state.store.read() as c:
        assert S.current(c) == published  # history is not edited
        row = c.execute(
            "SELECT sections FROM strategy_versions WHERE status='draft' ORDER BY n DESC LIMIT 1"
        ).fetchone()
    draft_segments = json.loads(row[0])["segments"]
    assert first not in draft_segments
    assert draft_segments[second]["summary"] == "Small landlords, one building at a time."


def test_the_segment_finance_and_bot_context_routes_are_cards_and_only_those_routes_are():
    """The Assistant proposes these by card (`hub assistant propose`): each route is proposable, and its neighbours in
    the same shape are not."""
    from backend.assistant import valid_operation

    for method, path in (
        ("POST", "/api/v2/segments/members"),
        ("PUT", "/api/v2/segments/strategy"),
        ("POST", "/api/v2/segments/segment/screening"),
        ("POST", "/api/v2/finance/contacts/cust-1/segment"),
        ("POST", "/api/v2/bots/ops/compact-at"),
    ):
        assert valid_operation(method, path), (method, path)
    for method, path in (
        ("GET", "/api/v2/segments"),
        ("POST", "/api/v2/finance/actions"),  # budgets: the Finance Manager proposes them, not the Assistant
        ("PUT", "/api/v2/finance/actions"),
        ("POST", "/api/v2/finance/contacts/cust-1"),
        ("POST", "/api/v2/finance/contacts/cust-1/segment/extra"),
    ):
        assert not valid_operation(method, path), (method, path)


def test_a_compact_at_card_writes_nothing_until_the_owner_taps_it(api):
    setup_attempt(api, "ops")
    _r, attempt = assistant_turn(api, "ana-test")
    before = get(api, "bots/ops")["revision"]
    operation = {
        "summary": "Compact ops at 250k",
        "method": "POST",
        "path": "/api/v2/bots/ops/compact-at",
        "body": {"compact_at": 250000, "expected_revision": before},
    }
    action = post(api, "assistant/actions", operation, token=attempt["token"])["action"]
    assert action["status"] == "pending"
    assert get(api, "bots/ops")["revision"] == before  # a pending card has changed nothing
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done" and done["result"]["status_code"] == 200, done["result"]
    assert get(api, "bots/ops")["revision"] == before + 1


def test_a_turns_own_write_to_a_segment_or_a_customers_segment_is_refused(api):
    """The owner's segment writes and a customer's segment are cards: an Assistant turn's own write is refused (the NEVER
    list in backend/assistant.py, and the owner's gate behind it), and nothing changes."""
    from backend.assistant import admits_on_turn

    assert not admits_on_turn("POST", "/api/v2/finance/contacts/cust-1/segment", {"segment_id": "segment/x"})
    assert not admits_on_turn("POST", "/api/v2/segments/members", {"member": "bot:ops", "segments": []})
    assert not admits_on_turn("PUT", "/api/v2/segments/strategy", {"id": "segment/x", "sections": {}})
    seg = segment(api, "Screening")["segment"]["id"]
    _r, attempt = assistant_turn(api, "ana-test")
    token = attempt["token"]
    before = get(api, "segments/members")
    post(api, "segments/members", {"member": "bot:ops", "segments": [seg]}, token=token, expected=403)
    put(
        api,
        "segments/strategy",
        {"id": seg, "sections": {"summary": "Landlords pay on time."}},
        token=token,
        expected=403,
    )
    post(api, f"segments/{seg}", {"who": "Landlords"}, token=token, expected=403)
    assert get(api, "segments/members") == before


def test_a_confirmed_customer_segment_card_stores_the_persons_choice(api):
    """The owner taps the card: the customer's segment is their own choice (segment_sure 1), not a guess."""
    from backend.tests.test_finance_fixtures import AT
    from backend.tests.test_finance_fixtures import act as finance_act

    seg = segment(api, "Small businesses")["segment"]["id"]
    store = api.app.state.store
    with store.transaction() as c:
        FA.setup(c, "human:ana", AT)
        customer = finance_act(c, "human:ana", "contact_create", {"name": "Fabrikam", "kind": "customer"})
        cid = str(customer.result["contact_id"])
    _r, attempt = assistant_turn(api, "ana-test")
    operation = {
        "summary": "Put Fabrikam in Small businesses",
        "method": "POST",
        "path": f"/api/v2/finance/contacts/{cid}/segment",
        "body": {"segment_id": seg},
    }
    action = post(api, "assistant/actions", operation, token=attempt["token"])["action"]
    assert action["status"] == "pending"
    done = post(api, f"assistant/actions/{action['id']}/confirm", {}, token="ana-test")["action"]
    assert done["status"] == "done" and done["result"]["status_code"] == 200, done["result"]
    with store.read() as c:
        row = c.execute("SELECT segment_id, segment_sure FROM fin_contacts WHERE id=?", (cid,)).fetchone()
    assert tuple(row) == (seg, 1)
