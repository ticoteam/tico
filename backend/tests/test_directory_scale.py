"""The scale gate's code path on a 2,000-entity fixture (bench/directory_scale.py), in the ordinary suite. It asserts the shape
of the report, that every read returned what the fixture says it should, and that the lookups seek an index (a query plan,
which does not depend on the machine). It asserts no timing: the §9.1 numbers are measured by the gate itself, at the size
the gate is run with, not by a test on a shared runner.
"""

from bench import directory_scale as DS

GATED = {
    "get_by_email",
    "get_by_domain",
    "get_by_external_id",
    "entity_page",
    "directory_list",
    "name_search",
    "lookup_plans",
    "longest_chunk_transaction",
}


def test_the_gate_runs_on_a_2k_fixture(tmp_path):
    report = DS.run(tmp_path, people=1500, orgs=500, runs=5)

    assert report["fixture"]["people"] == 1500 and report["fixture"]["orgs"] == 500
    assert report["fixture"]["entities"] == 2001  # the fixture's own entities, plus the company itself
    assert report["fixture"]["external_refs"] == 375
    assert {op["name"] for op in report["ops"]} == GATED
    for op in report["ops"]:
        assert op["status"] in {"pass", "miss"}, op  # a read that returned the wrong rows is "wrong"
        assert op["name"] in GATED
    assert {skip["name"] for skip in report["skipped"]} == set(DS.SKIPPED)
    assert all(reason for reason in DS.SKIPPED.values())
    assert set(report["informational"]["query_plans"]) == set(DS.PLAN_QUERIES)
    lookups = [op for op in report["ops"] if op["name"] == "lookup_plans"][0]
    assert lookups["status"] == "pass", lookups
