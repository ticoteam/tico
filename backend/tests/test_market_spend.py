"""The market by spend (backend/market_spend.py): the definition, segments, sized with cited estimates, figures and the
sources catalog. Examples use Acme and Northwind."""

import pytest

from backend import market_spend as MS
from backend.store import H
from backend.tests.test_api import api, get, post, put, setup_attempt  # noqa: F401
from backend.tests.test_company import with_strategy_bot
from backend.tests.test_market import _librarian, _seed
from backend.tests.test_sql import query
from backend.tests.test_strategy import publish, ready

B = 1_000_000_000


def evidence(api, quote="Analysts count 40,000 small landlords.", token="ana-test"):
    return post(api, "market/evidence", {"source_kind": "news", "quote": quote, "our_read": "A count."}, token=token)["evidence"]["id"]


def defined(api, **more):
    """A market with a root, and two top-level segments with a sub-segment under the first."""
    put(api, "market/definition", {"root_name": "Rental software", "description": "Software for small landlords.",
                                   "year": 2026, "params": {"adoption": {"value": 0.1, "judgement": True}}, **more})
    first = post(api, "market/segments", {"name": "Screening"})["segment"]["id"]
    second = post(api, "market/segments", {"name": "Payments"})["segment"]["id"]
    sub = post(api, "market/segments", {"name": "Credit checks", "parent_id": first})["segment"]["id"]
    return first, second, sub


def size(api, segment, spend=B, low=None, high=None, expected=200, token="ana-test", **more):
    body = {"segment_id": segment, "method": "owner", "spend_usd": spend, "low_usd": spend / 2 if low is None else low,
            "high_usd": spend * 2 if high is None else high, **more}
    out = post(api, "market/sizes", body, token=token, expected=expected)
    return out["size"] if expected == 200 else out


def test_only_the_owner_and_the_librarian_write_the_spend_tables(api):
    first, _, _ = defined(api)
    _librarian(api)
    _, _, librarian = setup_attempt(api, "librarian")
    _, _, other = setup_attempt(api, "finance")
    writes = [("market/segments", {"name": "Leases"}), ("market/sizes", {"segment_id": first, "method": "owner",
              "spend_usd": 10, "low_usd": 5, "high_usd": 20}),
              ("market/sources", {"name": "Public filings", "kind": "filings", "covers": ["players"], "access": "free",
                                  "reliability": "primary"})]
    for path, body in writes:
        post(api, path, body, token="ben-test", expected=403)
        post(api, path, body, token="cara-test", expected=403)
        post(api, path, body, token=other["token"], expected=403)
    assert put(api, "market/definition", {"year": 2027}, token="ben-test", expected=403)
    post(api, "market/segments", {"name": "Leases"}, token=librarian["token"])
    assert get(api, "market/spend", token="cara-test")["definition"]["year"] == 2026         # anyone may read


def test_a_size_is_in_dollars_in_order_and_cites_evidence_unless_the_owner_set_it(api):
    first, _, _ = defined(api)
    proof = evidence(api)
    base = {"segment_id": first, "method": "top_down", "spend_usd": B, "low_usd": B / 2, "high_usd": B * 2}
    post(api, "market/sizes", base, expected=422)                                   # no evidence
    post(api, "market/sizes", {**base, "evidence_ids": ["ev-nothing"]}, expected=404)
    for bad in ({"low_usd": B * 1.5}, {"high_usd": B / 4}, {"low_usd": -1}, {"spend_usd": 1e18, "high_usd": 1e18}):
        post(api, "market/sizes", {**base, "evidence_ids": [proof], **bad}, expected=422)
    post(api, "market/sizes", {**base, "evidence_ids": [proof], "low_usd": None}, expected=422)
    post(api, "market/sizes", {**base, "segment_id": "company/northwind", "evidence_ids": [proof]}, expected=404)
    row = post(api, "market/sizes", {**base, "evidence_ids": [proof]})["size"]
    assert row["status"] == "proposed" and row["year"] == 2026 and row["evidence_ids"] == [proof]
    inline = post(api, "market/sizes", {**base, "evidence": [{"source_kind": "news", "quote": "A count."}]})["size"]
    assert len(inline["evidence_ids"]) == 1
    estimates = next(s for s in get(api, "market/spend")["segments"] if s["id"] == first)["estimates"]
    by_id = {e["id"]: e for e in estimates}
    assert [e["id"] for e in by_id[row["id"]]["evidence"]] == [proof]
    assert set(by_id[row["id"]]["evidence"][0]) == {"id", "source_url", "source_kind", "quote"}
    assert by_id[inline["id"]]["evidence"][0]["quote"] == "A count."
    assert size(api, first, 5 * B)["method"] == "owner"                              # the owner needs none
    long = evidence(api, quote="x" * 1000)
    clipped = post(api, "market/sizes", {**base, "evidence_ids": [long]})["size"]
    seen = next(s for s in get(api, "market/spend")["segments"] if s["id"] == first)["estimates"]
    assert len(next(e for e in seen if e["id"] == clipped["id"])["evidence"][0]["quote"]) == MS.QUOTE_CLIP
    owner = next(e for e in seen if e["method"] == "owner")
    assert owner["evidence"] == []


@pytest.mark.parametrize("formula", [
    "__import__('os').system('x')", "a.b", "a ** 9 ** 9 ** 9", "9 ** 9", "a[0]", "len(a)", "a; b", "a if b else c", "a % b",
    "a, b", "'x'", "a $ b", "$", "a b", "a +", "(a", "a)", "", "   ", "a / (b - b)", "a" + " + a" * 200, "(" * 30 + "a" + ")" * 30])
def test_the_formula_evaluator_takes_arithmetic_over_known_names_and_nothing_else(formula):
    with pytest.raises(MS.FormulaError):
        MS.evaluate(formula, {"a": 2, "b": 3}, {})


def test_a_formula_that_is_one_huge_number_is_refused_not_stored_as_infinity():
    with pytest.raises(MS.FormulaError):
        MS.evaluate("1" + "0" * 400, {}, {})
    with pytest.raises(MS.FormulaError):
        MS.evaluate("big", {"big": float("inf")}, {})


def test_the_formula_evaluator_does_arithmetic_with_local_and_shared_names():
    assert MS.evaluate("a * (b + 1) / 2 - -3", {"a": 4, "b": 2}, {}) == 9
    assert MS.evaluate("units * $adoption", {"units": 1000}, {"adoption": 0.25}) == 250
    with pytest.raises(MS.FormulaError):
        MS.evaluate("units * $missing", {"units": 1}, {})
    with pytest.raises(MS.FormulaError):
        MS.evaluate("units * adoption", {"units": 1}, {"adoption": 1})                # shared needs the $


def test_a_formula_is_worked_out_by_the_server_and_a_bad_one_is_refused_with_a_reason(api):
    first, _, _ = defined(api)
    proof = evidence(api)
    base = {"segment_id": first, "method": "bottom_up", "low_usd": 1, "high_usd": 10 * B, "evidence_ids": [proof],
            "formula": "units * price * $adoption", "params": {"units": {"value": 4000000, "source": "census"},
                                                                  "price": {"value": 300, "judgement": True}}}
    row = post(api, "market/sizes", base)["size"]
    assert row["spend_usd"] == 120_000_000 and row["params"]["$adoption"]["value"] == 0.1
    for formula in ("units * missing", "units ** 2", "__import__('os')"):
        refused = post(api, "market/sizes", {**base, "formula": formula}, expected=422)
        assert refused["error"]["code"] == "formula"
    post(api, "market/sizes", {**base, "params": {"units": {"value": "lots"}}}, expected=422)


def test_a_shared_parameter_change_recomputes_the_spend_and_scales_the_range_on_read(api):
    first, _, _ = defined(api)
    proof = evidence(api)
    row = post(api, "market/sizes", {"segment_id": first, "method": "bottom_up", "spend_usd": 0, "low_usd": 60_000_000,
                                     "high_usd": 300_000_000, "evidence_ids": [proof], "formula": "units * $adoption",
                                     "params": {"units": {"value": 1_000_000_000}}, "accept": True})["size"]
    assert row["spend_usd"] == 100_000_000 and not row["recomputed"]
    put(api, "market/definition", {"params": {"adoption": {"value": 0.2}}})
    seen = next(s for s in get(api, "market/spend")["segments"] if s["id"] == first)["size"]
    assert seen["spend_usd"] == 200_000_000 and seen["recomputed"] and seen["accepted"]
    assert (seen["low_usd"], seen["high_usd"]) == (120_000_000, 600_000_000)
    with api.app.state.store.read() as c:                                            # the stored row is not rewritten
        stored = c.execute("SELECT spend_usd, low_usd FROM market_sizes WHERE id=?", (row["id"],)).fetchone()
    assert (stored["spend_usd"], stored["low_usd"]) == (100_000_000, 60_000_000)


def test_the_owner_changing_an_estimates_parameter_files_an_accepted_owner_row_and_only_the_owner_may(api):
    first, _, _ = defined(api)
    proof = evidence(api)
    row = post(api, "market/sizes", {"segment_id": first, "method": "bottom_up", "spend_usd": 0, "low_usd": 50_000_000,
                                     "high_usd": 200_000_000, "evidence_ids": [proof], "formula": "units * share",
                                     "params": {"units": {"value": 1_000_000_000}, "share": {"value": 0.1}},
                                     "accept": True})["size"]
    _librarian(api)
    _, _, librarian = setup_attempt(api, "librarian")
    post(api, f"market/sizes/{row['id']}/params", {"params": {"share": 0.2}}, token=librarian["token"], expected=403)
    post(api, f"market/sizes/{row['id']}/params", {"params": {"nope": 1}}, expected=422)
    owned = post(api, f"market/sizes/{row['id']}/params", {"params": {"share": 0.2}})["size"]
    assert owned["method"] == "owner" and owned["status"] == "accepted" and owned["spend_usd"] == 200_000_000
    assert owned["high_usd"] == 400_000_000
    old = next(s for s in get(api, "market/spend")["segments"] if s["id"] == first)["estimates"]
    assert {e["id"]: e["status"] for e in old}[row["id"]] == "proposed"


def test_accepting_one_estimate_sends_the_previous_accepted_one_back_to_proposed(api):
    first, _, _ = defined(api)
    one = size(api, first, B)
    two = size(api, first, 3 * B)
    post(api, f"market/sizes/{one['id']}/accept", {})
    post(api, f"market/sizes/{two['id']}/accept", {})
    seen = next(s for s in get(api, "market/spend")["segments"] if s["id"] == first)
    states = {e["id"]: e["status"] for e in seen["estimates"]}
    assert states == {one["id"]: "proposed", two["id"]: "accepted"} and seen["size"]["id"] == two["id"]
    post(api, f"market/sizes/{two['id']}/reject", {})
    seen = next(s for s in get(api, "market/spend")["segments"] if s["id"] == first)
    assert [e["id"] for e in seen["estimates"]] == [one["id"]] and seen["size"]["accepted"] is False


def test_estimates_more_than_twice_apart_are_flagged_as_disagreeing(api):
    first, second, _ = defined(api)
    size(api, first, B)
    size(api, first, 1.9 * B)
    size(api, second, B)
    size(api, second, 2.5 * B)
    rows = {s["id"]: s for s in get(api, "market/spend")["segments"]}
    assert rows[first]["disagree"] is False and rows[second]["disagree"] is True


def test_the_total_sums_the_top_level_segments_only(api):
    first, second, sub = defined(api)
    for segment, amount in ((first, 4 * B), (second, B), (sub, 2 * B)):
        size(api, segment, amount, accept=True)
    seen = get(api, "market/spend")
    assert seen["total"]["spend_usd"] == 5 * B and seen["total"]["low_usd"] == 2.5 * B and seen["total"]["high_usd"] == 10 * B
    by_id = {s["id"]: s for s in seen["segments"]}
    assert by_id[sub]["depth"] == 2 and by_id[sub]["parent"] == first and by_id[first]["depth"] == 1
    assert [s["id"] for s in seen["segments"]] == [second, first, sub]


def test_the_total_counts_a_sized_grandchild_under_unsized_parents(api):
    _, _, _ = defined(api)
    offices = post(api, "market/segments", {"name": "Offices"})["segment"]["id"]
    rooms = post(api, "market/segments", {"name": "Rooms", "parent_id": offices})["segment"]["id"]
    desks = post(api, "market/segments", {"name": "Desks", "parent_id": rooms})["segment"]["id"]
    size(api, desks, 10_000_000, accept=True)
    seen = get(api, "market/spend")
    by_id = {s["id"]: s for s in seen["segments"]}
    assert seen["total"]["spend_usd"] == 10_000_000
    assert by_id[offices]["rolled_usd"] == by_id[rooms]["rolled_usd"] == {"spend_usd": 10_000_000, "low_usd": 5_000_000, "high_usd": 20_000_000}
    assert by_id[desks]["rolled_usd"]["spend_usd"] == 10_000_000 and by_id[offices]["size"] is None


def test_a_shared_parameter_that_puts_a_formula_out_of_range_leaves_no_spend_and_stays_out_of_the_total(api):
    first, second, _ = defined(api)
    proof = evidence(api)
    size(api, second, B, accept=True)
    row = post(api, "market/sizes", {"segment_id": first, "method": "bottom_up", "spend_usd": 0, "low_usd": 1_000_000,
                                     "high_usd": 50_000_000, "evidence_ids": [proof], "accept": True,
                                     "formula": "units * $adoption - 90000000", "params": {"units": {"value": B}}})["size"]
    assert row["spend_usd"] == 10_000_000
    put(api, "market/definition", {"params": {"adoption": {"value": 0.05}}})
    seen = get(api, "market/spend")
    shown = next(s for s in seen["segments"] if s["id"] == first)
    assert shown["size"]["spend_usd"] is None and shown["size"]["low_usd"] is None and not shown["size"]["recomputed"]
    assert "out of range" in shown["size"]["error"] and shown["rolled_usd"] is None
    assert seen["total"]["spend_usd"] == B and shown["disagree"] is False
    from backend import lenses as L
    with api.app.state.store.read() as c:
        sent = L._spend_input(c, {"applies_to": "market"})
    assert [x["spend_usd"] for x in sent["segments"] if x["id"] == first] == [None]


def test_the_tree_is_capped_and_a_segment_needs_a_defined_root(api):
    post(api, "market/segments", {"name": "Early"}, expected=422)
    first, _, sub = defined(api)
    parent = sub
    for n in range(MS.MAX_DEPTH - 2):
        parent = post(api, "market/segments", {"name": f"Level {n}", "parent_id": parent})["segment"]["id"]
    post(api, "market/segments", {"name": "Too deep", "parent_id": parent}, expected=422)
    post(api, "market/segments", {"name": "Screening"}, expected=409)


def test_an_accepted_size_change_on_a_cited_segment_trips_the_bet_once_and_a_proposed_one_does_not(api):
    with_strategy_bot(api)
    first, _, _ = defined(api)
    ready(api, cites=[first], wrong_if={"kind": "thesis", "market_id": first})
    publish(api)
    one = size(api, first, B)
    two = size(api, first, 2 * B)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Bet %'").fetchone()[0] == 0
    post(api, f"market/sizes/{one['id']}/accept", {})
    post(api, f"market/sizes/{two['id']}/accept", {})
    with api.app.state.store.read() as c:
        tasks = H._rows(c.execute("SELECT title, owner_id, body FROM tasks WHERE title LIKE 'Bet %'"))
    assert [(t["title"], t["owner_id"]) for t in tasks] == [("Bet 1: may be wrong", "bot:goal-manager")]
    assert first in tasks[0]["body"]


def test_a_shared_parameter_change_trips_a_bet_on_a_segment_that_uses_it(api):
    with_strategy_bot(api)
    first, _, _ = defined(api)
    proof = evidence(api)
    post(api, "market/sizes", {"segment_id": first, "method": "bottom_up", "spend_usd": 0, "low_usd": 1, "high_usd": B,
                               "evidence_ids": [proof], "formula": "units * $adoption", "params": {"units": {"value": B}},
                               "accept": True})
    ready(api, cites=[first], wrong_if={"kind": "thesis", "market_id": first})
    publish(api)
    put(api, "market/definition", {"params": {"adoption": {"value": 0.3}}})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Bet %'").fetchone()[0] == 1


def test_asking_for_the_market_to_be_sized_files_one_task_and_asking_again_returns_it(api):
    post(api, "market/spend/research", {"description": "Software for small landlords"}, expected=409)    # no Librarian yet
    _librarian(api)
    post(api, "market/spend/research", {"description": "Software for small landlords"}, token="ben-test", expected=403)
    first = post(api, "market/spend/research", {"description": "Software for small landlords"})
    again = post(api, "market/spend/research", {"description": "A different words"})
    assert first["task_id"] == again["task_id"]
    with api.app.state.store.read() as c:
        rows = H._rows(c.execute("SELECT owner_id, title, body FROM tasks WHERE title LIKE 'Size the market%'"))
    assert len(rows) == 1 and rows[0]["owner_id"] == "bot:librarian" and "size-the-market.md" in rows[0]["body"]
    assert get(api, "market/spend")["definition"]["description"] == "Software for small landlords"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (first["task_id"],))
    later = post(api, "market/spend/research", {"description": "Software for small landlords"})
    assert later["task_id"] != first["task_id"]


def test_the_value_chain_puts_each_neighbour_in_its_layer_and_caps_a_layer(api, tmp_path):
    _seed(api, tmp_path)
    proof = evidence(api)
    names = {}

    def company(name):
        names[name] = post(api, "market/entities", {"type": "company", "name": name, "evidence_ids": [proof]})["entity"]["id"]
        return names[name]

    def edge(src, rel, dst, **props):
        post(api, "market/edges", {"src": src, "rel": rel, "dst": dst, "evidence_ids": [proof], "properties": props})

    with api.app.state.store.transaction() as c:
        from backend import market as MK
        MK.create_entity(c, "human:ana", type="company", name="Our company", entity_id=MK.OWN_COMPANY_ID, evidence_ids=[proof])
    me = "company/self"
    rival, platform, supplier, channel, buyer = (company(n) for n in ("Rival Co", "Platform Co", "Supplier Co", "Channel Co", "Buyer Co"))
    edge(me, "competes_with", rival)
    edge(me, "integrates_with", platform)
    edge(rival, "integrates_with", supplier, role="supplier")
    edge(rival, "distributes_through", channel)
    edge(me, "sells_to", buyer)
    chain = get(api, "market/value-chain")
    layers = {layer["key"]: [n["id"] for n in layer["nodes"]] for layer in chain["layers"]}
    assert [layer["key"] for layer in chain["layers"]] == ["suppliers", "platforms", "rivals", "channels", "buyers"]
    assert layers["rivals"][0] == me and set(layers["rivals"]) == {me, rival}
    assert layers["platforms"] == [platform] and layers["suppliers"] == [supplier]
    assert layers["channels"] == [channel] and layers["buyers"] == [buyer]
    assert {"src": rival, "rel": "competes_with", "dst": me} in chain["edges"]
    for n in range(MS.LAYER_MAX + 5):
        edge(me, "sells_to", company(f"Buyer {n:03}"))
    assert len(next(l for l in get(api, "market/value-chain")["layers"] if l["key"] == "buyers")["nodes"]) == MS.LAYER_MAX


def test_the_value_chain_is_empty_before_the_graph_has_our_company(api):
    chain = get(api, "market/value-chain")
    assert all(layer["nodes"] == [] for layer in chain["layers"]) and chain["edges"] == []


def test_a_figure_is_dated_labelled_and_cited_and_shows_under_the_segment_players(api, tmp_path):
    _seed(api, tmp_path)
    first, _, _ = defined(api)
    proof = evidence(api)
    body = {"entity_id": "company/northwind", "segment_id": first, "metric": "arr", "value": 12_000_000, "kind": "claimed",
            "as_of": "2026-03"}
    post(api, "market/figures", body, expected=422)                                  # no evidence
    post(api, "market/figures", {**body, "evidence_id": proof, "as_of": "March"}, expected=422)
    post(api, "market/figures", {**body, "evidence_id": proof, "value": None}, expected=422)
    post(api, "market/figures", {**body, "evidence_id": proof, "entity_id": first}, expected=404)
    post(api, "market/figures", {**body, "evidence_id": proof})
    seen = next(s for s in get(api, "market/spend")["segments"] if s["id"] == first)
    assert [(p["entity_id"], p["figures"][0]["metric"], p["figures"][0]["kind"], p["figures"][0]["as_of"])
            for p in seen["players"]] == [("company/northwind", "arr", "claimed", "2026-03")]


def test_the_shipped_catalog_is_valid_and_a_team_source_adds_to_it_or_replaces_one(api):
    shipped = MS.shipped_sources()
    ids = [s["id"] for s in shipped]
    assert len(ids) == len(set(ids)) >= 30 and all(s["origin"] == "shipped" for s in shipped)
    listed = {s["id"]: s for s in get(api, "market/sources")["sources"]}
    assert set(ids) <= set(listed)
    mine = {"name": "Landlord forum", "kind": "forum", "covers": ["signals", "buyers"], "access": "free", "reliability": "signal"}
    post(api, "market/sources", {**mine, "covers": ["gossip"]}, expected=422)
    added = post(api, "market/sources", mine)["source"]
    assert added["id"] == "landlord-forum" and added["origin"] == "team"
    post(api, "market/sources", {**mine, "id": ids[0], "reliability": "estimate"})
    listed = {s["id"]: s for s in get(api, "market/sources")["sources"]}
    assert listed[ids[0]]["origin"] == "team" and listed[ids[0]]["reliability"] == "estimate" and "landlord-forum" in listed


def test_hub_sql_reads_the_spend_tables_like_the_other_market_tables(api):
    first, _, _ = defined(api)
    size(api, first, B, accept=True)
    for token in ("ana-test", "cara-test"):
        rows = query(api, "SELECT segment_id, status, spend_usd FROM market_sizes", token=token)["rows"]
        assert rows == [[first, "accepted", B]]
        for table in ("market_figures", "market_sources"):
            assert query(api, f"SELECT count(*) FROM {table}", token=token)["rows"] == [[0]]


def test_a_definition_keeps_what_is_not_sent_and_refuses_a_second_root(api):
    defined(api)
    put(api, "market/definition", {"includes": ["Screening", "Rent collection"], "excludes": ["Property sales"]})
    put(api, "market/definition", {"counting_rules": ["Count each unit once"]})
    saved = get(api, "market/spend")["definition"]
    assert saved["includes"] == ["Screening", "Rent collection"] and saved["counting_rules"] == ["Count each unit once"]
    assert saved["description"] == "Software for small landlords." and saved["params"]["adoption"]["judgement"] is True
    put(api, "market/definition", {"root_name": "Another market"}, expected=409)
    put(api, "market/definition", {"params": {"adoption": None}})
    assert "adoption" not in get(api, "market/spend")["definition"]["params"]
    put(api, "market/definition", {"params": {"bad name": 1}}, expected=422)
    put(api, "market/definition", {"unknown": 1}, expected=422)


def test_the_cli_and_mcp_tools_send_what_the_routes_take():
    """`--param name=number`, a flat evidence url and quote, and accept/reject/params all land in the route's body."""
    from clients import hubcli, hubtools

    class Api:
        def __init__(self):
            self.calls = []

        def post(self, path, body=None, key=None):
            self.calls.append(("POST", path, body))
            return {"size": {"id": "s1"}}

        def call(self, method, path, body=None, key=None, **more):
            self.calls.append((method, path, body))
            return {}

    fake = Api()
    hubtools.market_size_add(fake, {"segment_id": "segment/a", "method": "bottom_up", "low_usd": 1, "high_usd": 9,
                                   "formula": "n * p", "params": ["n=10", "p=0.5"], "evidence_url": "https://example.com/a",
                                   "quote": "ten"})
    method, path, body = fake.calls[-1]
    assert (method, path) == ("POST", "market/sizes") and body["params"] == {"n": 10.0, "p": 0.5}
    assert body["evidence"] == [{"source_url": "https://example.com/a", "source_kind": "other", "quote": "ten", "our_read": ""}]
    hubtools.market_size_decide(fake, {"id": "s1", "decision": "params", "params": ["p=0.7"]})
    assert fake.calls[-1][1:] == ("market/sizes/s1/params", {"params": {"p": 0.7}})
    hubtools.market_size_decide(fake, {"id": "s1", "decision": "reject"})
    assert fake.calls[-1][1] == "market/sizes/s1/reject"
    hubtools.market_define(fake, {"includes": ["a"], "params": ["adoption=0.1"], "year": 2026})
    assert fake.calls[-1] == ("PUT", "market/definition", {"includes": ["a"], "year": 2026, "params": {"adoption": 0.1}})
    with pytest.raises(Exception):
        hubtools.market_define(fake, {"params": ["adoption"]})
    parsed = hubcli.parser().parse_args(["market", "size", "accept", "s1"])
    assert (parsed.fn, parsed.decision, parsed.id) == ("market size decide", "accept", "s1")
