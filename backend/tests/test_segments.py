"""Business segments (backend/segments.py, docs/strategy.md#segments): the owner makes, names and orders them; a teammate is
tagged with the ones it is dedicated to, and its faces and leads follow the org chart; a segment's own strategy lives in
the draft and the versions; a dedicated bot's run is handed the published text. The two migrations that fold Next into
Us and add the bet's segment run on the tables they were written for. Examples use Acme."""

import importlib.util
import json
import sqlite3
from pathlib import Path

from backend import migrations
from backend import segments as SG
from backend import strategy as S
from backend.store import H
from backend.tests.test_api import api, get, post, put, setup_attempt  # noqa: F401
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
        api, f"segments/{sid}/strategy", {"revision": revision, "sections": sections}, token=token, expected=expected
    )


def test_only_the_owner_makes_and_changes_the_segments(api):
    defined(api)
    _, _, bot = setup_attempt(api, "ops")
    segment(api, "Screening", token="ben-test", expected=403)  # a bot admin tags teammates, but makes no segments
    segment(api, "Screening", token=bot["token"], expected=403)
    made = segment(api, "Screening")["segment"]
    assert made["id"] == "segment/screening"
    post(api, "segments/" + made["id"], {"who": "Landlords"}, token="ben-test", expected=403)
    post(api, "segments/" + made["id"] + "/remove", {}, token=bot["token"], expected=403)
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


def test_a_segment_is_named_and_coloured_within_its_limits_and_renamed_in_place(api):
    defined(api)
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
    # A market segment that is not the company's is not renamed through the segments API.
    post(api, "segments/segment/payments", {"name": "Payments again"}, expected=404)


def test_a_market_segment_of_that_name_joins_and_a_removed_one_comes_back_with_its_id(api):
    first, _, _ = defined(api)
    joined = segment(api, "Screening", who="Landlords with 1-20 units")["segment"]
    assert joined["id"] == first and joined["who"] == "Landlords with 1-20 units" and ids(api) == [first]
    segment(api, "Screening", expected=409)  # already one of the company's
    assert post(api, "segments/" + first + "/remove", {}) == {"id": first, "removed": True}
    assert ids(api) == []
    assert segment(api, "Screening")["segment"]["id"] == first


def test_a_bet_names_one_of_the_companys_segments_or_none(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    bet = post(api, "strategy/bets", {"statement": "Landlords pay", "segment_id": first})["bet"]
    assert bet["segment_id"] == first
    post(api, "strategy/bets", {"statement": "Late fees", "segment_id": second}, expected=404)
    post(api, f"strategy/bets/{bet['id']}", {"segment_id": second}, expected=404)
    cleared = post(api, f"strategy/bets/{bet['id']}", {"segment_id": ""})["bet"]
    assert cleared["segment_id"] is None


def test_teammates_are_tagged_with_the_companys_segments_by_the_owner_or_a_bot_admin(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    _, _, bot = setup_attempt(api, "ops")
    post(api, "segments/members", {"member": "bot:ops", "segments": [second]}, expected=404)  # not the company's
    post(api, "segments/members", {"member": "person:ghost", "segments": [first]}, expected=404)
    tagged = post(api, "segments/members", {"member": "bot:ops", "segments": [first, "all"]})
    assert tagged["member"] == "bot:ops" and sorted(tagged["segments"]) == sorted([first, "all"])
    post(api, "segments/members", {"member": "person:cara", "segments": [first]}, token="ben-test")
    post(api, "segments/members", {"member": "person:cara", "segments": [first]}, token="cara-test", expected=403)
    post(api, "segments/members", {"member": "bot:ops", "segments": []}, token=bot["token"], expected=403)
    listed = get(api, "segments/members")
    assert listed["truncated"] is False
    assert {m["member"]: sorted(m["segments"]) for m in listed["members"]} == {
        "bot:ops": sorted([first, "all"]),
        "person:cara": [first],
    }
    post(api, "segments/members", {"member": "bot:ops", "segments": []})
    assert [m["member"] for m in get(api, "segments/members")["members"]] == ["person:cara"]


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
    }
    assert fields["sell"]["changed"] is False and fields["summary"]["title"] == "Summary"
    assert (
        write_segment(api, first, {"sell": "Bots at $99."}, revision=before, expected=409)["error"]["code"]
        == "conflict"
    )
    write_segment(api, first, {"sell": "Bots at $99."}, token="ben-test", expected=403)
    cleared = write_segment(api, first, {"serve": ""})["strategy"]
    serve = {f["key"]: f for f in cleared["fields"]}["serve"]
    assert serve["changed"] is False and serve["text"] == "Everyone who rents."
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


def test_a_dedicated_bot_is_handed_the_published_text_of_its_segments_and_not_the_draft(api):
    first, _, _ = defined(api)
    segment(api, "Screening", who="Landlords with 1-20 units")
    segment(api, "Payments")
    ready(api)
    publish(api)
    with api.app.state.store.read() as c:
        ops = H.resolve_bot(c, "ops")["id"]
        assert SG.run_text(c, ops) == ""  # no tag, no text
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    write_segment(api, first, {"serve": "Landlords with 1-20 units."})  # in the draft only
    with api.app.state.store.read() as c:
        assert SG.run_text(c, ops) == "Segment: Screening (Landlords with 1-20 units)"
    publish(api, "Second")
    with api.app.state.store.read() as c:
        assert SG.run_text(c, ops) == (
            f"Segment: Screening (Landlords with 1-20 units)\n{S.TITLES['serve']}: Landlords with 1-20 units."
        )
    post(api, "segments/members", {"member": "bot:ops", "segments": ["all"]})
    with api.app.state.store.read() as c:
        text = SG.run_text(c, ops)
    assert text.startswith("Segment: Screening") and "Segment: Payments" in text


def test_a_merge_moves_a_segments_tags_and_bets_and_a_removal_keeps_them(api):
    first, second, _ = defined(api)
    segment(api, "Screening")
    segment(api, "Payments")
    post(api, "segments/members", {"member": "bot:ops", "segments": [first]})
    bet = post(api, "strategy/bets", {"statement": "Landlords pay", "segment_id": first})["bet"]
    post(api, f"market/entities/{first}/merge", {"into": second})
    assert ids(api) == [second]
    assert {m["member"]: m["segments"] for m in get(api, "segments/members")["members"]} == {"bot:ops": [second]}
    assert [b["id"] for b in get(api, "segments/" + second)["bets"]] == [bet["id"]]
    post(api, "segments/" + second + "/remove", {})
    assert ids(api) == [] and get(api, "segments/members")["members"] == []  # the tag waits, unlisted
    kept = next(b for b in get(api, "strategy")["bets"] if b["id"] == bet["id"])
    assert kept["segment_id"] == second
    assert segment(api, "Payments")["segment"]["id"] == second
    assert {m["member"]: m["segments"] for m in get(api, "segments/members")["members"]} == {"bot:ops": [second]}


def _migration(name):
    path = Path(migrations.__file__).parent / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAMP = "2026-01-01T00:00:00.000Z"


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


def test_next_folds_into_ours_on_a_database_that_already_has_the_market():
    conn = market_before_segments()
    add_entity(conn, "segment/office", "segment", "Office cleaning", {"next": True, "color": "red"})
    add_entity(conn, "segment/home", "segment", "Home cleaning", {"next": True})
    add_entity(conn, "segment/sizes", "segment", "Sizes", {"rank": 2})
    add_entity(conn, "segment/parks", "segment", "Parks", {"next": False})
    _migration("20261107005648_segment_next_folds_into_ours.py").up(conn)
    assert conn.execute("SELECT name FROM market_entities WHERE id='company/self'").fetchall() == [("Acme Cleaning",)]
    assert sorted(conn.execute("SELECT src, dst FROM market_edges WHERE rel='operates_in'").fetchall()) == [
        ("company/self", "segment/home"),
        ("company/self", "segment/office"),
    ]
    props = {
        sid: json.loads(raw)
        for sid, raw in conn.execute("SELECT id, properties FROM market_entities WHERE type='segment'")
    }
    assert props == {
        "segment/office": {"color": "red"},
        "segment/home": {},
        "segment/sizes": {"rank": 2},
        "segment/parks": {"next": False},
    }


def test_a_segment_already_us_keeps_its_one_edge_and_loses_only_the_mark():
    conn = market_before_segments()
    add_entity(conn, "company/self", "company", "Acme Cleaning", {})
    add_entity(conn, "segment/home", "segment", "Home cleaning", {"next": True, "color": "teal"})
    add_us(conn, "segment/home")
    _migration("20261107005648_segment_next_folds_into_ours.py").up(conn)
    assert conn.execute("SELECT COUNT(*) FROM market_edges").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM market_entities WHERE id='company/self'").fetchone() == (1,)
    assert conn.execute("SELECT properties FROM market_entities WHERE id='segment/home'").fetchone() == (
        '{"color": "teal"}',
    )


def test_a_new_file_has_no_market_to_fold():
    conn = sqlite3.connect(":memory:")
    _migration("20261107005648_segment_next_folds_into_ours.py").up(conn)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone() == (0,)


def test_the_bet_segment_column_is_added_to_a_strategy_table_that_lacks_it_once():
    module = _migration("20261107005649_strategy_bet_segment.py")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE strategy_bets (id TEXT PRIMARY KEY, statement TEXT NOT NULL)")
    module.up(conn)
    module.up(conn)
    assert [row[1] for row in conn.execute("PRAGMA table_info(strategy_bets)")] == ["id", "statement", "segment_id"]
    module.up(sqlite3.connect(":memory:"))  # a new file has no bets table yet
